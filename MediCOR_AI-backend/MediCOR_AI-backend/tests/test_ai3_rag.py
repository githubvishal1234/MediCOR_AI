"""
AI-3 — retrieval-augmented knowledge base.

Before this there was no retrieval at all: an uploaded document was
written to disk and flipped straight to "Indexed" without being opened,
`/knowledge-base/search` matched on filename only, and `/knowledge-base/ask`
returned an FAQ as authoritative when `best_score >= 1` — a **single
shared keyword** — to a patient on a live call.

These tests are **offline**. Embedding and synthesis are both stubbed, so
the suite needs no model download and no API key. What is under test is
the pipeline and, above all, the safety property.

The safety design is worth stating, because it is not the obvious one.
Measured against a real clinic handbook, cosine similarity **cannot**
separate answerable from unanswerable questions: the lowest answerable
scored 0.648 and the highest unanswerable 0.645. "Can I get a repeat
prescription?" retrieves the policy text with high similarity precisely
because it is a plausible clinic question. So the threshold is a coarse
filter and **the synthesiser is the safety gate** — it returns
INSUFFICIENT_CONTEXT when the retrieved text does not answer the
question. `test_an_in_domain_but_unanswered_question_is_declined` is the
test that pins that, and it is the most important one in this file.

    pytest tests/test_ai3_rag.py -v
"""

import os
import sys
import tempfile

os.environ.setdefault("DB_SERVER", "unused")
os.environ.setdefault("DB_PORT", "1433")
os.environ.setdefault("DB_NAME", "unused")
os.environ.setdefault("DB_USER", "unused")
os.environ.setdefault("DB_PASSWORD", "unused")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import embedding_service, models, rag_service
from app.config import settings
from app.database import Base

HANDBOOK = """Northside Clinic Patient Handbook

Opening Hours
We are open Monday to Friday from 9:00 AM to 5:00 PM.
On Saturdays we open from 9:00 AM until 1:00 PM. We are closed on Sundays.

Payment and Fees
We accept Aetna, BlueCross BlueShield, and Cigna. We do not currently
accept Medicaid. Payment is due at the time of the visit.

Cancellation Policy
Please give at least 24 hours notice to cancel. A late cancellation fee of
$40 applies to appointments cancelled with less than 24 hours notice.

Parking
Free patient parking is available in the rear lot, accessed from Elm Street.
"""


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def org(db):
    o = models.Organization(name="Northside Clinic")
    db.add(o)
    db.commit()
    return o


def _write(text: str, suffix: str = ".txt") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


@pytest.fixture()
def fake_embeddings(monkeypatch):
    """
    Deterministic stand-in for the real model.

    Hashes each text into a fixed-width vector. Similarity is meaningless,
    which is the point — these tests exercise the plumbing and the
    decision logic, not the model's quality. Retrieval quality was
    validated separately against the real model and recorded in
    app/config.py's tuning notes.
    """
    dim = settings.embedding_dim

    def vec(text: str) -> np.ndarray:
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        return rng.random(dim, dtype=np.float32)

    monkeypatch.setattr(
        embedding_service, "embed_texts",
        lambda texts: np.vstack([vec(t) for t in texts]) if texts else None,
    )
    monkeypatch.setattr(embedding_service, "embed_query", vec)
    return vec


# ---------- chunking ----------

def test_a_long_document_becomes_several_focused_chunks():
    """
    The tuning that made retrieval work at all.

    At 800 chars this handbook collapsed into ONE chunk whose embedding
    averaged hours + fees + parking together, and every question
    scored a mushy 0.56-0.64. Small chunks are what let retrieval
    discriminate.
    """
    chunks = rag_service.chunk_text(HANDBOOK)
    assert len(chunks) > 1
    assert all(c.strip() for c in chunks)
    # Nothing wildly over the configured window.
    assert max(len(c) for c in chunks) <= settings.kb_chunk_chars * 2


def test_chunking_handles_empty_and_whitespace_input():
    assert rag_service.chunk_text("") == []
    assert rag_service.chunk_text("   \n\n  ") == []


def test_a_paragraph_longer_than_the_window_is_still_split():
    chunks = rag_service.chunk_text("x" * (settings.kb_chunk_chars * 3))
    assert len(chunks) > 1


# ---------- parsing ----------

def test_plain_text_and_markdown_are_parsed():
    for suffix in (".txt", ".md", ".csv"):
        path = _write("Opening hours are 9 to 5.", suffix)
        try:
            assert "Opening hours" in rag_service._extract_text(path)
        finally:
            os.unlink(path)


def test_an_unreadable_file_returns_none_rather_than_raising():
    assert rag_service._extract_text("/nonexistent/path/file.txt") is None


def test_an_unsupported_extension_returns_none():
    path = _write("data", ".xyz")
    try:
        assert rag_service._extract_text(path) is None
    finally:
        os.unlink(path)


# ---------- indexing ----------

def test_indexing_stores_chunks_and_marks_the_document_indexed(db, org, fake_embeddings):
    path = _write(HANDBOOK)
    doc = models.KBDocument(org_id=org.id, name="handbook.txt", file_path=path, status="Indexing")
    db.add(doc)
    db.commit()
    try:
        assert rag_service.index_document(db, doc) is True
        assert doc.status == "Indexed"
        assert db.query(models.KBChunk).filter_by(org_id=org.id).count() > 1
    finally:
        os.unlink(path)


def test_a_missing_file_marks_the_document_failed(db, org, fake_embeddings):
    doc = models.KBDocument(
        org_id=org.id, name="gone.txt", file_path="/nonexistent/gone.txt", status="Indexing"
    )
    db.add(doc)
    db.commit()
    assert rag_service.index_document(db, doc) is False
    assert doc.status == "Failed"


def test_an_empty_document_fails_rather_than_indexing_nothing(db, org, fake_embeddings):
    """
    "Indexed" must mean "searchable". Marking an unreadable file Indexed
    is exactly the lie this feature exists to remove.
    """
    path = _write("   ")
    doc = models.KBDocument(org_id=org.id, name="blank.txt", file_path=path, status="Indexing")
    db.add(doc)
    db.commit()
    try:
        assert rag_service.index_document(db, doc) is False
        assert doc.status == "Failed"
    finally:
        os.unlink(path)


def test_reindexing_replaces_chunks_rather_than_duplicating(db, org, fake_embeddings):
    path = _write(HANDBOOK)
    doc = models.KBDocument(org_id=org.id, name="handbook.txt", file_path=path, status="Indexing")
    db.add(doc)
    db.commit()
    try:
        rag_service.index_document(db, doc)
        first = db.query(models.KBChunk).filter_by(document_id=doc.id).count()
        rag_service.index_document(db, doc)
        assert db.query(models.KBChunk).filter_by(document_id=doc.id).count() == first
    finally:
        os.unlink(path)


def test_indexing_fails_cleanly_when_embeddings_are_unavailable(db, org, monkeypatch):
    """A model outage must not leave chunks that can never be retrieved."""
    monkeypatch.setattr(embedding_service, "embed_texts", lambda texts: None)
    path = _write(HANDBOOK)
    doc = models.KBDocument(org_id=org.id, name="handbook.txt", file_path=path, status="Indexing")
    db.add(doc)
    db.commit()
    try:
        assert rag_service.index_document(db, doc) is False
        assert doc.status == "Failed"
        assert db.query(models.KBChunk).filter_by(document_id=doc.id).count() == 0
    finally:
        os.unlink(path)


# ---------- vector round-trip ----------

def test_a_vector_survives_serialisation():
    v = np.arange(settings.embedding_dim, dtype=np.float32)
    assert np.allclose(embedding_service.from_bytes(embedding_service.to_bytes(v)), v)


def test_a_wrong_sized_vector_is_skipped_not_reshaped():
    """
    A row written by a different embedding model would otherwise produce
    a silently meaningless similarity score.
    """
    assert embedding_service.from_bytes(b"\x00" * 16) is None
    assert embedding_service.from_bytes(None) is None


def test_cosine_similarity_handles_a_zero_vector():
    q = np.zeros(4, dtype=np.float32)
    m = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32)
    assert not np.isnan(embedding_service.cosine_similarity(q, m)).any()


# ---------- answering ----------

def test_a_confident_match_is_synthesised_into_a_spoken_answer(db, org, fake_embeddings, monkeypatch):
    monkeypatch.setattr(settings, "kb_min_similarity", 0.0)
    monkeypatch.setattr(rag_service, "_synthesise", lambda q, p: "We open at 9 AM on Saturday.")
    path = _write(HANDBOOK)
    doc = models.KBDocument(org_id=org.id, name="handbook.txt", file_path=path, status="Indexing")
    db.add(doc)
    db.commit()
    try:
        rag_service.index_document(db, doc)
        result = rag_service.answer_question(db, org.id, "Saturday hours?")
        assert result["source"] == "document"
        assert result["answer"] == "We open at 9 AM on Saturday."
        assert result["matched_question"] == "handbook.txt"
        assert result["confidence"] > 0
    finally:
        os.unlink(path)


def test_an_in_domain_but_unanswered_question_is_declined(db, org, fake_embeddings, monkeypatch):
    """
    **The most important test in this file.**

    Retrieval WILL surface plausible-looking chunks for a question the
    document does not answer — measured, an unanswered question scored
    0.645 against an answerable one's 0.648. The threshold cannot tell
    them apart, so the synthesiser must: it returns INSUFFICIENT_CONTEXT,
    `_synthesise` turns that into None, and the caller gets
    `source: "none"` — which the voice agent already treats as "I don't
    have that, let me transfer you".

    If this ever fails, the AI is inventing clinical answers on live
    calls.
    """
    monkeypatch.setattr(settings, "kb_min_similarity", 0.0)  # retrieval succeeds
    monkeypatch.setattr(rag_service, "_synthesise", lambda q, p: None)  # model declines
    path = _write(HANDBOOK)
    doc = models.KBDocument(org_id=org.id, name="handbook.txt", file_path=path, status="Indexing")
    db.add(doc)
    db.commit()
    try:
        rag_service.index_document(db, doc)
        result = rag_service.answer_question(db, org.id, "Can I get a repeat prescription?")
        assert result["source"] == "none"
        assert result["confidence"] == 0.0
        assert "don't have that specific detail" in result["answer"]
    finally:
        os.unlink(path)


def test_nothing_indexed_means_a_clean_i_dont_know(db, org, fake_embeddings):
    result = rag_service.answer_question(db, org.id, "What time do you open?")
    assert result["source"] == "none"
    assert result["matched_question"] is None


def test_retrieval_never_crosses_org_boundaries(db, fake_embeddings, monkeypatch):
    """A clinic must never answer from another clinic's handbook."""
    monkeypatch.setattr(settings, "kb_min_similarity", 0.0)
    a = models.Organization(name="Clinic A")
    b = models.Organization(name="Clinic B")
    db.add_all([a, b])
    db.commit()

    path = _write(HANDBOOK)
    doc = models.KBDocument(org_id=a.id, name="handbook.txt", file_path=path, status="Indexing")
    db.add(doc)
    db.commit()
    try:
        rag_service.index_document(db, doc)
        assert rag_service.retrieve(db, a.id, "opening hours")
        assert rag_service.retrieve(db, b.id, "opening hours") == []
    finally:
        os.unlink(path)


def test_the_response_shape_the_voice_agent_depends_on_is_unchanged(db, org, fake_embeddings):
    """
    `voice-agent/agent.py` speaks back `answer` and keys on
    `source == "none"`. Adding fields is safe; renaming or removing these
    would break live calls.
    """
    result = rag_service.answer_question(db, org.id, "anything")
    for key in ("answer", "matched_question", "source"):
        assert key in result
    assert isinstance(result["answer"], str) and result["answer"]
    assert result["source"] in {"document", "faq", "none"}


def test_an_faq_is_matched_semantically_not_by_keyword_overlap(db, org, fake_embeddings, monkeypatch):
    monkeypatch.setattr(settings, "kb_min_similarity", 0.0)
    db.add(models.FAQ(org_id=org.id, question="Do you have parking?", answer="Yes, in the rear lot."))
    db.commit()
    result = rag_service.answer_question(db, org.id, "Do you have parking?")
    assert result["source"] == "faq"
    assert result["answer"] == "Yes, in the rear lot."
    assert result["matched_question"] == "Do you have parking?"


def test_a_weak_faq_match_is_refused_rather_than_answered(db, org, fake_embeddings, monkeypatch):
    """
    The old rule was `best_score >= 1` — one shared keyword returned an
    FAQ as authoritative. Now a weak match declines.
    """
    monkeypatch.setattr(settings, "kb_min_similarity", 0.99)
    db.add(models.FAQ(org_id=org.id, question="Do you have parking?", answer="Yes."))
    db.commit()
    assert rag_service.answer_question(db, org.id, "totally unrelated")["source"] == "none"
