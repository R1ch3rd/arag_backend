from typing import List
from config import config
import requests

def generate_embeddings(texts: List[str]) -> List[List[float]]:
    """
    Generate embeddings using Google Gemini Embedding API
    """
    if not texts:
        return []
    
    print(f"🔄 Generating embeddings for {len(texts)} texts")

    if not config.GEMINI_API_KEY:
        raise Exception("GEMINI_API_KEY not configured")

    # MUST be exactly this model
    model = "models/text-embedding-004"

    # correct endpoint
    url = f"https://generativelanguage.googleapis.com/v1beta/{model}:embedContent"

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": config.GEMINI_API_KEY,
    }

    embeddings = []

    try:
        for text in texts:
            payload = {
                "model": model,
                "content": {
                    "parts": [
                        {"text": text}
                    ]
                }
            }

            response = requests.post(url, headers=headers, json=payload, timeout=30)

            print("Status:", response.status_code)
            print(response.text)

            if response.status_code != 200:
                raise Exception(f"API error {response.status_code}: {response.text}")

            data = response.json()

            vector = data["embedding"]["values"]
            embeddings.append(vector)

        print(f"✅ Generated {len(embeddings)} embeddings")
        return embeddings

    except Exception as e:
        print(f"❌ Batch embedding failed: {str(e)}")
        raise


# Run test
generate_embeddings(["hello world"])
