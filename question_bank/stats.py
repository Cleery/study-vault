from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Count
from django.utils import timezone

from .models import Question, ReviewRecord


def get_statistics(now=None):
    now = now or timezone.now()
    cutoff = now - timedelta(days=30)
    questions = Question.objects.filter(deleted_at__isnull=True, archived=False)
    local_day = timezone.localtime(now, ZoneInfo("Asia/Shanghai")).date()
    due_end = timezone.make_aware(
        datetime.combine(local_day + timedelta(days=1), time.min),
        ZoneInfo("Asia/Shanghai"),
    )
    by_subject = {
        row["subject__name"] or "未分类": row["count"]
        for row in questions.values("subject__name").annotate(count=Count("pk")).order_by("subject__name")
    }
    by_mastery = {
        key: questions.filter(mastery=key).count()
        for key, _ in Question.MASTERY_CHOICES
    }
    recent_reviews = ReviewRecord.objects.filter(reviewed_at__gte=cutoff)
    return {
        "question_total": questions.count(),
        "questions_by_subject": by_subject,
        "questions_by_mastery": by_mastery,
        "reviews_last_30_days": recent_reviews.count(),
        "errors_last_30_days": recent_reviews.filter(result__in=("not_done", "hinted")).count(),
        "due_questions": questions.filter(next_review_at__isnull=False, next_review_at__lt=due_end).count(),
    }
