"""Explicitly opt-in AI proposals; all accounting arithmetic stays local."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Protocol

from .workflow import Classification, ClassificationCategory, NormalizedTransaction


class ClassificationProvider(Protocol):
    def propose(self, transaction: NormalizedTransaction) -> tuple[ClassificationCategory, Decimal, str]: ...


@dataclass(frozen=True, slots=True)
class AIClassificationOptions:
    enabled: bool = False
    confirm_external_processing: bool = False
    max_transactions: int = 25


def classify_with_ai(
    transactions: tuple[NormalizedTransaction, ...],
    *,
    provider: ClassificationProvider | None = None,
    options: AIClassificationOptions | None = None,
) -> tuple[NormalizedTransaction, ...]:
    """Ask an explicitly enabled provider only about unresolved transactions.

    Results are always proposals requiring user confirmation. No provider call
    is made unless both opt-in switches are set.
    """
    options = options or AIClassificationOptions()
    if not options.enabled:
        return transactions
    if not options.confirm_external_processing:
        raise ValueError("Confirm external processing before enabling AI classification.")
    if provider is None:
        raise ValueError("An AI classification provider is required when AI is enabled.")
    if options.max_transactions < 1:
        raise ValueError("max_transactions must be positive.")
    result = list(transactions)
    eligible = [
        index for index, item in enumerate(result)
        if item.classification.category == ClassificationCategory.UNCLASSIFIED
        and item.classification.status not in ("duplicate", "user_confirmed")
        # Possible duplicates, transfer pairs and refunds need a human decision,
        # not a category guess that could double-count revenue or expenses.
        and not item.possible_duplicate_of
        and not item.internal_transfer_id
        and not item.is_refund
        and item.amount is not None and item.currency and item.description
    ][: options.max_transactions]
    for index in eligible:
        category, confidence, reason = provider.propose(result[index])
        try:
            category = ClassificationCategory(category)
            confidence = Decimal(confidence)
            if not confidence.is_finite():
                raise ValueError("Confidence must be finite.")
        except (TypeError, ValueError):
            category = ClassificationCategory.UNCLASSIFIED
            confidence = Decimal(0)
            reason = "AI proposal was invalid and requires manual review."
        if category == ClassificationCategory.INTERNAL_TRANSFER:
            # The deterministic two-account matcher owns transfer recognition.
            category = ClassificationCategory.UNCLASSIFIED
            reason = "Internal transfers require deterministic matching between bank accounts."
        confidence = max(Decimal(0), min(Decimal(1), confidence))
        result[index] = replace(
            result[index],
            classification=Classification(category, confidence, "ai_proposed", (str(reason)[:500],)),
        )
    return tuple(result)


class OpenAIClassificationProvider:
    """Optional OpenAI Responses API adapter; sends minimal transaction fields."""

    def __init__(self, *, model: str, client: object | None = None) -> None:
        try:
            from openai import OpenAI
            from pydantic import BaseModel
        except ImportError as exc:
            raise RuntimeError("Install the optional ai extra to use this provider.") from exc
        if not model.strip():
            raise ValueError("A model name must be supplied explicitly.")
        self._client = client or OpenAI()
        self._model = model

        class Proposal(BaseModel):
            category: str
            confidence: float
            reason: str

        self._proposal_model = Proposal

    def propose(self, transaction: NormalizedTransaction) -> tuple[ClassificationCategory, Decimal, str]:
        model = self._proposal_model
        response = self._client.responses.parse(
            model=self._model,
            store=False,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Suggest one accounting category from: revenue, operating_expense, bank_fee, "
                        "loan, owner_contribution, tax, unclassified. Do not calculate or infer missing facts. "
                        "If uncertain, choose unclassified and use confidence below 0.7. This is only a proposal."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Amount: {transaction.amount}; currency: {transaction.currency}; "
                        f"description: {transaction.description}"
                    ),
                },
            ],
            text_format=model,
        )
        proposal = response.output_parsed
        if proposal is None:
            return ClassificationCategory.UNCLASSIFIED, Decimal(0), "AI returned no valid structured proposal."
        try:
            category = ClassificationCategory(proposal.category)
        except ValueError:
            category = ClassificationCategory.UNCLASSIFIED
        if category == ClassificationCategory.INTERNAL_TRANSFER:
            category = ClassificationCategory.UNCLASSIFIED
        confidence = max(Decimal(0), min(Decimal(1), Decimal(str(proposal.confidence))))
        return category, confidence, proposal.reason[:500]
