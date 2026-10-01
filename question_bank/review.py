"""Review state transitions and query helpers."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from .models import KnowledgeCard, Question, ReviewRecord


REVIEW_ZONE = ZoneInfo("Asia/Shanghai")
RESULT_INTERVALS = {
    "not_done": 1,
    "hinted": 2,
    "independent": 7,
    "mastered": 30,
}

MASTERY_TRANSITIONS = {
    "unstarted": {
        "not_done": "struggling",
        "hinted": "unstable",
        "independent": "unstable",
        "mastered": "mastered",
    },
    "struggling": {
        "not_done": "struggling",
        "hinted": "unstable",
        "independent": "unstable",
        "mastered": "mastered",
    },
    "unstable": {
        "not_done": "struggling",
        "hinted": "unstable",
        "independent": "mastered",
        "mastered": "mastered",
    },
    "mastered": {
        "not_done": "unstable",
        "hinted": "unstable",
        "independent": "mastered",
        "mastered": "mastered",
    },
}


def _as_aware(value):
    value = value or timezone.now()
    if timezone.is_naive(value):
        return timezone.make_aware(value, REVIEW_ZONE)
    return value


def _local_date(value=None) -> date:
    return timezone.localtime(_as_aware(value), REVIEW_ZONE).date()


def _at_local_start(day: date):
    return timezone.make_aware(datetime.combine(day, time.min), REVIEW_ZONE)


def mastery_after_result(mastery_before, result):
    if result == "skipped":
        if mastery_before not in MASTERY_TRANSITIONS:
            raise ValueError(f"invalid mastery state: {mastery_before}")
        return mastery_before
    try:
        return MASTERY_TRANSITIONS[mastery_before][result]
    except KeyError as exc:
        raise ValueError(f"invalid review result or mastery state: {result}, {mastery_before}") from exc


def next_review_at_for_result(result, reviewed_at=None):
    if result == "skipped":
        return None
    try:
        interval = RESULT_INTERVALS[result]
    except KeyError as exc:
        raise ValueError(f"invalid review result: {result}") from exc
    return _at_local_start(_local_date(reviewed_at) + timedelta(days=interval))


@transaction.atomic
def apply_review(question, result, *, duration_seconds=None, note="", reviewed_at=None):
    """Apply one review and persist its immutable before/after snapshot."""
    question_id = question.pk if isinstance(question, Question) else question
    locked = Question.objects.select_for_update().get(pk=question_id)
    reviewed_at = _as_aware(reviewed_at)
    mastery_before = locked.mastery
    mastery_after = mastery_after_result(mastery_before, result)
    if duration_seconds in ("", None):
        duration_seconds = None
    elif isinstance(duration_seconds, str):
        try:
            duration_seconds = int(duration_seconds)
        except ValueError as exc:
            raise ValueError("duration_seconds must be an integer") from exc
    if duration_seconds is not None and duration_seconds < 0:
        raise ValueError("duration_seconds cannot be negative")

    next_review_at = locked.next_review_at if result == "skipped" else next_review_at_for_result(result, reviewed_at)
    if result != "skipped":
        locked.mastery = mastery_after
        locked.next_review_at = next_review_at
        locked.save(update_fields=["mastery", "next_review_at", "updated_at"])
    return ReviewRecord.objects.create(
        question=locked,
        reviewed_at=reviewed_at,
        result=result,
        mastery_before=mastery_before,
        mastery_after=mastery_after,
        duration_seconds=duration_seconds,
        note=note or "",
        next_review_at=next_review_at,
    )


def _active_question_queryset():
    return Question.objects.filter(deleted_at__isnull=True, archived=False).select_related("subject", "section")


def _knowledge_card_filter(queryset, knowledge_card):
    if knowledge_card:
        card_id = knowledge_card.pk if isinstance(knowledge_card, KnowledgeCard) else knowledge_card
        queryset = queryset.filter(knowledge_cards__pk=card_id)
    return queryset


def due_today_questions(*, now=None, knowledge_card=None):
    """Return all questions due through the current local calendar day."""
    day = _local_date(now)
    end = _at_local_start(day + timedelta(days=1))
    queryset = _active_question_queryset().filter(next_review_at__isnull=False, next_review_at__lt=end)
    return _knowledge_card_filter(queryset, knowledge_card).distinct().order_by("next_review_at", "pk")


def get_overdue_questions(*, now=None, knowledge_card=None):
    """Return strictly overdue questions, oldest due dates first."""
    day = _local_date(now)
    start = _at_local_start(day)
    queryset = _active_question_queryset().filter(next_review_at__isnull=False, next_review_at__lt=start)
    return _knowledge_card_filter(queryset, knowledge_card).distinct().order_by("next_review_at", "pk")


def get_recent_mistakes(*, now=None, knowledge_card=None):
    """Return each question once, ordered by its most recent recent mistake."""
    cutoff = _as_aware(now) - timedelta(days=30)
    matching = ReviewRecord.objects.filter(
        question_id=OuterRef("pk"),
        reviewed_at__gte=cutoff,
        result__in=("not_done", "hinted"),
    ).order_by("-reviewed_at", "-pk")
    queryset = _active_question_queryset().filter(
        review_records__reviewed_at__gte=cutoff,
        review_records__result__in=("not_done", "hinted"),
    )
    queryset = queryset.annotate(last_mistake=Subquery(matching.values("reviewed_at")[:1]))
    return _knowledge_card_filter(queryset, knowledge_card).distinct().order_by("-last_mistake", "pk")


# Short aliases keep the service convenient for callers and tests.
review_question = apply_review
record_review = apply_review
recent_mistake_questions = get_recent_mistakes
recent_mistakes = get_recent_mistakes
overdue_questions = get_overdue_questions
due_questions = due_today_questions

