"""The benchmark knowledge base and its questions.

A deliberately small, hand-written corpus for a customer-support assistant.
Small is the point: every document and every expected answer is auditable, so
when the benchmark reports 90% root-cause accuracy anyone can read the fixture
and check what the other 10% were.

The corpus contains superseded documents alongside their replacements. That is
what makes the stale-retrieval scenario realistic rather than synthetic: the
retriever picks a real document from the real index, and nothing downstream
can tell it is the wrong one except its metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The clock the benchmark runs against. Document validity is judged against
#: this, so results do not change as real time passes.
BENCHMARK_NOW = "2026-06-01T09:00:00+00:00"


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    text: str
    effective_date: str
    status: str = "current"
    valid_until: str | None = None
    superseded_by: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """The shape the SDK records and the detectors read."""
        payload: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "effective_date": self.effective_date,
            "status": self.status,
        }
        if self.valid_until:
            payload["valid_until"] = self.valid_until
        if self.superseded_by:
            payload["superseded_by"] = self.superseded_by
        return payload


DOCUMENTS: list[Document] = [
    Document(
        id="refund-2026",
        title="Refund policy (current)",
        text=(
            "Customers may return any item within 30 days of delivery for a full refund. "
            "Refunds are issued to the original payment method within 5 business days."
        ),
        effective_date="2026-01-01",
    ),
    Document(
        id="refund-2019",
        title="Refund policy (superseded)",
        text=(
            "Customers may return any item within 90 days of delivery for a full refund. "
            "Refunds are issued to the original payment method within 21 business days."
        ),
        effective_date="2019-03-01",
        status="outdated",
        valid_until="2025-12-31",
        superseded_by="refund-2026",
    ),
    Document(
        id="shipping-2026",
        title="Shipping times",
        text=(
            "Standard shipping arrives in 3 to 5 business days. "
            "Express shipping arrives in 1 business day and costs 12 USD."
        ),
        effective_date="2026-02-10",
    ),
    Document(
        id="warranty-2026",
        title="Warranty coverage",
        text=(
            "Every device carries a 24 month manufacturer warranty from the delivery date. "
            "The warranty covers manufacturing defects but not accidental damage."
        ),
        effective_date="2026-01-15",
    ),
    Document(
        id="warranty-2021",
        title="Warranty coverage (superseded)",
        text=(
            "Every device carries a 12 month manufacturer warranty from the delivery date. "
            "The warranty covers manufacturing defects but not accidental damage."
        ),
        effective_date="2021-06-01",
        status="outdated",
        valid_until="2025-12-31",
        superseded_by="warranty-2026",
    ),
    Document(
        id="support-hours-2026",
        title="Support hours",
        text=(
            "Live support is available from 8 to 20 on weekdays. "
            "Weekend support is email only with a 24 hour response target."
        ),
        effective_date="2026-03-01",
    ),
    Document(
        id="account-2026",
        title="Account deletion",
        text=(
            "An account deletion request is completed within 14 days. "
            "Deleted accounts cannot be restored after 30 days."
        ),
        effective_date="2026-04-01",
    ),
    Document(
        id="loyalty-2026",
        title="Loyalty points",
        text=(
            "Loyalty points expire 18 months after they are earned. "
            "Members earn 2 points for every 1 USD spent."
        ),
        effective_date="2026-05-01",
    ),
]

DOCUMENTS_BY_ID = {document.id: document for document in DOCUMENTS}


@dataclass(frozen=True)
class Question:
    """One benchmark question with the document that answers it."""

    id: str
    text: str
    expected_document_id: str
    expected_answer: str
    #: The superseded document a stale retriever would pick instead, when one
    #: exists. Questions without a superseded counterpart use a wrong-topic
    #: document for that scenario instead.
    stale_document_id: str | None = None
    #: A plausible but unsupported answer, used by the hallucination scenario.
    hallucinated_answer: str = ""
    tool: dict[str, Any] = field(default_factory=dict)


QUESTIONS: list[Question] = [
    Question(
        id="q-refund-window",
        text="How many days do customers have to return an item for a refund?",
        expected_document_id="refund-2026",
        expected_answer=(
            "Customers may return any item within 30 days of delivery for a full refund."
        ),
        stale_document_id="refund-2019",
        hallucinated_answer="Customers may return any item within 14 days of delivery.",
    ),
    Question(
        id="q-refund-speed",
        text="How long does a refund take to reach the original payment method?",
        expected_document_id="refund-2026",
        expected_answer="Refunds are issued to the original payment method within 5 business days.",
        stale_document_id="refund-2019",
        hallucinated_answer="Refunds are issued within 45 business days.",
    ),
    Question(
        id="q-warranty-length",
        text="How many months of manufacturer warranty does a device carry?",
        expected_document_id="warranty-2026",
        expected_answer=(
            "Every device carries a 24 month manufacturer warranty from the delivery date."
        ),
        stale_document_id="warranty-2021",
        hallucinated_answer="Every device carries a 36 month manufacturer warranty.",
    ),
    Question(
        id="q-shipping-standard",
        text="How many business days does standard shipping take to arrive?",
        expected_document_id="shipping-2026",
        expected_answer="Standard shipping arrives in 3 to 5 business days.",
        hallucinated_answer="Standard shipping arrives in 9 business days.",
    ),
    Question(
        id="q-support-hours",
        text="What hours is live support available on weekdays?",
        expected_document_id="support-hours-2026",
        expected_answer="Live support is available from 8 to 20 on weekdays.",
        hallucinated_answer="Live support is available from 6 to 23 on weekdays.",
    ),
    Question(
        id="q-account-deletion",
        text="How many days does an account deletion request take to complete?",
        expected_document_id="account-2026",
        expected_answer="An account deletion request is completed within 14 days.",
        hallucinated_answer="An account deletion request is completed within 60 days.",
    ),
    Question(
        id="q-loyalty-expiry",
        text="After how many months do loyalty points expire?",
        expected_document_id="loyalty-2026",
        expected_answer="Loyalty points expire 18 months after they are earned.",
        hallucinated_answer="Loyalty points expire 48 months after they are earned.",
    ),
    Question(
        id="q-express-cost",
        text="How much does express shipping cost in USD?",
        expected_document_id="shipping-2026",
        expected_answer="Express shipping arrives in 1 business day and costs 12 USD.",
        hallucinated_answer="Express shipping costs 79 USD.",
    ),
]

QUESTIONS_BY_ID = {question.id: question for question in QUESTIONS}


def wrong_topic_document(question: Question) -> Document:
    """A real, current, well-formed document that does not answer the question.

    Used by the wrong-retrieval scenario. Picking a genuine document rather
    than noise is what makes the scenario hard: nothing about the document is
    malformed, it simply is not the one that was asked for.
    """
    for document in DOCUMENTS:
        if document.id != question.expected_document_id and document.status == "current":
            return document
    raise LookupError("corpus has no alternative current document")
