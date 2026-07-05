# backend/lambdas/chat/guest.py
# Public, unauthenticated guest chat over pre-seeded workspaces.
# Stateless by design: no sessions, no message persistence, no DynamoDB writes.

import json
from typing import Dict

from shared.config import config
from shared.vector_store import vector_store
from shared.cache import cache
from shared.utils import create_success_response, create_error_response

# Only these namespaces are reachable without auth. Each maps to a
# Pinecone namespace seeded offline via scripts/seed_public_workspace.py.
PUBLIC_WORKSPACES = {
    "demo": "public-demo",           # aRAG site "try as guest"
    "portfolio": "public-portfolio", # folio chat widget
}

GUEST_RATE_LIMIT = 20          # requests per IP per window
GUEST_RATE_WINDOW = 3600       # seconds
GUEST_MAX_MESSAGE_CHARS = 500
GUEST_TOP_K = 8
GUEST_CONTEXT_CHAR_BUDGET = 9000
GUEST_MODEL = "gemini-flash-latest"  # alias tracks current stable Flash; routed by prefix


def _client_ip(event: Dict) -> str:
    ctx = event.get("requestContext", {}) or {}
    identity = ctx.get("identity", {}) or {}
    ip = identity.get("sourceIp")
    if not ip:
        # X-Forwarded-For: client, proxy1, proxy2
        xff = (event.get("headers") or {}).get("X-Forwarded-For", "")
        ip = xff.split(",")[0].strip() if xff else "unknown"
    return ip


def guest_chat_handler(event: Dict, context) -> Dict:
    """POST /guest/chat  body: {"message": str, "workspace": "demo"|"portfolio"}"""
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return create_error_response(400, "Invalid JSON body")

    message = (body.get("message") or "").strip()
    workspace = (body.get("workspace") or "demo").strip().lower()

    if workspace not in PUBLIC_WORKSPACES:
        return create_error_response(400, f"Unknown workspace. Valid: {sorted(PUBLIC_WORKSPACES)}")
    if not message:
        return create_error_response(400, "Message is required")
    if len(message) > GUEST_MAX_MESSAGE_CHARS:
        return create_error_response(400, f"Message too long (max {GUEST_MAX_MESSAGE_CHARS} chars)")

    ip = _client_ip(event)
    if not cache.check_rate_limit(f"guest-{ip}", "guest_chat", GUEST_RATE_LIMIT, GUEST_RATE_WINDOW):
        return create_error_response(429, "Rate limit reached. Try again in a bit.")

    namespace = PUBLIC_WORKSPACES[workspace]

    try:
        query_embedding = vector_store.generate_embeddings([message])[0]
    except Exception as e:
        print(f"Guest embed failed: {e}")
        return create_error_response(502, "Embedding service unavailable")

    try:
        contexts = vector_store.search_namespace(namespace, query_embedding, top_k=GUEST_TOP_K)
    except Exception as e:
        print(f"Guest search failed: {e}")
        return create_error_response(502, "Search unavailable")

    # Import here to avoid circulars (handler imports guest for routing).
    from chat.handler import call_gemini

    # Own context builder: the legacy prompt builder caps each document at
    # 1000 chars, which starves single-document guest workspaces. Guests get
    # full retrieved chunks up to a generous overall budget instead.
    context_parts = []
    budget = GUEST_CONTEXT_CHAR_BUDGET
    for c in contexts:
        chunk = (c.get("chunk_text") or "").strip()
        if not chunk:
            continue
        label = c.get("filename", "document")
        if c.get("page_number"):
            label += f", page {c['page_number']}"
        block = f"[{label}]\n{chunk}"
        if len(block) > budget:
            break
        context_parts.append(block)
        budget -= len(block)

    prompt = f"""You are the assistant on aRAG, a retrieval-augmented generation system. Answer the visitor's question using only the document excerpts below.

Document excerpts:
{chr(10).join(context_parts) if context_parts else "(no relevant excerpts found)"}

Question: {message}

Instructions:
- Ground every claim in the excerpts; if they don't contain the answer, say so plainly
- Cite page numbers when the excerpt labels include them
- Be concise and direct"""

    try:
        response_text = call_gemini(prompt, GUEST_MODEL)
    except Exception as e:
        print(f"Guest LLM failed: {e}")
        return create_error_response(502, "Generation unavailable")

    # Source list: unique filenames with the pages that contributed
    by_file = {}
    for c in contexts:
        name = c.get("filename")
        if not name:
            continue
        entry = by_file.setdefault(name, {"filename": name, "pages": set(),
                                          "relevance_score": 0.0})
        entry["relevance_score"] = max(entry["relevance_score"], float(c.get("score", 0)))
        if c.get("page_number"):
            entry["pages"].add(int(c["page_number"]))

    sources = [
        {
            "filename": e["filename"],
            "pages": sorted(e["pages"]),
            "relevance_score": round(e["relevance_score"], 3),
        }
        for e in by_file.values()
    ]

    return create_success_response({
        "answer": response_text,
        "sources": sources,
        "workspace": workspace,
    })
