"""Synchronous, deterministic analysis service for the initial AI workflow."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Iterable

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from question_bank.models import KnowledgeCard, Question, QuestionAIAnalysis

from .config import AIConfig
from .exceptions import AIResultValidationError
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


def _redact_secret(value: Any, secret: str) -> Any:
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    if isinstance(value, Mapping):
        return {
            _redact_secret(key, secret): _redact_secret(item, secret)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_secret(item, secret) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_secret(item, secret) for item in value)
    return value


def _safe_raw_response(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    try:
        json.dumps(payload)
    except (TypeError, ValueError):
        return {}
    return payload


def _mark_failed(
    analysis: QuestionAIAnalysis,
    *,
    message: str,
    provider: AnalysisProvider,
    secret: str = "",
    raw_response: Any = None,
) -> QuestionAIAnalysis:
    provider_name = _redact_secret(
        getattr(provider, "provider_name", provider.__class__.__name__), secret
    )
    model_name = _redact_secret(getattr(provider, "model_name", ""), secret)
    completed_at = timezone.now()
    values = {
        "status": Question.AI_STATUS_FAILED,
        "error_message": message,
        "provider": provider_name,
        "model": model_name,
        "completed_at": completed_at,
        "updated_at": completed_at,
    }
    if raw_response is not None:
        values["raw_response"] = _safe_raw_response(raw_response)
    QuestionAIAnalysis.objects.filter(
        pk=analysis.pk,
        version=analysis.version,
        status=Question.AI_STATUS_PENDING,
    ).update(**values)
    latest_id = (
        QuestionAIAnalysis.objects.filter(question_id=analysis.question_id)
        .order_by("-version", "-id")
        .values_list("pk", flat=True)
        .first()
    )
    if latest_id == analysis.pk:
        Question.objects.filter(
            pk=analysis.question_id,
            ai_status=Question.AI_STATUS_ANALYZING,
        ).update(ai_status=Question.AI_STATUS_FAILED)
    analysis.refresh_from_db()
    return analysis


def purge_expired_raw_responses(
    *,
    retention_days: int | None = None,
    now=None,
) -> int:
    """Remove expired provider payloads while retaining summaries and review actions."""

    days = (
        AIConfig.from_env().raw_response_retention_days
        if retention_days is None
        else retention_days
    )
    if days <= 0:
        raise ValueError("retention_days must be positive")
    cutoff = (now or timezone.now()) - timedelta(days=days)
    return QuestionAIAnalysis.objects.filter(
        Q(completed_at__lt=cutoff)
        | Q(completed_at__isnull=True, updated_at__lt=cutoff)
    ).exclude(raw_response={}).update(raw_response={}, updated_at=timezone.now())


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
    existing = question.ai_analyses.exclude(
        status=Question.AI_STATUS_FAILED
    ).filter(input_fingerprint=fingerprint).order_by("-version").first()
    if existing is not None:
        return existing

    with transaction.atomic():
        locked = Question.objects.select_for_update().get(pk=question.pk)
        cards = list(KnowledgeCard.objects.filter(subject_id=locked.subject_id))
        fingerprint = _fingerprint(locked, cards)
        existing = locked.ai_analyses.exclude(
            status=Question.AI_STATUS_FAILED
        ).filter(input_fingerprint=fingerprint).order_by("-version").first()
        if existing is not None:
            return existing
        while True:
            analysis, created = QuestionAIAnalysis.objects.get_or_create(
                question=locked,
                version=_new_version(locked, fingerprint),
                defaults={
                    "input_fingerprint": fingerprint,
                    "status": Question.AI_STATUS_PENDING,
                    "provider": "",
                    "model": "",
                },
            )
            if created:
                break
            if (
                analysis.input_fingerprint == fingerprint
                and analysis.status != Question.AI_STATUS_FAILED
            ):
                return analysis
        locked.ai_status = Question.AI_STATUS_ANALYZING if config.enabled else Question.AI_STATUS_PENDING
        locked.save(update_fields=["ai_status", "updated_at"])
        source_question = locked

    if not config.enabled:
        return analysis

    corrected_text = {
        "statement": source_question.recognized_statement or "",
        "solution": source_question.recognized_solution or "",
    }
    try:
        provider = provider or provider_for_config(config)
    except Exception:
        return _mark_failed(
            analysis,
            message="AI Provider 配置无效，请检查服务配置。",
            provider=PlaceholderProvider(),
            secret=config.api_key,
        )

    try:
        payload = provider.analyze(
            question_images=list(source_question.attachments.filter(attachment_role="question")),
            solution_images=list(source_question.attachments.filter(attachment_role="solution")),
            corrected_text=corrected_text,
            knowledge_cards=cards,
            personal_notes=source_question.personal_signals or "",
        )
        payload = _redact_secret(payload, config.api_key)
    except TimeoutError:
        return _mark_failed(
            analysis,
            message="AI Provider 请求超时，请稍后重试。",
            provider=provider,
            secret=config.api_key,
        )
    except Exception:
        return _mark_failed(
            analysis,
            message="AI 分析暂时不可用，请稍后重试。",
            provider=provider,
            secret=config.api_key,
        )

    try:
        result = AnalysisResult.from_dict(payload)
    except (AIResultValidationError, TypeError, ValueError, KeyError):
        return _mark_failed(
            analysis,
            message="AI 返回格式无效，请重新分析。",
            provider=provider,
            secret=config.api_key,
            raw_response=payload,
        )

    try:
        keyword_items = _keyword_candidates(source_question, cards)
    except Exception:
        return _mark_failed(
            analysis,
            message="候选知识卡片检索失败，请稍后重试。",
            provider=provider,
            secret=config.api_key,
            raw_response=payload,
        )

    try:
        merged = {item["matched_card_id"]: item for item in keyword_items}
        for item in result.knowledge_points:
            if item.matched_card_id:
                merged[item.matched_card_id] = item.to_dict()
        with transaction.atomic():
            current_question = Question.objects.select_for_update().get(pk=question.pk)
            current_fingerprint = _fingerprint(
                current_question,
                KnowledgeCard.objects.filter(subject_id=current_question.subject_id),
            )
            latest_id = (
                QuestionAIAnalysis.objects.filter(question_id=question.pk)
                .order_by("-version", "-id")
                .values_list("pk", flat=True)
                .first()
            )
            if current_fingerprint != fingerprint or latest_id != analysis.pk:
                QuestionAIAnalysis.objects.filter(
                    pk=analysis.pk,
                    version=analysis.version,
                    status=Question.AI_STATUS_PENDING,
                ).update(
                    error_message="输入或分析版本在处理期间发生变化，结果未写回",
                    updated_at=timezone.now(),
                )
                analysis.refresh_from_db()
                return analysis
            completed_at = timezone.now()
            updated = QuestionAIAnalysis.objects.filter(
                pk=analysis.pk,
                version=analysis.version,
                status=Question.AI_STATUS_PENDING,
            ).update(
                status=result.status.value,
                provider=_redact_secret(
                    getattr(provider, "provider_name", provider.__class__.__name__),
                    config.api_key,
                ),
                model=_redact_secret(getattr(provider, "model_name", ""), config.api_key),
                recognized_statement=result.recognized_statement,
                recognized_solution=result.recognized_solution,
                knowledge_points={"items": list(merged.values())},
                suggested_tags={"items": [item.to_dict() for item in result.suggested_tags]},
                missing_cards={"items": [item.to_dict() for item in result.missing_cards]},
                raw_response=result.to_dict(),
                error_message="",
                completed_at=completed_at,
                updated_at=completed_at,
            )
            if not updated:
                analysis.refresh_from_db()
                return analysis
            Question.objects.filter(
                pk=question.pk,
                ai_status=Question.AI_STATUS_ANALYZING,
            ).update(
                ai_status=result.status.value,
                recognized_statement=result.recognized_statement,
                recognized_solution=result.recognized_solution,
            )
        analysis.refresh_from_db()
        return analysis
    except Exception:
        return _mark_failed(
            analysis,
            message="AI 分析结果保存失败，请稍后重试。",
            provider=provider,
            secret=config.api_key,
            raw_response=payload,
        )


__all__ = ["analyze_question", "purge_expired_raw_responses"]

