"""
Qdrant vector store client.
Used by the RAG retrieval node to find relevant TechnoBrain/Salesmate knowledge.
"""
from typing import Optional
from qdrant_client import QdrantClient as _QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter,
    FieldCondition, MatchValue, FilterSelector
)
import hashlib, uuid
import config


class VectorStore:
    def __init__(self):
        self._client: Optional[_QdrantClient] = None

    def client(self) -> _QdrantClient:
        if not self._client:
            self._client = _QdrantClient(
                host=config.QDRANT_HOST,
                port=config.QDRANT_PORT,
            )
        return self._client

    def delete_by_url(self, url: str) -> None:
        """Delete all vectors whose payload.url matches the given URL."""
        self.client().delete(
            collection_name=config.QDRANT_COLLECTION,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="url", match=MatchValue(value=url))]
                )
            ),
        )

    def delete_collection(self) -> None:
        """Drop and recreate the collection (used before a full re-ingest)."""
        c = self.client()
        existing = [col.name for col in c.get_collections().collections]
        if config.QDRANT_COLLECTION in existing:
            c.delete_collection(config.QDRANT_COLLECTION)
            print(f"[Qdrant] Dropped collection '{config.QDRANT_COLLECTION}'")

    def ensure_collection(self) -> None:
        """Create the collection if it doesn't exist."""
        c = self.client()
        existing = [col.name for col in c.get_collections().collections]
        if config.QDRANT_COLLECTION not in existing:
            c.create_collection(
                collection_name=config.QDRANT_COLLECTION,
                vectors_config=VectorParams(
                    size=config.EMBED_DIM,
                    distance=Distance.COSINE,
                ),
            )
            print(f"[Qdrant] Created collection '{config.QDRANT_COLLECTION}'")

    def upsert(self, texts: list[str], metadatas: list[dict]) -> None:
        """Embed texts and upsert into Qdrant using deterministic IDs to prevent duplicates."""
        vectors = embed_texts(texts)
        points = [
            PointStruct(
                # SHA-256 of the text → UUID for a stable, content-addressed ID
                id=str(uuid.UUID(hashlib.sha256(text.encode()).hexdigest()[:32])),
                vector=vec,
                payload={**meta, "text": text},
            )
            for text, vec, meta in zip(texts, vectors, metadatas)
        ]
        self.client().upsert(
            collection_name=config.QDRANT_COLLECTION,
            points=points,
        )

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """
        Semantic search. Returns list of {text, score, source, object_type}.
        """
        query_vec = embed_texts([query])[0]
        results = self.client().search(
            collection_name=config.QDRANT_COLLECTION,
            query_vector=query_vec,
            limit=top_k,
        )
        return [
            {
                "text":        hit.payload.get("text", ""),
                "score":       hit.score,
                "source":      hit.payload.get("source", ""),
                "object_type": hit.payload.get("object_type", ""),
            }
            for hit in results
        ]

    def count(self) -> int:
        info = self.client().get_collection(config.QDRANT_COLLECTION)
        return info.points_count


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed text using nomic-embed-text via Ollama.
    Falls back to sentence-transformers if Ollama is unavailable.
    """
    try:
        import ollama as _ollama
        result = []
        for text in texts:
            resp = _ollama.embeddings(
                model=config.EMBED_MODEL,
                prompt=text,
            )
            result.append(resp["embedding"])
        return result
    except Exception as e:
        print(f"[Embedder] Ollama failed ({e}), falling back to sentence-transformers")
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("nomic-ai/nomic-embed-text-v1", trust_remote_code=True)
        return model.encode(texts).tolist()


# Module-level singleton
vector_store = VectorStore()
