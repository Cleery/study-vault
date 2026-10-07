"""Synchronous, deterministic analysis service for the initial AI workflow."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from django.db import transaction

from question_bank.models import KnowledgeCard, Question, QuestionAIAnalysis

from .config import AIConfig
from .providers import AnalysisProvider, PlaceholderProvider, provider_for_config
from .schemas import AnalysisResult


def _card_text(card: KnowledgeCard) -> str:
    values = (
        card.name,
        card.formal_statement,
        card.conditions,
        card.proof,
        card.usage_signals,
    )
    return " ".join(value or "" for value in values)


def _fingerprint(question: Question, cards: Iterable[KnowledgeCard]) -> str:
    attachments = [
        {
            "name": attachment.file.name,
            "role": attachment.attachment_role,
            "sha256": attachment.content_sha256 or "",
            "order": attachment.sort_order,
        }
        for attachment in question.attachments.order_by("attachment_role", "sort_order", "id")
    ]
    payload = {
        "statement": question.recognized_statement or "",
        "solution": question.recognized_solution or "",
        "personal_signals": question.personal_signals or "",
        "attachments": attachments,
        "cards": [
            {"id": str(card.pk), "text": _card_text(card)}
            for card in sorted(cards, key=lambda item: str(item.pk))
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _keyword_candidates(question: Question, cards: Iterable[KnowledgeCard]) -> list[dict[str, Any]]:
    haystack = " ".join(
        (
            question.recognized_statement or "",
            question.recognized_solution or "",
            question.personal_signals or "",
        )
    ).casefold()
    candidates: list[dict[str, Any]] = []
    for card in cards:
        fields = [
            ("名称", card.name),
            ("正式表述", card.formal_statement),
            ("条件", card.conditions),
            ("证明", card.proof),
            ("使用信号", card.usage_signals),
        ]
        hits = [label for label, value in fields if value and value.casefold() in haystack]
        if not hits:
            continue
        confidence = min(0.95, 0.6 + 0.08 * len(hits))
        candidates.append(
            {
                "name": card.name,
                "confidence": confidence,
                "reason": "关键词命中：" + "、".join(hits),
                "matched_card_id": str(card.pk),
            }
        )
    return candidates


def _new_version(question: Question, fingerprint: str) -> int:
    latest = question.ai_analyses.order_by("-version").first()
    return (latest.version if latest else 0) + 1


def analyze_question(
    question: Question,
    *,
    provider: AnalysisProvider | None = None,
    config: AIConfig | None = None,
) -> QuestionAIAnalysis:
    """Analyze one question, reusing an existing result for identical input."""

    config = (config or AIConfig.from_env()).validated()
    cards = list(KnowledgeCard.objects.filter(subject_id=question.subject_id))
    fingerprint = _fingerprint(question, cards)
    existing = question.ai_analyses.filter(input_fingerprint=fingerprint).order_by("-version").first()
    if existing is not None:
        return existing

    with transaction.atomic():
        locked = Question.objects.select_for_update().get(pk=question.pk)
        cards = list(KnowledgeCard.objects.filter(subject_id=locked.subject_id))
        fingerprint = _fingerprint(locked, cards)
        existing = locked.ai_analyses.filter(input_fingerprint=fingerprint).order_by("-version").first()
        if existing is not None:
            return existing
        analysis = QuestionAIAnalysis.objects.create(
            question=locked,
            version=_new_version(locked, fingerprint),
            input_fingerprint=fingerprint,
            status=Question.AI_STATUS_PENDING,
            provider="",
            model="",
        )
        locked.ai_status = Question.AI_STATUS_ANALYZING if config.enabled else Question.AI_STATUS_PENDING
        locked.save(update_fields=["ai_status", "updated_at"])

    if not config.enabled:
        return analysis

    provider = provider or provider_for_config(config)
    corrected_text = {
        "statement": question.recognized_statement or "",
        "solution": question.recognized_solution or "",
    }
    try:
        payload = provider.analyze(
            question_images=list(question.attachments.filter(attachment_role="question")),
            solution_images=list(question.attachments.filter(attachment_role="solution")),
            corrected_text=corrected_text,
            knowledge_cards=cards,
            personal_notes=question.personal_signals or "",
        )
        result = AnalysisResult.from_dict(payload)
        keyword_items = _keyword_candidates(question, cards)
        merged = {item["matched_card_id"]: item for item in keyword_items}
        for item in result.knowledge_points:
            if item.matched_card_id:
                merged[item.matched_card_id] = item.to_dict()
        current_question = Question.objects.get(pk=question.pk)
        current_fingerprint = _fingerprint(
            current_question,
            KnowledgeCard.objects.filter(subject_id=current_question.subject_id),
        )
        latest_version = (
            QuestionAIAnalysis.objects.filter(question_id=question.pk)
            .order_by("-version")
            .values_list("version", flat=True)
            .first()
        )
        if current_fingerprint != fingerprint or latest_version != analysis.version:
            analysis.status = Question.AI_STATUS_PENDING
            analysis.error_message = "输入或分析版本在处理期间发生变化，结果未写回"
            analysis.save(update_fields=["status", "error_message", "updated_at"])
            return analysis
        analysis.status = result.status.value
        analysis.provider = getattr(provider, "provider_name", provider.__class__.__name__)
        analysis.model = getattr(provider, "model_name", "")
        analysis.recognized_statement = result.recognized_statement
        analysis.recognized_solution = result.recognized_solution
        analysis.knowledge_points = {"items": list(merged.values())}
        analysis.suggested_tags = {"items": [item.to_dict() for item in result.suggested_tags]}
        analysis.missing_cards = {"items": [item.to_dict() for item in result.missing_cards]}
        analysis.raw_response = result.to_dict()
        analysis.save()
        Question.objects.filter(pk=question.pk).update(
            ai_status=analysis.status,
            recognized_statement=result.recognized_statement,
            recognized_solution=result.recognized_solution,
        )
        return analysis
    except Exception as exc:
        analysis.status = Question.AI_STATUS_FAILED
        analysis.error_message = str(exc)[:2000]
        analysis.provider = getattr(provider, "provider_name", provider.__class__.__name__)
        analysis.model = getattr(provider, "model_name", "")
        analysis.save(update_fields=["status", "error_message", "provider", "model", "updated_at"])
        Question.objects.filter(pk=question.pk).update(ai_status=Question.AI_STATUS_FAILED)
        return analysis


__all__ = ["analyze_question"]

