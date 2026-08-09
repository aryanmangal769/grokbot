"""Local semantic embeddings — no external API of any kind.

xAI/Grok has no embeddings endpoint (checked), so cosine-similarity search
needs a vector source. This runs a small sentence-transformer model entirely
on your machine — the only "new" thing pulled in is a local Python library,
not a third-party API — so the only network calls anywhere in this project
remain Polymarket (Gamma/CLOB/Data) and xAI (Grok).

Model: all-MiniLM-L6-v2 (384-dim, ~90MB, downloaded once by the library on
first use and cached locally). Small and fast enough for 100s-1000s of short
market titles/descriptions.
"""
from __future__ import annotations

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def embed(text: str) -> list[float]:
    return _get_model().encode(text, normalize_embeddings=True).tolist()


def embed_batch(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    vecs = _get_model().encode(texts, batch_size=batch_size, normalize_embeddings=True,
                               show_progress_bar=False)
    return [v.tolist() for v in vecs]


DIM = 384  # all-MiniLM-L6-v2 output dimension — must match db.py's vector(384) column


def embed_text_for(doc) -> str:
    """The single source of truth for what text a ContractDocument gets
    embedded from — used by both ingest_bulk.py and the single-contract
    CLI's --save-db path, so nothing gets saved without a real embedding."""
    parts = [doc.title or "", doc.summary or "", doc.resolution_summary or ""]
    return " — ".join(p for p in parts if p)
