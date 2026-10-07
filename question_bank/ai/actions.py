"""Transactional review actions for one immutable AI analysis version."""

from __future__ import annotations

from django.db import transaction

from question_bank.models import (
    KnowledgeCard,
    MissingKnowledgeCardSuggestion,
    Question,
    QuestionAIAnalysis,
    QuestionAIAnalysisAction,
    Tag,
)

from .schemas import TAG_CATEGORIES


class AnalysisReviewError(ValueError):
    pass


class StaleAnalysis(AnalysisReviewError):
    pass


class InvalidCandidate(AnalysisReviewError):
    pass


TAG_KIND_BY_AI_CATEGORY = {
    "topic": "problem_type",
    "method": "method",
    "signal": "custom",
}


def _items(analysis: QuestionAIAnalysis, field: str) -> list[dict]:
    value = getattr(analysis, field, {})
    items = value.get("items", []) if isinstance(value, dict) else []
    return [item for item in items if isinstance(item, dict)]


def _normalized_name(value) -> str:
    return " ".join(str(value or "").split())


def _item_key(candidate_type: str, item: dict) -> str:
    if candidate_type == "knowledge_point":
        return str(item.get("matched_card_id") or "")
    if candidate_type == "tag":
        return f"{item.get('category', '')}:{_normalized_name(item.get('name'))}"
    if candidate_type == "missing_card":
        card_type = item.get("card_type", item.get("type", "other"))
        return f"{card_type}:{_normalized_name(item.get('name'))}"
    return ""


def _find_candidate(analysis: QuestionAIAnalysis, candidate_type: str, candidate_key: str) -> dict:
    fields = {
        "knowledge_point": "knowledge_points",
        "tag": "suggested_tags",
        "missing_card": "missing_cards",
    }
    field = fields.get(candidate_type)
    if field is None:
        raise InvalidCandidate("候选类型无效。")
    for item in _items(analysis, field):
        if _item_key(candidate_type, item) == candidate_key:
            return item
    raise InvalidCandidate("候选不存在或已经变化。")


def _record_action(analysis, action, candidate_type, candidate_key, item):
    record, _ = QuestionAIAnalysisAction.objects.get_or_create(
        analysis=analysis,
        action_type=action,
        candidate_type=candidate_type,
        candidate_key=candidate_key,
        defaults={"payload": {"candidate": item}},
    )
    return record


def _review_knowledge(question, analysis, candidate_key, action, item):
    try:
        card = KnowledgeCard.objects.get(pk=candidate_key, subject_id=question.subject_id)
    except (KnowledgeCard.DoesNotExist, ValueError) as exc:
        raise InvalidCandidate("候选知识卡片无效。") from exc
    if action == "confirm":
        question.knowledge_cards.add(card)
    elif action == "revoke":
        question.knowledge_cards.remove(card)
    return _record_action(analysis, action, "knowledge_point", candidate_key, item)


def _review_tag(question, analysis, candidate_key, action, item):
    category = str(item.get("category") or "")
    if category not in TAG_CATEGORIES or category not in TAG_KIND_BY_AI_CATEGORY:
        raise InvalidCandidate("标签类别无效。")
    name = _normalized_name(item.get("name"))
    if not name or len(name) > 100:
        raise InvalidCandidate("标签名称无效。")
    kind = TAG_KIND_BY_AI_CATEGORY[category]
    tag = Tag.objects.filter(name__iexact=name, parent__isnull=True).first()
    if action == "confirm":
        if tag is None:
            tag = Tag.objects.create(name=name, kind=kind)
        elif tag.archived or tag.redirect_to_id:
            raise InvalidCandidate("同名标签已归档或已合并。")
        elif tag.kind != kind:
            raise InvalidCandidate("同名标签的类别与建议不一致。")
        question.tags.add(tag)
    elif action == "revoke" and tag is not None:
        question.tags.remove(tag.resolve_redirect())
    return _record_action(analysis, action, "tag", candidate_key, item)


def _review_missing(analysis, candidate_key, action, item):
    if action == "confirm":
        name = _normalized_name(item.get("name"))
        card_type = str(item.get("card_type", item.get("type", "other")) or "other")
        if not name or len(name) > 255:
            raise InvalidCandidate("待建立知识卡片名称无效。")
        MissingKnowledgeCardSuggestion.objects.get_or_create(
            analysis=analysis,
            candidate_key=candidate_key,
            defaults={
                "name": name,
                "card_type": card_type,
                "reason": str(item.get("reason") or ""),
            },
        )
    elif action == "revoke":
        MissingKnowledgeCardSuggestion.objects.filter(
            analysis=analysis,
            candidate_key=candidate_key,
            status=MissingKnowledgeCardSuggestion.STATUS_PENDING,
        ).delete()
    return _record_action(analysis, action, "missing_card", candidate_key, item)


@transaction.atomic
def review_candidate(
    analysis: QuestionAIAnalysis,
    candidate_type: str,
    candidate_key: str,
    action: str,
) -> QuestionAIAnalysisAction:
    if action not in {"confirm", "ignore", "revoke"}:
        raise InvalidCandidate("审核动作无效。")
    question = Question.objects.select_for_update().get(pk=analysis.question_id)
    locked_analysis = QuestionAIAnalysis.objects.select_for_update().get(
        pk=analysis.pk, question_id=question.pk
    )
    latest = question.ai_analyses.order_by("-version", "-id").first()
    if latest is None or latest.pk != locked_analysis.pk:
        raise StaleAnalysis("分析版本已经更新。")
    item = _find_candidate(locked_analysis, candidate_type, candidate_key)
    if action == "ignore":
        return _record_action(locked_analysis, action, candidate_type, candidate_key, item)
    if candidate_type == "knowledge_point":
        return _review_knowledge(question, locked_analysis, candidate_key, action, item)
    if candidate_type == "tag":
        return _review_tag(question, locked_analysis, candidate_key, action, item)
    return _review_missing(locked_analysis, candidate_key, action, item)


__all__ = [
    "AnalysisReviewError",
    "InvalidCandidate",
    "StaleAnalysis",
    "review_candidate",
]
