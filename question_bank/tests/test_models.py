from datetime import timedelta

import pytest
from django.core import management
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone

from question_bank.models import (
    KnowledgeCard,
    Question,
    QuestionAttachment,
    ReviewRecord,
    Section,
    Subject,
    Tag,
)


@pytest.fixture
def subject(db):
    return Subject.objects.create(name="数学分析")


@pytest.fixture
def section(subject):
    return Section.objects.create(subject=subject, name="极限与连续")


@pytest.mark.django_db
def test_question_uses_stable_uuid_and_formal_validation(subject):
    draft = Question(draft=True)
    draft.full_clean()
    draft.save()
    assert draft.pk

    formal = Question(draft=False)
    with pytest.raises(ValidationError):
        formal.full_clean()

    formal.subject = subject
    formal.save()
    assert formal.pk
    assert formal.deleted_at is None


@pytest.mark.django_db
def test_question_can_be_soft_deleted_and_attachments_keep_order(subject):
    question = Question.objects.create(subject=subject, title="一道题", draft=False)
    second = QuestionAttachment.objects.create(
        question=question,
        file=SimpleUploadedFile("second.png", b"png"),
        file_kind="image",
        sort_order=2,
    )
    first = QuestionAttachment.objects.create(
        question=question,
        file=SimpleUploadedFile("first.png", b"png"),
        file_kind="image",
        sort_order=1,
    )
    assert list(question.attachments.values_list("sort_order", flat=True)) == [1, 2]
    assert first.file.name and second.file.name

    question.deleted_at = timezone.now()
    question.save(update_fields=["deleted_at", "updated_at"])
    question.refresh_from_db()
    assert question.deleted_at is not None


@pytest.mark.django_db
def test_knowledge_card_requires_name_subject_and_type(subject, section):
    card = KnowledgeCard(section=section)
    with pytest.raises(ValidationError):
        card.full_clean()

    card.name = "介值定理"
    card.subject = subject
    card.card_type = "theorem"
    card.full_clean()
    card.save()
    assert card.formal_statement == ""


@pytest.mark.django_db
def test_knowledge_card_prerequisites_reject_self_and_cycles(subject):
    first = KnowledgeCard.objects.create(
        name="极限", subject=subject, card_type="definition"
    )
    second = KnowledgeCard.objects.create(
        name="连续", subject=subject, card_type="definition"
    )
    first.prerequisite_cards.add(second)
    assert list(first.prerequisite_cards.all()) == [second]

    with transaction.atomic():
        with pytest.raises(ValidationError):
            first.prerequisite_cards.add(first)
    with transaction.atomic():
        with pytest.raises(ValidationError):
            second.prerequisite_cards.add(first)


@pytest.mark.django_db
def test_tag_name_is_unique_per_parent_and_redirect_resolves(subject):
    root = Tag.objects.create(name="证明")
    child = Tag.objects.create(name="反证法", parent=root)
    duplicate = Tag(name="反证法", parent=root)
    with pytest.raises(ValidationError):
        duplicate.full_clean()

    target = Tag.objects.create(name="等价变形")
    child.redirect_to = target
    child.save()
    assert child.resolve_redirect() == target

    target.redirect_to = root
    target.save()
    assert child.resolve_redirect() == root


@pytest.mark.django_db
def test_question_relationships_are_unique(subject):
    question = Question.objects.create(subject=subject, title="题目", draft=False)
    card = KnowledgeCard.objects.create(
        name="定义", subject=subject, card_type="definition"
    )
    tag = Tag.objects.create(name="定义")
    question.knowledge_cards.add(card, card)
    question.tags.add(tag, tag)
    assert question.knowledge_cards.count() == 1
    assert question.tags.count() == 1


@pytest.mark.django_db
def test_review_record_keeps_before_after_and_optional_duration(subject):
    question = Question.objects.create(subject=subject, title="题目", draft=False)
    reviewed_at = timezone.now()
    next_review = reviewed_at + timedelta(days=7)
    record = ReviewRecord.objects.create(
        question=question,
        reviewed_at=reviewed_at,
        result="independent",
        mastery_before="unstarted",
        mastery_after="unstable",
        duration_seconds=None,
        note="需要复习证明细节",
        next_review_at=next_review,
    )
    assert record.duration_seconds is None
    assert record.next_review_at == next_review


@pytest.mark.django_db
def test_seed_system_data_is_idempotent():
    management.call_command("seed_system_data", verbosity=0)
    first_counts = {
        "subjects": Subject.objects.count(),
        "sections": Section.objects.count(),
        "error_tags": Tag.objects.filter(kind="error_type").count(),
    }
    management.call_command("seed_system_data", verbosity=0)
    assert first_counts == {
        "subjects": Subject.objects.count(),
        "sections": Section.objects.count(),
        "error_tags": Tag.objects.filter(kind="error_type").count(),
    }
    assert set(Subject.objects.values_list("name", flat=True)) >= {"数学分析", "高等代数"}
