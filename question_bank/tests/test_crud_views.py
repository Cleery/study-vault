import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from question_bank.models import KnowledgeCard, Question, QuestionAttachment, Section, Subject


def image_file(name, color):
    stream = io.BytesIO()
    Image.new("RGB", (3, 3), color=color).save(stream, format="PNG")
    return SimpleUploadedFile(name, stream.getvalue(), content_type="image/png")


@pytest.fixture
def subject(db):
    return Subject.objects.create(name="数学分析")


@pytest.fixture
def second_subject(db):
    return Subject.objects.create(name="高等代数")


@pytest.fixture
def section(subject):
    return Section.objects.create(subject=subject, name="极限与连续")


@pytest.mark.django_db
def test_draft_question_creation_allows_empty_content(client):
    response = client.post(reverse("question-create"), {"draft": "on"})

    assert response.status_code == 302
    question = Question.objects.get()
    assert question.draft is True
    assert question.title == ""


@pytest.mark.django_db
def test_formal_question_requires_subject_title_or_statement(client):
    response = client.post(reverse("question-create"), {"draft": ""})

    assert response.status_code == 200
    assert "正式题目至少需要科目、标题或题干中的一项" in response.content.decode()
    assert Question.objects.count() == 0


@pytest.mark.django_db
def test_question_creation_accepts_markdown_latex_and_ordered_images(client, subject, section):
    response = client.post(
        reverse("question-create"),
        {
            "subject": str(subject.pk),
            "section": str(section.pk),
            "title": "极限题",
            "statement": "**求极限** $x^2$",
            "personal_solution": "\n$$x=0$$",
            "reference_solution": "\\n\\(x=0\\)",
            "error_note": "常见错误",
            "mastery": "unstarted",
            "draft": "",
            "attachments": [image_file("first.png", (255, 0, 0)), image_file("second.png", (0, 0, 255))],
        },
    )

    assert response.status_code == 302
    question = Question.objects.get()
    assert question.draft is False
    assert list(question.attachments.values_list("sort_order", flat=True)) == [0, 1]
    assert question.attachments.count() == 2
    detail = client.get(reverse("question-detail", args=[question.pk]))
    body = detail.content.decode()
    assert "<strong>求极限</strong>" in body
    assert "$x^2$" in body
    assert "x=0" in body


@pytest.mark.django_db
def test_question_edit_updates_fields_and_relationships(client, subject, section):
    card = KnowledgeCard.objects.create(name="极限定义", subject=subject, type="definition")
    question = Question.objects.create(subject=subject, title="旧标题", draft=False)

    response = client.post(
        reverse("question-edit", args=[question.pk]),
        {
            "subject": str(subject.pk),
            "section": str(section.pk),
            "title": "新标题",
            "statement": "题干",
            "personal_solution": "解法",
            "reference_solution": "参考",
            "error_note": "错误",
            "mastery": "struggling",
            "knowledge_cards": [str(card.pk)],
            "draft": "",
        },
    )

    assert response.status_code == 302
    question.refresh_from_db()
    assert question.title == "新标题"
    assert question.mastery == "struggling"
    assert list(question.knowledge_cards.all()) == [card]


@pytest.mark.django_db
def test_question_detail_shows_links_and_attachment_order(client, subject):
    question = Question.objects.create(subject=subject, title="详情题", statement="内容", draft=False)
    QuestionAttachment.objects.create(question=question, file=image_file("b.png", (0, 0, 1)), sort_order=1)
    QuestionAttachment.objects.create(question=question, file=image_file("a.png", (0, 1, 0)), sort_order=0)

    response = client.get(reverse("question-detail", args=[question.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "详情题" in content
    assert "题目附件 1" in content
    assert list(question.attachments.values_list("sort_order", flat=True)) == [0, 1]


@pytest.mark.django_db
def test_knowledge_card_creation_persists_all_fields_and_question_link(client, subject, section):
    prerequisite = KnowledgeCard.objects.create(name="前置概念", subject=subject, type="definition")
    question = Question.objects.create(subject=subject, title="关联题", draft=False)
    response = client.post(
        reverse("knowledge-card-create"),
        {
            "name": "介值定理",
            "subject": str(subject.pk),
            "section": str(section.pk),
            "type": "theorem",
            "formal_statement": "若连续则存在 $c$",
            "conditions": "连续",
            "proof": "利用介值性",
            "usage_signals": "看到闭区间",
            "common_mistakes": "忽略连续",
            "personal_notes": "重点掌握",
            "prerequisite_cards": [str(prerequisite.pk)],
            "questions": [str(question.pk)],
        },
    )

    assert response.status_code == 302
    card = KnowledgeCard.objects.get(name="介值定理")
    assert card.formal_statement == "若连续则存在 $c$"
    assert list(card.prerequisite_cards.all()) == [prerequisite]
    assert list(card.questions.all()) == [question]


@pytest.mark.django_db
def test_knowledge_card_list_filters_by_subject_and_type(client, subject, second_subject):
    KnowledgeCard.objects.create(name="极限定义", subject=subject, type="definition")
    KnowledgeCard.objects.create(name="代数定理", subject=second_subject, type="theorem")

    response = client.get(reverse("knowledge-card-list"), {"subject": subject.pk, "type": "definition"})

    assert response.status_code == 200
    content = response.content.decode()
    assert "极限定义" in content
    assert "代数定理" not in content


@pytest.mark.django_db
def test_invalid_knowledge_card_form_returns_errors(client):
    response = client.post(reverse("knowledge-card-create"), {"name": "", "type": ""})

    assert response.status_code == 200
    content = response.content.decode()
    assert "知识卡片名称不能为空" in content or "该字段是必填项" in content
    assert KnowledgeCard.objects.count() == 0
