from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from question_bank.forms import QuestionForm
from question_bank.models import KnowledgeCard, Question, Section, Subject, Tag
from question_bank.search import build_question_queryset, merge_tags


@pytest.fixture
def subject(db):
    return Subject.objects.create(name="数学分析")


@pytest.fixture
def section(subject):
    return Section.objects.create(subject=subject, name="极限")


def make_question(subject, title, **kwargs):
    return Question.objects.create(subject=subject, title=title, draft=False, **kwargs)


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["title", "statement", "personal_solution", "reference_solution", "error_note"])
def test_keyword_search_covers_all_text_fields(client, subject, field):
    question = make_question(subject, "其他")
    setattr(question, field, "needle")
    question.save()
    response = client.get(reverse("question-list"), {"q": "NEEDLE"})
    assert response.status_code == 200
    assert str(question.pk) in response.content.decode()


@pytest.mark.django_db
def test_filters_use_and_between_fields_and_or_within_field(client, subject, section):
    other = Subject.objects.create(name="高等代数")
    q1 = make_question(subject, "q1", section=section, mastery=Question.MASTERY_MASTERED)
    q2 = make_question(subject, "q2", section=section, mastery=Question.MASTERY_STRUGGLING)
    q3 = make_question(other, "q3", mastery=Question.MASTERY_MASTERED)
    response = client.get(
        reverse("question-list"),
        [("subject", str(subject.pk)), ("mastery", Question.MASTERY_MASTERED), ("mastery", Question.MASTERY_STRUGGLING)],
    )
    body = response.content.decode()
    assert str(q1.pk) in body and str(q2.pk) in body
    assert str(q3.pk) not in body


@pytest.mark.django_db
def test_search_paginates_twenty_and_orders_by_updated_then_uuid(subject):
    questions = [make_question(subject, f"q{i}") for i in range(21)]
    page = build_question_queryset({})
    assert page.count() == 21
    from django.core.paginator import Paginator

    assert Paginator(page, 20).num_pages == 2
    assert list(page.values_list("updated_at", flat=True)) == sorted(
        page.values_list("updated_at", flat=True), reverse=True
    )


@pytest.mark.django_db
def test_due_filter_and_knowledge_card_filter(subject):
    card = KnowledgeCard.objects.create(name="极限定义", subject=subject, type="definition")
    due = make_question(subject, "due", next_review_at=timezone.now() - timedelta(minutes=1))
    future = make_question(subject, "future", next_review_at=timezone.now() + timedelta(days=1))
    due.knowledge_cards.add(card)
    result = build_question_queryset({"due": "1", "knowledge_card": str(card.pk)})
    assert list(result) == [due]
    assert future not in result


@pytest.mark.django_db
def test_merge_migrates_relations_and_compresses_redirects(subject):
    source = Tag.objects.create(name="旧")
    middle = Tag.objects.create(name="中")
    target = Tag.objects.create(name="新")
    question = make_question(subject, "带标签")
    question.tags.add(source)
    source.redirect_to = middle
    source.save()
    merge_tags(middle, target)
    source.refresh_from_db()
    middle.refresh_from_db()
    assert source.redirect_to_id == target.pk
    assert middle.redirect_to_id == target.pk
    assert set(question.tags.all()) == {source, target}


@pytest.mark.django_db
def test_archived_tag_is_hidden_for_new_questions_but_kept_in_history(subject):
    archived = Tag.objects.create(name="历史", archived=True)
    question = make_question(subject, "历史题")
    question.tags.add(archived)
    assert archived not in QuestionForm().fields["tags"].queryset
    assert archived in question.tags.all()


@pytest.mark.django_db
def test_root_route_is_question_search_home(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "题目检索" in response.content.decode()

