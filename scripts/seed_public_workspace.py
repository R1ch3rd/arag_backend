#!/usr/bin/env python
"""
Seed a public guest workspace (Pinecone namespace) from a folder of documents.

Runs locally with the same shared modules the Lambdas use. Guests can then
query these documents through POST /guest/chat without authentication.

Usage:
    # env: GEMINI_API_KEY, PINECONE_API_KEY (PINECONE_INDEX optional)
    python scripts/seed_public_workspace.py demo ./seed-docs/demo
    python scripts/seed_public_workspace.py portfolio ./seed-docs/portfolio

Workspaces map to namespaces: demo -> public-demo, portfolio -> public-portfolio.
Re-running replaces the namespace content (delete + re-upsert).
"""

import sys
import uuid
from pathlib import Path

# Make lambdas/ importable so we reuse the exact production code paths
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lambdas"))

from shared.vector_store import vector_store          # noqa: E402
from shared.utils import extract_text_with_pages, chunk_pages  # noqa: E402

WORKSPACES = {"demo": "public-demo", "portfolio": "public-portfolio"}
SUPPORTED = {".pdf", ".txt", ".md", ".docx"}


def seed(workspace: str, folder: Path) -> None:
    namespace = WORKSPACES[workspace]
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in SUPPORTED)
    if not files:
        sys.exit(f"No seedable files ({', '.join(sorted(SUPPORTED))}) in {folder}")

    print(f"Seeding namespace '{namespace}' from {folder} ({len(files)} files)")

    # Clear previous content so re-seeding is idempotent
    try:
        vector_store.index.delete(delete_all=True, namespace=namespace)
        print(f"Cleared existing vectors in '{namespace}'")
    except Exception as e:
        print(f"(namespace may not exist yet: {e})")

    for path in files:
        print(f"\n--- {path.name} ---")
        content = path.read_bytes()
        pages = extract_text_with_pages(content, path.name)
        chunks = chunk_pages(pages, chunk_size=400, overlap=100)
        if not chunks:
            print("  no chunks, skipping")
            continue

        texts = [c["text"] for c in chunks]
        embeddings = vector_store.generate_embeddings(texts)

        document_id = f"seed-{uuid.uuid4().hex[:12]}"
        vector_store.upsert_chunks(
            user_id=namespace,
            document_id=document_id,
            chunks=chunks,
            embeddings=embeddings,
            document_metadata={"filename": path.name},
        )
        print(f"  upserted {len(chunks)} chunks as {document_id}")

    print(f"\nDone. Namespace '{namespace}' is live for guest chat.")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in WORKSPACES:
        sys.exit(__doc__)
    folder = Path(sys.argv[2])
    if not folder.is_dir():
        sys.exit(f"Not a directory: {folder}")
    seed(sys.argv[1], folder)
