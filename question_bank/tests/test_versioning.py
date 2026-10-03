import json
from datetime import timedelta, timezone

import pytest
from django.core import signing

from question_bank.models import KnowledgeCard, Question, QuestionAttachment, Subject, Tag
from question_bank import versioning


@pytest.fixture
def question(db):
    subject = Subject.objects.create(name="Mathematics")
    return Question.objects.create(subject=subject, title="Original", draft=False)


@pytest.mark.django_db
def test_version_is_stable_and_payload_is_canonical(question, monkeypatch):
    monkeypatch.setattr(signing.time, "time", lambda: 1_800_000_000)
    later = QuestionAttachment.objects.create(
        question=question,
        file="questions/private/second.png",
        file_kind="image",
        sort_order=2,
    )
    earlier = QuestionAttachment.objects.create(
        question=question,
        file="questions/private/first.pdf",
        file_kind="document",
        sort_order=1,
    )
    tags = [Tag.objects.create(name=name) for name in ("B", "A")]
    cards = [
        KnowledgeCard.objects.create(name=name, subject=question.subject, type="definition")
        for name in ("B", "A")
    ]
    question.tags.add(*tags)
    question.knowledge_cards.add(*cards)

    token = versioning.build_question_version(question)
    assert token == versioning.build_question_version(question)
    assert versioning.verify_question_version(question, token)

    payload = signing.loads(token, salt=versioning.QUESTION_VERSION_SALT)
    json.dumps(payload)
    assert payload == {
        "question_id": str(question.pk),
        "updated_at": question.updated_at.astimezone(timezone.utc).isoformat(),
        "attachments": [
            {
                "id": earlier.pk,
                "updated_at": earlier.updated_at.astimezone(timezone.utc).isoformat(),
                "type": "document",
                "filename": "first.pdf",
                "order": 1,
            },
            {
                "id": later.pk,
                "updated_at": later.updated_at.astimezone(timezone.utc).isoformat(),
                "type": "image",
                "filename": "second.png",
                "order": 2,
            },
        ],
        "tag_ids": sorted(str(tag.pk) for tag in tags),
        "knowledge_card_ids": sorted(str(card.pk) for card in cards),
    }


@pytest.mark.django_db
@pytest.mark.parametrize("token", [None, "", "malformed", "a:b:c"])
def test_missing_or_malformed_token_is_rejected(question, token):
    assert not versioning.verify_question_version(question, token)


@pytest.mark.django_db
def test_tampered_token_is_rejected(question):
    token = versioning.build_question_version(question)
    replacement = "a" if token[0] != "a" else "b"
    assert not versioning.verify_question_version(question, replacement + token[1:])


@pytest.mark.django_db
def test_expired_token_is_rejected(question, monkeypatch):
    token = versioning.build_question_version(question)
    monkeypatch.setattr(versioning, "QUESTION_VERSION_MAX_AGE", -1)
    assert not versioning.verify_question_version(question, token)


@pytest.mark.django_db
def test_token_is_bound_to_question(question):
    other = Question.objects.create(subject=question.subject, title="Other", draft=False)
    assert not versioning.verify_question_version(other, versioning.build_question_version(question))


@pytest.mark.django_db
def test_question_field_change_invalidates_token(question):
    token = versioning.build_question_version(question)
    question.title = "Revised"
    question.save(update_fields=["title", "updated_at"])
    assert not versioning.verify_question_version(question, token)


@pytest.mark.django_db
@pytest.mark.parametrize("field,value", [
    ("file_kind", "document"),
    ("file", "questions/private/replaced.png"),
    ("sort_order", 2),
])
def test_attachment_change_invalidates_token(question, field, value):
    attachment = QuestionAttachment.objects.create(
        question=question, file="questions/private/original.png", sort_order=0
    )
    token = versioning.build_question_version(question)
    setattr(attachment, field, value)
    attachment.save(update_fields=[field, "updated_at"])
    assert not versioning.verify_question_version(question, token)


@pytest.mark.django_db
def test_attachment_addition_or_deletion_invalidates_token(question):
    token = versioning.build_question_version(question)
    attachment = QuestionAttachment.objects.create(
        question=question, file="questions/private/new.png", sort_order=0
    )
    assert not versioning.verify_question_version(question, token)
    token = versioning.build_question_version(question)
    attachment.delete()
    assert not versioning.verify_question_version(question, token)


@pytest.mark.django_db
def test_attachment_timestamp_change_invalidates_token(question):
    attachment = QuestionAttachment.objects.create(
        question=question, file="questions/private/original.png", sort_order=0
    )
    token = versioning.build_question_version(question)
    QuestionAttachment.objects.filter(pk=attachment.pk).update(
        updated_at=attachment.updated_at + timedelta(seconds=1)
    )
    assert not versioning.verify_question_version(question, token)


@pytest.mark.django_db
def test_tag_change_invalidates_token(question):
    token = versioning.build_question_version(question)
    question.tags.add(Tag.objects.create(name="New"))
    assert not versioning.verify_question_version(question, token)


@pytest.mark.django_db
def test_knowledge_card_change_invalidates_token(question):
    token = versioning.build_question_version(question)
    card = KnowledgeCard.objects.create(
        name="New", subject=question.subject, type="definition"
    )
    question.knowledge_cards.add(card)
    assert not versioning.verify_question_version(question, token)
