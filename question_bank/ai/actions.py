"""Transactional review actions for one immutable AI analysis version."""

from __future__ import annotations

from collections.abc import Mapping

from django.db import IntegrityError, OperationalError, transaction

from question_bank.models import (
    AIReviewOwnership,
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
MAX_CANDIDATE_KEY = 255


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


def _record_action(analysis, action, candidate_type, candidate_key, item, **metadata):
    payload = {"candidate": item, **metadata}
    record, created = QuestionAIAnalysisAction.objects.get_or_create(
        analysis=analysis,
        action_type=action,
        candidate_type=candidate_type,
        candidate_key=candidate_key,
        defaults={"payload": payload},
    )
    if not created and metadata:
        record.payload = payload
        record.save(update_fields=["payload", "updated_at"])
    return record


def _ownership(question, target_type, target_id):
    return AIReviewOwnership.objects.select_for_update().filter(
        question=question,
        target_type=target_type,
        target_id=target_id,
        active=True,
    ).first()


def _claim_created_relation(question, analysis, target_type, target_id, relation_created):
    if not relation_created:
        return False
    ownership, created = AIReviewOwnership.objects.select_for_update().get_or_create(
        question=question,
        target_type=target_type,
        target_id=target_id,
        defaults={"owning_analysis": analysis, "active": True},
    )
    if not created and not ownership.active:
        ownership.owning_analysis = analysis
        ownership.active = True
        ownership.save(update_fields=["owning_analysis", "active", "updated_at"])
        return True
    return created or ownership.owning_analysis_id == analysis.pk


def _through_get_or_create(through, **lookup):
    try:
        with transaction.atomic():
            return through.objects.get_or_create(**lookup)
    except (IntegrityError, OperationalError):
        existing = through.objects.filter(**lookup).first()
        if existing is not None:
            return existing, False
        raise InvalidCandidate("关联正在被其他操作修改，请重试。")


def _set_confirm_payload(analysis, candidate_type, candidate_key, **values):
    confirmed = QuestionAIAnalysisAction.objects.filter(
        analysis=analysis,
        action_type="confirm",
        candidate_type=candidate_type,
        candidate_key=candidate_key,
    ).first()
    if confirmed:
        payload = dict(confirmed.payload) if isinstance(confirmed.payload, dict) else {}
        payload.update(values)
        confirmed.payload = payload
        confirmed.save(update_fields=["payload", "updated_at"])


def _review_knowledge(question, analysis, candidate_key, action, item):
    try:
        card = KnowledgeCard.objects.get(pk=candidate_key, subject_id=question.subject_id)
    except (KnowledgeCard.DoesNotExist, ValueError) as exc:
        raise InvalidCandidate("候选知识卡片无效。") from exc
    through = Question.knowledge_cards.through
    if action == "confirm":
        _, relation_created = _through_get_or_create(through,
            question_id=question.pk, knowledgecard_id=card.pk
        )
        relation_owned = _claim_created_relation(
            question, analysis, "knowledge_point", card.pk, relation_created
        )
        return _record_action(
            analysis,
            action,
            "knowledge_point",
            candidate_key,
            item,
            relation_added=relation_owned,
            relation_owned=relation_owned,
            knowledge_card_id=str(card.pk),
        )
    elif action == "revoke":
        ownership = _ownership(question, "knowledge_point", card.pk)
        if ownership and ownership.owning_analysis_id == analysis.pk:
            through.objects.filter(question_id=question.pk, knowledgecard_id=card.pk).delete()
            ownership.active = False
            ownership.save(update_fields=["active", "updated_at"])
            _set_confirm_payload(
                ownership.owning_analysis,
                "knowledge_point",
                candidate_key,
                relation_owned=False,
                relation_added=False,
            )
    return _record_action(analysis, action, "knowledge_point", candidate_key, item)


def _tag_values(item):
    category = str(item.get("category") or "")
    if category not in TAG_CATEGORIES or category not in TAG_KIND_BY_AI_CATEGORY:
        raise InvalidCandidate("标签类别无效。")
    name = _normalized_name(item.get("name"))
    if not name or len(name) > 100:
        raise InvalidCandidate("标签名称无效。")
    return name, TAG_KIND_BY_AI_CATEGORY[category]


def _review_tag(question, analysis, candidate_key, action, item):
    name, kind = _tag_values(item)
    tag = Tag.objects.filter(name__iexact=name, kind=kind, parent__isnull=True).first()
    if action == "confirm":
        if tag is None:
            try:
                with transaction.atomic():
                    tag = Tag.objects.create(name=name, kind=kind)
            except (IntegrityError, OperationalError):
                tag = Tag.objects.filter(
                    name__iexact=name, kind=kind, parent__isnull=True
                ).first()
                if tag is None:
                    raise InvalidCandidate("并发创建标签失败，请重试。")
        elif tag.archived or tag.redirect_to_id:
            raise InvalidCandidate("同名标签已归档或已合并。")
        through = Question.tags.through
        _, relation_created = _through_get_or_create(through,
            question_id=question.pk, tag_id=tag.pk
        )
        relation_owned = _claim_created_relation(
            question, analysis, "tag", tag.pk, relation_created
        )
        return _record_action(
            analysis,
            action,
            "tag",
            candidate_key,
            item,
            relation_added=relation_owned,
            relation_owned=relation_owned,
            tag_id=str(tag.pk),
        )
    elif action == "revoke":
        confirmed = QuestionAIAnalysisAction.objects.filter(
            analysis=analysis,
            action_type="confirm",
            candidate_type="tag",
            candidate_key=candidate_key,
        ).first()
        payload = confirmed.payload if confirmed and isinstance(confirmed.payload, dict) else {}
        tag_id = payload.get("tag_id")
        ownership = _ownership(question, "tag", tag_id) if tag_id else None
        if ownership and ownership.owning_analysis_id == analysis.pk:
            try:
                owned_tag = Tag.objects.get(pk=tag_id)
            except (Tag.DoesNotExist, ValueError, TypeError) as exc:
                raise InvalidCandidate("标签关联目标已不存在。") from exc
            Question.tags.through.objects.filter(
                question_id=question.pk, tag_id=owned_tag.pk
            ).delete()
            ownership.active = False
            ownership.save(update_fields=["active", "updated_at"])
            _set_confirm_payload(
                ownership.owning_analysis,
                "tag",
                candidate_key,
                relation_owned=False,
                relation_added=False,
            )
    return _record_action(analysis, action, "tag", candidate_key, item)


def _review_missing(analysis, candidate_key, action, item):
    if action == "confirm":
        name = _normalized_name(item.get("name"))
        card_type = str(item.get("card_type", item.get("type", "other")) or "other")
        if not name or len(name) > 255:
            raise InvalidCandidate("待建立知识卡片名称无效。")
        allowed_types = {value for value, _ in KnowledgeCard.CARD_TYPE_CHOICES}
        if card_type not in allowed_types:
            raise InvalidCandidate("待建立知识卡片类型无效。")
        suggestion, created = MissingKnowledgeCardSuggestion.objects.select_for_update().get_or_create(
            analysis=analysis,
            candidate_key=candidate_key,
            defaults={
                "name": name,
                "card_type": card_type,
                "reason": str(item.get("reason") or ""),
            },
        )
        if not created and suggestion.status == MissingKnowledgeCardSuggestion.STATUS_DISMISSED:
            suggestion.status = MissingKnowledgeCardSuggestion.STATUS_PENDING
            suggestion.save(update_fields=["status", "updated_at"])
    elif action == "revoke":
        MissingKnowledgeCardSuggestion.objects.select_for_update().filter(
            analysis=analysis,
            candidate_key=candidate_key,
            status=MissingKnowledgeCardSuggestion.STATUS_PENDING,
        ).update(status=MissingKnowledgeCardSuggestion.STATUS_DISMISSED)
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
    if not isinstance(candidate_key, str) or not candidate_key or len(candidate_key) > MAX_CANDIDATE_KEY:
        raise InvalidCandidate("候选标识无效。")
    question = Question.objects.select_for_update().get(pk=analysis.question_id)
    locked_analysis = QuestionAIAnalysis.objects.select_for_update().get(
        pk=analysis.pk, question_id=question.pk
    )
    latest = question.ai_analyses.order_by("-version", "-id").first()
    if latest is None or latest.pk != locked_analysis.pk:
        raise StaleAnalysis("只能审核最新分析版本。")
    if locked_analysis.status not in {
        Question.AI_STATUS_AWAITING_REVIEW,
        Question.AI_STATUS_COMPLETED,
    }:
        raise InvalidCandidate("当前分析状态不允许审核。")
    item = _find_candidate(locked_analysis, candidate_type, candidate_key)
    if candidate_type == "tag":
        _tag_values(item)
    previous = locked_analysis.actions.filter(
        candidate_type=candidate_type,
        candidate_key=candidate_key,
    ).order_by("-updated_at", "-id").first()
    if action == "ignore" and previous and previous.action_type == "confirm":
            raise InvalidCandidate("已确认的候选不能再忽略。")
    if action == "ignore" and previous and previous.action_type == "revoke":
        raise InvalidCandidate("已撤销的候选不能再忽略。")
    if action == "revoke" and previous and previous.action_type == "revoke":
        return previous
    if action == "revoke" and (previous is None or previous.action_type != "confirm"):
        raise InvalidCandidate("只有已确认的候选才能撤销。")
    if action == "confirm" and previous and previous.action_type == "confirm":
        previous_payload = previous.payload if isinstance(previous.payload, Mapping) else {}
        target_id = previous_payload.get("knowledge_card_id") or previous_payload.get("tag_id")
        if not target_id:
            if candidate_type == "knowledge_point":
                target_id = candidate_key
            elif candidate_type == "tag":
                name, kind = _tag_values(item)
                target_id = Tag.objects.filter(
                    name__iexact=name, kind=kind, parent__isnull=True
                ).values_list("pk", flat=True).first()
        relation_exists = (
            question.knowledge_cards.filter(pk=target_id).exists()
            if candidate_type == "knowledge_point" and target_id
            else question.tags.filter(pk=target_id).exists()
            if candidate_type == "tag" and target_id
            else False
        )
        if relation_exists:
            return previous
    if action == "confirm" and previous and previous.action_type == "ignore":
        previous.delete()
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
