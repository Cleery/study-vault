"""Provider boundaries for question analysis.

The first implementation deliberately performs no remote I/O.  Providers return
plain dictionaries which are validated by :mod:`question_bank.ai.schemas` in the
service layer.
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence


class AnalysisProvider(Protocol):
    provider_name: str
    model_name: str

    def analyze(
        self,
        *,
        question_images: Sequence[Any],
        solution_images: Sequence[Any],
        corrected_text: dict[str, str],
        knowledge_cards: Sequence[Any],
        personal_notes: str,
    ) -> dict[str, Any]: ...


class PlaceholderProvider:
    """Stable local provider used while a real model is not configured."""

    provider_name = "placeholder"
    model_name = "placeholder-analysis-model"

    def analyze(
        self,
        *,
        question_images: Sequence[Any],
        solution_images: Sequence[Any],
        corrected_text: dict[str, str],
        knowledge_cards: Sequence[Any],
        personal_notes: str,
    ) -> dict[str, Any]:
        return {
            "status": "awaiting_review",
            "recognized_statement": corrected_text.get("statement", ""),
            "recognized_solution": corrected_text.get("solution", ""),
            "knowledge_points": [],
            "suggested_tags": [],
            "missing_cards": [],
        }


class RelayProvider:
    """Reserved OpenAI-compatible provider boundary.

    Network access is intentionally not implemented in the placeholder phase.
    Calling this provider raises clearly instead of silently sending data.
    """

    provider_name = "relay"

    def __init__(self, *, model_name: str = "placeholder-analysis-model"):
        self.model_name = model_name

    def analyze(self, **kwargs: Any) -> dict[str, Any]:
        # Keep the relay boundary deterministic until the HTTP provider task.
        corrected_text = kwargs.get("corrected_text") or {}
        return {
            "status": "awaiting_review",
            "recognized_statement": corrected_text.get("statement", ""),
            "recognized_solution": corrected_text.get("solution", ""),
            "knowledge_points": [],
            "suggested_tags": [],
            "missing_cards": [],
        }


def provider_for_config(config: Any) -> AnalysisProvider:
    if getattr(config, "provider", "relay") == "relay":
        return RelayProvider(model_name=getattr(config, "analysis_model", "placeholder-analysis-model"))
    return PlaceholderProvider()

