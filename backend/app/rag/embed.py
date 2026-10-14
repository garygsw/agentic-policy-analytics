"""Local embedding and reranking models (fastembed / ONNX, no GPU, no API key). Downloaded once into var/models.

The same embedding model must be used to build the index and to search it; the index records the model's name
and refuses to run with a different one.
"""
from functools import lru_cache
from typing import Protocol

from app.config import REPO_ROOT

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"   # 384 dimensions, 67 MB, MIT licence
EMBEDDING_DIM = 384
RERANKER_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"  # cross-encoder, 80 MB, Apache-2.0
MODELS_DIR = REPO_ROOT / "var" / "models"


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class Reranker(Protocol):
    def rerank(self, query: str, texts: list[str]) -> list[float]: ...


class LocalEmbedder:
    name, dim = EMBEDDING_MODEL, EMBEDDING_DIM

    def __init__(self):
        from fastembed import TextEmbedding
        self.model = TextEmbedding(EMBEDDING_MODEL, cache_dir=str(MODELS_DIR))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self.model.embed(texts, batch_size=32)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self.model.query_embed(text))).tolist()  # adds the model's query instruction


class LocalReranker:
    def __init__(self):
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        self.model = TextCrossEncoder(RERANKER_MODEL, cache_dir=str(MODELS_DIR))

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        return [float(score) for score in self.model.rerank(query, texts)]


@lru_cache(maxsize=1)
def get_embedder() -> LocalEmbedder:
    return LocalEmbedder()


@lru_cache(maxsize=1)
def get_reranker() -> LocalReranker:
    return LocalReranker()
