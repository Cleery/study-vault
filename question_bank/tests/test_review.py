from datetime import date, datetime, time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from zoneinfo import ZoneInfo

from question_bank.models import KnowledgeCard, Question, ReviewRecord, Section, Subject
from question_bank.review import (
    apply_review,
    due_today_questions,
    get_recent_mistakes,
    get_overdue_questions,
    mastery_after_result,
    next_review_at_for_result,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def subject(db):
    return Subject.objects.create(name="数学分析")


@pytest.fixture
def section(subject):
    return Section.objects.create(subject=subject, name="极限")


def make_question(subject, title, *, mastery=Question.MASTERY_UNSTARTED, next_review_at=None):
    return Question.objects.create(
        subject=subject,
        title=title,
        draft=False,
        mastery=mastery,
        next_review_at=next_review_at,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "before,result,after",
    [
        ("unstarted", "not_done", "struggling"),
        ("unstarted", "hinted", "unstable"),
        ("unstarted", "independent", "unstable"),
        ("unstarted", "mastered", "mastered"),
        ("struggling", "not_done", "struggling"),
        ("struggling", "hinted", "unstable"),
        ("struggling", "independent", "unstable"),
        ("struggling", "mastered", "mastered"),
        ("unstable", "not_done", "struggling"),
        ("unstable", "hinted", "unstable"),
        ("unstable", "independent", "mastered"),
        ("unstable", "mastered", "mastered"),
        ("mastered", "not_done", "unstable"),
        ("mastered", "hinted", "unstable"),
        ("mastered", "independent", "mastered"),
        ("mastered", "mastered", "mastered"),
        ("unstarted", "skipped", "unstarted"),
    ],
)
def test_mastery_transition_table(before, result, after):
    assert mastery_after_result(before, result) == after


@pytest.mark.django_db
@pytest.mark.parametrize("result,days", [("not_done", 1), ("hinted", 2), ("independent", 7), ("mastered", 30)])
def test_review_uses_local_review_date_for_interval(subject, result, days):
    reviewed_at = timezone.make_aware(datetime(2026, 10, 1, 23, 30), SHANGHAI)
    next_at = next_review_at_for_result(result, reviewed_at)
    assert timezone.localtime(next_at, SHANGHAI).date() == date(2026, 10, 1) + timedelta(days=days)
    assert timezone.localtime(next_at, SHANGHAI).time() == time.min


@pytest.mark.django_db
def test_apply_review_is_atomic_and_records_before_after(subject):
    question = make_question(subject, "首次复习")
    reviewed_at = timezone.make_aware(datetime(2026, 10, 1, 10, 0), SHANGHAI)
    record = apply_review(question, "independent", duration_seconds=42, note="注意定义", reviewed_at=reviewed_at)

    question.refresh_from_db()
    assert record.question_id == question.pk
    assert record.mastery_before == "unstarted"
    assert record.mastery_after == "unstable"
    assert record.duration_seconds == 42
    assert record.note == "注意定义"
    assert question.mastery == "unstable"
    assert timezone.localtime(question.next_review_at, SHANGHAI).date() == date(2026, 10, 8)


@pytest.mark.django_db
def test_skipped_review_keeps_mastery_and_next_review(subject):
    old_next = timezone.make_aware(datetime(2026, 10, 3, 0, 0), SHANGHAI)
    question = make_question(subject, "跳过", mastery="unstable", next_review_at=old_next)
    record = apply_review(question, "skipped", reviewed_at=timezone.make_aware(datetime(2026, 10, 2), SHANGHAI))

    question.refresh_from_db()
    assert record.mastery_before == record.mastery_after == "unstable"
    assert question.mastery == "unstable"
    assert question.next_review_at == old_next


@pytest.mark.django_db
def test_due_queue_uses_shanghai_day_boundary_and_deduplicates_card(subject):
    card = KnowledgeCard.objects.create(name="极限定义", subject=subject, type="definition")
    now = timezone.make_aware(datetime(2026, 10, 2, 0, 1), SHANGHAI)
    due = make_question(subject, "到期", next_review_at=timezone.make_aware(datetime(2026, 10, 2, 0, 0), SHANGHAI))
    old = make_question(subject, "逾期", next_review_at=timezone.make_aware(datetime(2026, 10, 1, 23, 59), SHANGHAI))
    future = make_question(subject, "未到期", next_review_at=timezone.make_aware(datetime(2026, 10, 3), SHANGHAI))
    due.knowledge_cards.add(card)
    old.knowledge_cards.add(card)
    assert list(due_today_questions(now=now)) == [old, due]
    assert list(due_today_questions(now=now, knowledge_card=card)) == [old, due]
    assert future not in due_today_questions(now=now)


@pytest.mark.django_db
def test_recent_mistakes_are_last_30_days_and_unique(subject):
    now = timezone.make_aware(datetime(2026, 10, 31, 12), SHANGHAI)
    recent = make_question(subject, "近期错误")
    old = make_question(subject, "过期错误")
    ReviewRecord.objects.create(question=recent, reviewed_at=now - timedelta(days=10), result="not_done", mastery_before="unstarted", mastery_after="struggling")
    ReviewRecord.objects.create(question=recent, reviewed_at=now - timedelta(days=2), result="hinted", mastery_before="struggling", mastery_after="unstable")
    ReviewRecord.objects.create(question=old, reviewed_at=now - timedelta(days=31), result="not_done", mastery_before="unstarted", mastery_after="struggling")
    assert list(get_recent_mistakes(now=now)) == [recent]


@pytest.mark.django_db
def test_overdue_queue_orders_by_days_overdue_and_card_filter(subject):
    card = KnowledgeCard.objects.create(name="连续", subject=subject, type="definition")
    now = timezone.make_aware(datetime(2026, 10, 10, 8), SHANGHAI)
    three_days = make_question(subject, "三天", next_review_at=timezone.make_aware(datetime(2026, 10, 7), SHANGHAI))
    one_day = make_question(subject, "一天", next_review_at=timezone.make_aware(datetime(2026, 10, 9), SHANGHAI))
    three_days.knowledge_cards.add(card)
    assert list(get_overdue_questions(now=now, knowledge_card=card)) == [three_days]
    assert list(get_overdue_questions(now=now)) == [three_days, one_day]


@pytest.mark.django_db
def test_review_queue_fallback_uses_due_state_and_results(client, subject):
    due = make_question(subject, "待复习题", next_review_at=timezone.now() - timedelta(days=1))

    response = client.get(reverse("review-list"), {"queue": "unknown"})

    assert response.status_code == 200
    assert response.context["queue"] == "due"
    assert due in response.context["questions"]
    tabs = response.context["queue_tabs"]
    assert [tab["key"] for tab in tabs] == ["due", "recent", "overdue"]
    assert [tab["title"] for tab in tabs] == ["今日到期", "最近错误", "逾期"]
    assert tabs[0]["is_current"] is True
    assert sum(tab["is_current"] for tab in tabs) == 1
    assert tabs[0]["count"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize("queue", ["due", "recent", "overdue"])
def test_review_workbench_counts_respect_selected_card(client, subject, queue):
    selected = KnowledgeCard.objects.create(name="筛选卡", subject=subject, type="definition")
    other = KnowledgeCard.objects.create(name="其他卡", subject=subject, type="definition")
    first = make_question(subject, "选中卡的到期题", next_review_at=timezone.now() - timedelta(days=2))
    second = make_question(subject, "其他卡的到期题", next_review_at=timezone.now() - timedelta(days=2))
    first.knowledge_cards.add(selected)
    second.knowledge_cards.add(other)

    response = client.get(reverse("review-list"), {"queue": queue, "knowledge_card": selected.pk})

    assert response.context["queue"] == queue
    assert response.context["selected_card"] == str(selected.pk)
    assert [tab["count"] for tab in response.context["queue_tabs"]] == [1, 0, 1]
    assert [tab["is_current"] for tab in response.context["queue_tabs"]] == [
        key == queue for key in ("due", "recent", "overdue")
    ]


@pytest.mark.django_db
def test_review_detail_hides_reference_until_revealed(client, subject):
    question = make_question(subject, "隐藏参考", next_review_at=None)
    question.reference_solution = "秘密参考解答"
    question.save(update_fields=["reference_solution", "updated_at"])
    response = client.get(reverse("review-detail", args=[question.pk]))
    assert response.status_code == 200
    assert "秘密参考解答" not in response.content.decode()
    revealed = client.get(reverse("review-detail", args=[question.pk]), {"show_reference": "1"})
    assert "秘密参考解答" in revealed.content.decode()


@pytest.mark.django_db
def test_review_submission_creates_one_record_and_updates_page(client, subject):
    question = make_question(subject, "提交复习")
    response = client.post(
        reverse("review-detail", args=[question.pk]),
        {"result": "mastered", "duration_seconds": "12", "note": "稳定"},
    )
    assert response.status_code == 302
    assert ReviewRecord.objects.filter(question=question).count() == 1
    question.refresh_from_db()
    assert question.mastery == "mastered"

