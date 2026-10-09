"""
Local text embeddings — AI-3.

Runs entirely on this machine. Clinic documents and the questions patients
ask of them are PHI-adjacent, and sending either to a hosted embedding API
would create a new data-processor relationship to justify in a BAA. A
384-dimension ONNX model is small enough that there is no reason to.

Model: BAAI/bge-small-en-v1.5 via fastembed. Quantised ONNX, so no torch —
about 130 MB on disk against roughly 2.5 GB for a torch install, which
matters on a Windows box already running SQL Server Express.

Two things this module is careful about:

  * **Loading is lazy and cached.** The model takes several seconds to
    initialise. Doing that at import time would add it to every process
    start including the test suite; doing it per request would add it to
    every question a caller asks. It loads once, on first use.

  * **Never raises.** A missing model or a failed download degrades
    retrieval to "no results", which the caller turns into "I don't have
    that detail, let me transfer you". That is a worse answer, never a
    failed call.

Queries and passages are embedded differently on purpose — see
`embed_query` for why that matters with BGE models.
"""

import logging
import os
import threading
from typing import Optional, Sequence

import numpy as np

from .config import settings

logger = logging.getLogger("embedding-service")

_model = None
_model_lock = threading.Lock()
_load_failed = False


def _cache_dir() -> str:
    if settings.embedding_cache_dir.strip():
        return settings.embedding_cache_dir.strip()
    # Beside the app rather than in the system temp directory, which
    # Windows clears — otherwise the model re-downloads after a reboot.
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), ".model_cache")


def _get_model():
    """
    The shared model instance, loaded on first use.

    Double-checked locking: several requests can arrive together on a
    cold process, and loading the model twice would double the memory
    for no benefit.
    """
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model

    with _model_lock:
        if _model is not None or _load_failed:
            return _model
        try:
            from fastembed import TextEmbedding

            cache = _cache_dir()
            os.makedirs(cache, exist_ok=True)
            logger.info("Loading embedding model %s", settings.embedding_model)
            _model = TextEmbedding(
                model_name=settings.embedding_model, cache_dir=cache
            )
            logger.info("Embedding model ready")
        except Exception:
            # Most likely: fastembed not installed, or no network on a
            # cold cache. Retrieval degrades to empty rather than 500.
            _load_failed = True
            logger.exception(
                "Could not load the embedding model — knowledge-base retrieval "
                "will return no results until this is fixed"
            )
    return _model


def is_available() -> bool:
    """Whether embeddings can be produced at all."""
    return _get_model() is not None


def embed_texts(texts: Sequence[str]) -> Optional[np.ndarray]:
    """
    Embeds document passages. Returns an (n, dim) float32 array, or None.

    Used at indexing time. Passages are embedded as-is; only the *query*
    side gets a prefix (see embed_query).
    """
    if not texts:
        return None
    model = _get_model()
    if model is None:
        return None
    try:
        vectors = list(model.embed(list(texts)))
        return np.asarray(vectors, dtype=np.float32)
    except Exception:
        logger.exception("Embedding failed for %d texts", len(texts))
        return None


def embed_query(text: str) -> Optional[np.ndarray]:
    """
    Embeds a question. Returns a (dim,) float32 vector, or None.

    Deliberately separate from `embed_texts`: BGE models are trained
    asymmetrically, with queries carrying a retrieval instruction prefix
    that passages do not. fastembed's `query_embed` applies it. Embedding
    a question as though it were a passage measurably degrades recall —
    the two are not interchangeable even though both return vectors.
    """
    if not text or not text.strip():
        return None
    model = _get_model()
    if model is None:
        return None
    try:
        vectors = list(model.query_embed([text]))
        if not vectors:
            return None
        return np.asarray(vectors[0], dtype=np.float32)
    except Exception:
        logger.exception("Query embedding failed")
        return None


def to_bytes(vector: np.ndarray) -> bytes:
    """Serialise for the VARBINARY column. Plain little-endian float32."""
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_bytes(blob: Optional[bytes]) -> Optional[np.ndarray]:
    """
    Deserialise a stored vector.

    Returns None for anything the wrong size rather than reshaping
    whatever it finds — a truncated row, or one written by a different
    model, would otherwise produce a silently meaningless similarity
    score instead of being skipped.
    """
    if not blob:
        return None
    expected = settings.embedding_dim * 4
    if len(blob) != expected:
        logger.warning(
            "Stored embedding is %d bytes, expected %d — skipping it. "
            "Re-index if the embedding model changed.",
            len(blob), expected,
        )
        return None
    return np.frombuffer(blob, dtype=np.float32)


def cosine_similarity(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """
    Cosine similarity of one query against every row of `matrix`.

    Brute force, in numpy, over all of one org's chunks. At clinic scale
    — hundreds to low thousands of chunks — this is sub-millisecond and
    beats a round trip to a vector database, which is why AI-3 introduces
    no new datastore. If a single org ever exceeds ~100k chunks this is
    the line to revisit, not before.
    """
    if matrix.size == 0:
        return np.zeros(0, dtype=np.float32)
    query_norm = np.linalg.norm(query)
    row_norms = np.linalg.norm(matrix, axis=1)
    # Guard against a zero vector, which would divide by zero and yield
    # nan — nan compares false against the threshold, so it would fail
    # closed anyway, but a warning-free 0.0 is clearer.
    denom = query_norm * row_norms
    denom[denom == 0] = 1e-12
    return (matrix @ query) / denom
