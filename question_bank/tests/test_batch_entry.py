import io
import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from question_bank.models import Question, QuestionAttachment, Section, Subject, Tag


def image_file(name="question.png", color="white"):
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(stream, format="PNG")
    return SimpleUploadedFile(name, stream.getvalue(), content_type="image/png")


@pytest.mark.django_db
def test_batch_upload_creates_one_draft_with_one_image(client, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    batch_id, upload_id = uuid.uuid4(), uuid.uuid4()

    response = client.post(reverse("batch-upload"), {
        "batch_id": str(batch_id), "client_upload_id": str(upload_id),
        "image": image_file(),
    }, HTTP_ACCEPT="application/json")

    assert response.status_code == 201
    question = Question.objects.get()
    attachment = QuestionAttachment.objects.get(question=question)
    assert question.draft and question.batch_id == batch_id
    assert attachment.client_upload_id == upload_id
    assert attachment.content_sha256 and attachment.file.storage.exists(attachment.file.name)
    assert response.json()["question_id"] == str(question.pk)


@pytest.mark.django_db
def test_batch_upload_retries_without_creating_another_question(client, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    batch_id, upload_id = uuid.uuid4(), uuid.uuid4()
    data = {"batch_id": str(batch_id), "client_upload_id": str(upload_id)}
    first = client.post(reverse("batch-upload"), {**data, "image": image_file()})
    second = client.post(reverse("batch-upload"), {**data, "image": image_file()})

    assert first.status_code == 302
    assert second.status_code == 302
    assert Question.objects.count() == QuestionAttachment.objects.count() == 1


@pytest.mark.django_db
def test_batch_upload_rejects_changed_image_for_same_id(client, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    data = {"batch_id": str(uuid.uuid4()), "client_upload_id": str(uuid.uuid4())}
    client.post(reverse("batch-upload"), {**data, "image": image_file()})
    response = client.post(reverse("batch-upload"), {**data, "image": image_file(color="black")})

    assert response.status_code == 409
    assert Question.objects.count() == QuestionAttachment.objects.count() == 1


@pytest.mark.django_db
def test_batch_upload_rejects_invalid_image_without_records(client, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    response = client.post(reverse("batch-upload"), {
        "batch_id": str(uuid.uuid4()), "client_upload_id": str(uuid.uuid4()),
        "image": SimpleUploadedFile("bad.png", b"bad", content_type="image/png"),
    }, HTTP_ACCEPT="application/json")

    assert response.status_code == 400
    assert not Question.objects.exists()
    assert not QuestionAttachment.objects.exists()


@pytest.mark.django_db
def test_batch_page_and_metadata_update(client, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    batch_id = uuid.uuid4()
    client.post(reverse("batch-upload"), {
        "batch_id": str(batch_id), "client_upload_id": str(uuid.uuid4()),
        "image": image_file(),
    })
    question = Question.objects.get()
    page = client.get(reverse("batch-detail", args=[batch_id]))
    assert page.status_code == 200
    assert str(question.pk) in page.content.decode()

    response = client.post(reverse("batch-metadata", args=[batch_id, question.pk]), {
        "subject": "实变函数", "section": "测度", "tags": [],
    })
    assert response.status_code == 302
    question.refresh_from_db()
    assert question.subject.name == "实变函数"
    assert question.section.name == "测度"


@pytest.mark.django_db
def test_bulk_metadata_changes_only_selected_drafts(client, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    batch_id = uuid.uuid4()
    first = Question.objects.create(batch_id=batch_id)
    second = Question.objects.create(batch_id=batch_id)
    tag = Tag.objects.create(name="极限")
    response = client.post(reverse("batch-apply", args=[batch_id]), {
        "questions": [str(first.pk)], "apply_subject": "on", "subject": "数学分析",
        "apply_section": "on", "section": "数列极限", "add_tags": "on", "tags": [str(tag.pk)],
    })

    assert response.status_code == 302
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.subject.name == "数学分析"
    assert first.section.name == "数列极限"
    assert list(first.tags.all()) == [tag]
    assert second.subject is None and second.section is None and not second.tags.exists()


@pytest.mark.django_db
def test_question_search_can_show_only_drafts(client):
    draft = Question.objects.create(title="待整理", draft=True)
    Question.objects.create(title="已完成", draft=False)
    response = client.get(reverse("question-list"), {"draft": "1"})
    assert response.status_code == 200
    assert list(response.context["questions"]) == [draft]
    assert "批量录入" in response.content.decode()
    assert "name=\"draft\" value=\"1\"" in response.content.decode()


@pytest.mark.django_db
def test_draft_filter_excludes_archived_even_when_archived_requested(client):
    active = Question.objects.create(draft=True)
    Question.objects.create(draft=True, archived=True)
    response = client.get(reverse("question-list"), {"draft": "1", "include_archived": "1"})
    assert list(response.context["questions"]) == [active]


@pytest.mark.django_db
def test_batch_apply_rejects_foreign_and_invalid_ids_without_creating_taxonomy(client):
    batch_id = uuid.uuid4()
    foreign = Question.objects.create(batch_id=uuid.uuid4())
    for selected in (str(foreign.pk), "invalid-id"):
        response = client.post(reverse("batch-apply", args=[batch_id]), {
            "questions": [selected], "apply_subject": "on", "subject": "不应创建",
        })
        assert response.status_code == 400
    assert not Subject.objects.filter(name="不应创建").exists()


@pytest.mark.django_db
def test_batch_apply_rejects_archived_tag_without_partial_changes(client):
    batch_id = uuid.uuid4()
    question = Question.objects.create(batch_id=batch_id)
    tag = Tag.objects.create(name="旧标签", archived=True)
    response = client.post(reverse("batch-apply", args=[batch_id]), {
        "questions": [str(question.pk)], "apply_subject": "on", "subject": "不应创建",
        "add_tags": "on", "tags": [str(tag.pk)],
    })
    assert response.status_code == 400
    question.refresh_from_db()
    assert question.subject is None and not Subject.objects.filter(name="不应创建").exists()


@pytest.mark.django_db
def test_batch_metadata_rejects_published_question(client):
    batch_id = uuid.uuid4()
    question = Question.objects.create(batch_id=batch_id, title="已发布", draft=False)
    response = client.post(reverse("batch-metadata", args=[batch_id, question.pk]), {
        "subject": "不应创建", "section": "", "tags": [],
    })
    assert response.status_code == 404
    assert not Subject.objects.filter(name="不应创建").exists()


@pytest.mark.django_db
def test_batch_native_upload_redirects_to_same_batch(client, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    batch_id = uuid.uuid4()
    response = client.post(reverse("batch-upload-page"), {
        "batch_id": str(batch_id), "image": image_file(),
    })
    assert response.status_code == 302
    assert f"batch_id={batch_id}" in response["Location"]
    assert client.get(response["Location"]).status_code == 200


@pytest.mark.django_db
def test_batch_apply_with_no_selection_does_not_create_subject(client):
    response = client.post(reverse("batch-apply", args=[uuid.uuid4()]), {
        "apply_subject": "on", "subject": "不应创建",
    })
    assert response.status_code == 400
    assert not Subject.objects.filter(name="不应创建").exists()


@pytest.mark.django_db
def test_batch_apply_rejects_overlong_taxonomy_without_changes(client):
    batch_id = uuid.uuid4()
    question = Question.objects.create(batch_id=batch_id)
    response = client.post(reverse("batch-apply", args=[batch_id]), {
        "questions": [str(question.pk)], "apply_subject": "on", "subject": "x" * 101,
    })
    assert response.status_code == 400
    question.refresh_from_db()
    assert question.subject is None


@pytest.mark.django_db
def test_batch_error_preserves_selection_and_inputs(client):
    batch_id = uuid.uuid4()
    question = Question.objects.create(batch_id=batch_id)
    response = client.post(reverse("batch-apply", args=[batch_id]), {
        "questions": [str(question.pk)], "apply_section": "on", "section": "数列极限",
    })
    assert response.status_code == 400
    content = response.content.decode()
    assert f'value="{question.pk}" checked' in content
    assert 'value="数列极限"' in content


@pytest.mark.django_db
def test_batch_continue_upload_keeps_batch_id(client):
    batch_id = uuid.uuid4()
    response = client.get(reverse("batch-detail", args=[batch_id]))
    assert f"batch_id={batch_id}" in response.content.decode()
