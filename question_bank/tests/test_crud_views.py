import io

import pytest
from django.test import override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from question_bank.models import KnowledgeCard, Question, QuestionAttachment, Section, Subject, Tag


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
    response = client.post(reverse("question-create"), {"save_intent": "draft"})

    assert response.status_code == 302
    question = Question.objects.get()
    assert question.draft is True
    assert question.title == ""


@pytest.mark.django_db
def test_formal_question_requires_subject_title_or_statement(client):
    response = client.post(reverse("question-create"), {"save_intent": "publish"})

    assert response.status_code == 200
    assert "正式题目至少需要科目、标题或题干中的一项" in response.content.decode()
    assert Question.objects.count() == 0


@pytest.mark.django_db
def test_invalid_create_with_upload_warns_to_reselect_file(client):
    response = client.post(reverse("question-create"), {
        "save_intent": "publish", "attachments": image_file("unsaved.png", (1, 2, 3)),
    })

    assert response.status_code == 200
    assert response.context["form"].errors
    assert response.context["needs_upload_reselection"] is True
    assert "请重新选择新文件" in response.content.decode()
    assert Question.objects.count() == 0


@pytest.mark.django_db
def test_invalid_upload_error_names_file_and_reason_in_summary(client):
    broken_upload = SimpleUploadedFile(
        "broken.png", b"not an image", content_type="image/png"
    )

    response = client.post(
        reverse("question-create"),
        {"save_intent": "publish", "attachments": broken_upload},
    )

    assert response.status_code == 200
    error = response.context["form"].errors["attachments"][0]
    assert "broken.png" in error
    assert "上传文件不是有效图片" in error
    body = response.content.decode()
    assert "broken.png" in body
    assert "上传文件不是有效图片" in body


@pytest.mark.django_db
def test_bound_upload_error_explains_browser_file_reset_even_for_field_error(client, subject):
    response = client.post(reverse("question-create"), {
        "save_intent": "publish",
        "subject": str(subject.pk),
        "title": "待修正题目",
        "mastery": "invalid",
        "attachments": image_file("valid.png", (1, 2, 3)),
    })

    assert response.status_code == 200
    assert response.context["form"].errors
    assert response.context["needs_upload_reselection"] is True
    body = response.content.decode()
    assert "浏览器不会保留文件输入" in body
    assert "请重新选择新文件" in body


@pytest.mark.django_db
def test_invalid_save_intent_preserves_submitted_intent(client):
    response = client.post(reverse("question-create"), {"save_intent": "archive"})

    assert response.status_code == 200
    assert response.context["submitted_intent"] == "archive"
    assert "保存意图无效" in response.content.decode()


@pytest.mark.django_db
def test_missing_save_intent_preserves_submitted_intent(client):
    response = client.post(reverse("question-create"), {})

    assert response.status_code == 200
    assert response.context["submitted_intent"] == ""
    assert "保存意图无效" in response.content.decode()


@pytest.mark.django_db
def test_question_form_exposes_draft_and_publish_save_intents(client, subject):
    create_response = client.get(reverse("question-create"))
    create_content = create_response.content.decode()
    assert 'name="save_intent"' in create_content
    assert 'value="draft"' in create_content
    assert 'value="publish"' in create_content
    assert 'name="attachment_protocol" value="enhanced" disabled' in create_content

    question = Question.objects.create(subject=subject, title="待编辑")
    edit_response = client.get(reverse("question-edit", args=[question.pk]))
    edit_content = edit_response.content.decode()
    assert edit_content.count('name="save_intent"') >= 2
    assert 'value="draft"' in edit_content
    assert 'value="publish"' in edit_content


@pytest.mark.django_db
def test_published_question_can_be_explicitly_saved_as_draft(client, subject):
    question = Question.objects.create(subject=subject, title="旧题目", draft=False)
    token = client.get(reverse("question-edit", args=[question.pk])).context["question_version"]

    response = client.post(
        reverse("question-edit", args=[question.pk]),
        {"save_intent": "draft", "title": "暂存题目", "question_version": token},
    )

    assert response.status_code == 302
    question.refresh_from_db()
    assert question.draft is True
    assert question.title == "暂存题目"


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
            "save_intent": "publish",
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
    token = client.get(reverse("question-edit", args=[question.pk])).context["question_version"]

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
            "save_intent": "publish",
            "question_version": token,
        },
    )

    assert response.status_code == 302
    question.refresh_from_db()
    assert question.title == "新标题"
    assert question.mastery == "struggling"
    assert list(question.knowledge_cards.all()) == [card]


@pytest.mark.django_db
def test_create_enhanced_order_and_redirect(client, subject, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        response = client.post(reverse("question-create"), {
            "subject": str(subject.pk), "save_intent": "publish", "title": "排序",
            "attachment_protocol": "enhanced", "attachment_order": ["new:1", "new:0"],
            "attachments": [image_file("first.png", (255, 0, 0)), image_file("second.png", (0, 0, 255))],
        })
        question = Question.objects.get(title="排序")
        assert response.status_code == 302
        assert response.url == f'{reverse("question-edit", args=[question.pk])}?saved=1'
        assert [item.file.read() for item in question.attachments.all()] == [
            image_file("second.png", (0, 0, 255)).read(),
            image_file("first.png", (255, 0, 0)).read(),
        ]


@pytest.mark.django_db
def test_apply_attachment_plan_validation_error_returns_bound_form(client, subject, monkeypatch):
    def fail_plan(*args, **kwargs):
        from question_bank.attachments import AttachmentPlanValidationError

        raise AttachmentPlanValidationError("附件计划已失效。")

    monkeypatch.setattr("question_bank.views.apply_attachment_plan", fail_plan)

    response = client.post(reverse("question-create"), {
        "subject": str(subject.pk),
        "title": "执行期附件错误",
        "save_intent": "publish",
    })

    assert response.status_code == 200
    assert "附件计划已失效。" in response.content.decode()
    assert response.context["form"].errors
    assert not Question.objects.filter(title="执行期附件错误").exists()


@pytest.mark.django_db
def test_edit_fallback_removes_and_orders_mixed_attachment_types(client, subject, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        question = Question.objects.create(subject=subject, title="旧题")
        removed = QuestionAttachment.objects.create(question=question, file=image_file("removed.png", (1, 1, 1)), sort_order=0)
        retained = QuestionAttachment.objects.create(question=question, file=SimpleUploadedFile("notes.pdf", b"pdf"), file_kind="document", sort_order=1)
        token = client.get(reverse("question-edit", args=[question.pk])).context["question_version"]
        response = client.post(reverse("question-edit", args=[question.pk]), {
            "question_version": token, "save_intent": "draft", "title": "新题",
            "remove_attachment": str(removed.pk), f"attachment_position_{retained.pk}": "3",
            "attachments": image_file("added.png", (1, 2, 3)),
        })
        assert response.status_code == 302
        assert response.url == f'{reverse("question-edit", args=[question.pk])}?saved=1'
        question.refresh_from_db()
        assert question.draft and question.title == "新题"
        assert list(question.attachments.values_list("file_kind", "sort_order")) == [
            ("document", 0), ("image", 1),
        ]


@pytest.mark.django_db
def test_two_editor_conflict_preserves_fields_relations_and_attachments(client, subject, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        question = Question.objects.create(subject=subject, title="原题")
        tag = Tag.objects.create(name="标签")
        attachment = QuestionAttachment.objects.create(question=question, file=image_file("old.png", (1, 1, 1)), sort_order=0)
        url = reverse("question-edit", args=[question.pk])
        first = client.get(url)
        second = client.get(url)
        old_token = second.context["question_version"]
        assert first.context["question_version"] == old_token
        assert 'name="question_version"' in first.content.decode()
        assert all(key in first.context for key in (
            "question", "question_version", "existing_attachments", "submitted_intent", "form"
        ))

        saved = client.post(url, {
            "question_version": old_token, "save_intent": "publish", "title": "第一版",
            f"attachment_position_{attachment.pk}": "0", "tags": [str(tag.pk)],
        })
        assert saved.status_code == 302
        stale = client.post(url, {
            "question_version": old_token, "save_intent": "draft", "title": "过期修改",
            "remove_attachment": str(attachment.pk), "attachments": image_file("stale.png", (2, 2, 2)),
        })
        assert stale.status_code == 409
        assert stale.context["conflict"] is True
        assert stale.context["submitted_intent"] == "draft"
        assert stale.context["form"].errors
        assert stale.context["question_version"] != old_token
        assert [item.pk for item in stale.context["existing_attachments"]] == [attachment.pk]
        question.refresh_from_db()
        assert question.title == "第一版" and not question.draft
        assert list(question.tags.all()) == [tag]
        assert list(question.attachments.values_list("pk", flat=True)) == [attachment.pk]


@pytest.mark.django_db
def test_invalid_edit_keeps_context_and_attachment_plan_does_not_write(client, subject):
    question = Question.objects.create(subject=subject, title="原题")
    attachment = QuestionAttachment.objects.create(question=question, file="questions/old.png", sort_order=0)
    url = reverse("question-edit", args=[question.pk])
    token = client.get(url).context["question_version"]
    response = client.post(url, {
        "question_version": token, "save_intent": "publish", "title": "改动",
        "attachment_protocol": "enhanced", "attachment_order": ["existing:999"],
    })
    assert response.status_code == 200
    assert response.context["form"].errors
    assert response.context["submitted_intent"] == "publish"
    assert response.context["question"].pk == question.pk
    assert response.context["question_version"] == token
    assert [item.pk for item in response.context["existing_attachments"]] == [attachment.pk]
    question.refresh_from_db()
    assert question.title == "原题"


@pytest.mark.django_db
def test_invalid_edit_echoes_fallback_controls_and_upload_warning(client, subject):
    question = Question.objects.create(subject=subject, title="原题")
    first = QuestionAttachment.objects.create(question=question, file="questions/a.png", sort_order=0)
    second = QuestionAttachment.objects.create(question=question, file="questions/b.png", sort_order=1)
    url = reverse("question-edit", args=[question.pk])
    token = client.get(url).context["question_version"]

    response = client.post(url, {
        "question_version": token, "save_intent": "invalid", "title": "待修正",
        f"attachment_position_{first.pk}": "12", f"attachment_position_{second.pk}": "4",
        "remove_attachment": str(first.pk),
        "attachments": image_file("unsaved.png", (3, 4, 5)),
    })

    assert response.status_code == 200
    controls = {item["attachment"].pk: item for item in response.context["attachment_controls"]}
    assert controls[first.pk]["position"] == "12"
    assert controls[first.pk]["removed"] is True
    assert controls[second.pk]["position"] == "4"
    assert controls[second.pk]["removed"] is False
    body = response.content.decode()
    assert f'name="attachment_position_{first.pk}" value="12"' in body
    assert f'name="attachment_position_{second.pk}" value="4"' in body
    assert f'name="remove_attachment" value="{first.pk}" checked' in body
    assert f'name="remove_attachment" value="{second.pk}" checked' not in body
    assert "请重新选择新文件" in body
    assert response.context["needs_upload_reselection"] is True
    question.refresh_from_db()
    assert question.title == "原题"


@pytest.mark.django_db
def test_conflict_echoes_controls_and_cannot_resubmit_from_rendered_form(client, subject):
    question = Question.objects.create(subject=subject, title="原题")
    attachment = QuestionAttachment.objects.create(question=question, file="questions/old.png", sort_order=0)
    url = reverse("question-edit", args=[question.pk])
    stale_token = client.get(url).context["question_version"]
    question.title = "服务器版本"
    question.save()

    submission = {
        "question_version": stale_token, "save_intent": "publish", "title": "本地版本",
        f"attachment_position_{attachment.pk}": "7", "remove_attachment": str(attachment.pk),
        "attachments": image_file("unsaved.png", (5, 4, 3)),
    }
    response = client.post(url, submission)
    assert response.status_code == 409
    controls = response.context["attachment_controls"]
    assert controls[0]["position"] == "7" and controls[0]["removed"] is True
    body = response.content.decode()
    assert f'name="attachment_position_{attachment.pk}" value="7"' in body
    assert f'name="remove_attachment" value="{attachment.pk}" checked' in body
    assert "请重新选择新文件" in body
    assert 'name="question_version"' not in body
    assert 'href="' + url + '"' in body
    assert 'data-preserve-local-draft' in body
    assert 'data-reload-server' in body
    assert 'name="save_intent" value="publish" disabled' in body

    submission["attachments"] = image_file("again.png", (1, 1, 1))
    repeated = client.post(url, submission)
    assert repeated.status_code == 409
    rendered_form_submission = client.post(url, {
        "title": "本地版本", f"attachment_position_{attachment.pk}": "7",
        "remove_attachment": str(attachment.pk),
    })
    assert rendered_form_submission.status_code == 409
    question.refresh_from_db()
    assert question.title == "服务器版本"
    assert list(question.attachments.values_list("pk", flat=True)) == [attachment.pk]


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
def test_knowledge_card_form_uses_compact_core_content_editor(client, subject, section):
    response = client.get(reverse("knowledge-card-create"))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'class="knowledge-card-workbench"' in body
    assert 'name="core_content"' in body
    assert 'data-kc-editor' in body
    assert 'data-kc-rendered' in body
    assert 'data-kc-insert="heading"' in body
    assert 'data-kc-insert="formula"' in body
    assert 'name="formal_statement"' not in body
    assert 'name="conditions"' not in body
    assert 'name="proof"' not in body
    assert 'name="usage_signals"' not in body
    assert 'name="common_mistakes"' not in body


@pytest.mark.django_db
def test_knowledge_card_meta_fields_allow_free_text_with_existing_suggestions(client, subject, section):
    response = client.get(reverse("knowledge-card-create"))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'name="subject"' in body and 'type="text"' in body
    assert 'name="section"' in body and 'type="text"' in body
    assert 'name="type"' in body and 'type="text"' in body
    assert 'list="subject-options"' in body
    assert 'list="section-options"' in body
    assert 'list="type-options"' in body
    assert 'value="数学分析"' in body
    assert 'value="极限与连续"' in body
    assert 'value="定理"' in body


@pytest.mark.django_db
def test_knowledge_card_creation_creates_new_subject_section_and_type_from_text(client):
    response = client.post(
        reverse("knowledge-card-create"),
        {
            "name": "自定义知识点",
            "subject": "实变函数",
            "section": "测度与积分",
            "type": "核心结论",
            "core_content": "内容",
        },
    )

    assert response.status_code == 302
    subject = Subject.objects.get(name="实变函数")
    section = Section.objects.get(subject=subject, name="测度与积分")
    card = KnowledgeCard.objects.get(name="自定义知识点")
    assert card.subject == subject
    assert card.section == section
    assert card.type == "核心结论"


@pytest.mark.django_db
def test_invalid_knowledge_card_does_not_create_subject_or_section(client):
    response = client.post(
        reverse("knowledge-card-create"),
        {"name": "", "subject": "新科目", "section": "新章节", "type": "定义"},
    )

    assert response.status_code == 200
    assert not Subject.objects.filter(name="新科目").exists()
    assert not Section.objects.filter(name="新章节").exists()


@pytest.mark.django_db
def test_existing_knowledge_card_post_ids_reuse_subject_and_section(client, subject, section):
    response = client.post(
        reverse("knowledge-card-create"),
        {"name": "旧表单兼容", "subject": str(subject.pk), "section": str(section.pk), "type": "theorem"},
    )

    assert response.status_code == 302
    card = KnowledgeCard.objects.get(name="旧表单兼容")
    assert card.subject == subject
    assert card.section == section
    assert card.type == "theorem"
    assert Subject.objects.count() == 1


@pytest.mark.django_db
def test_predefined_type_label_reuses_existing_filter_value(client, subject):
    response = client.post(
        reverse("knowledge-card-create"),
        {"name": "新定理", "subject": subject.name, "type": "定理"},
    )

    assert response.status_code == 302
    card = KnowledgeCard.objects.get(name="新定理")
    assert card.type == "theorem"
    assert card.get_type_display() == "定理"
    listing = client.get(reverse("knowledge-card-list"), {"type": "theorem"})
    assert "新定理" in listing.content.decode()


@pytest.mark.django_db
def test_custom_type_appears_in_filter_options(client, subject):
    KnowledgeCard.objects.create(name="新分类卡", subject=subject, type="核心结论")

    response = client.get(reverse("knowledge-card-list"), {"type": "核心结论"})

    assert response.status_code == 200
    assert "新分类卡" in response.content.decode()
    assert '<option value="核心结论" selected>' in response.content.decode()


@pytest.mark.django_db
def test_knowledge_card_core_content_is_split_into_legacy_sections(client, subject):
    response = client.post(
        reverse("knowledge-card-create"),
        {
            "name": "拉格朗日中值定理",
            "subject": str(subject.pk),
            "type": "theorem",
            "core_content": (
                "## 定理内容\n\n正式表述。\n\n"
                "## 使用条件\n\n连续且可导。\n\n"
                "## 证明\n\n利用辅助函数。\n\n"
                "## 使用信号\n\n看到存在一点。\n\n"
                "## 常见错误\n\n漏写区间条件。"
            ),
            "personal_notes": "记忆口诀",
        },
    )

    assert response.status_code == 302
    card = KnowledgeCard.objects.get(name="拉格朗日中值定理")
    assert card.formal_statement == "正式表述。"
    assert card.conditions == "连续且可导。"
    assert card.proof == "利用辅助函数。"
    assert card.usage_signals == "看到存在一点。"
    assert card.common_mistakes == "漏写区间条件。"
    assert card.personal_notes == "记忆口诀"


@pytest.mark.django_db
def test_knowledge_card_editor_prefills_compact_content_from_existing_sections(client, subject):
    card = KnowledgeCard.objects.create(
        name="已有卡片",
        subject=subject,
        type="theorem",
        formal_statement="定理内容",
        conditions="成立条件",
        proof="证明过程",
    )

    response = client.get(reverse("knowledge-card-edit", args=[card.pk]))

    assert response.status_code == 200
    core_content = response.context["form"]["core_content"].value()
    assert "## 定理内容" in core_content
    assert "定理内容" in core_content
    assert "## 使用条件" in core_content
    assert "成立条件" in core_content
    assert "## 证明" in core_content
    assert "证明过程" in core_content


@pytest.mark.django_db
def test_knowledge_card_preserves_custom_core_headings_and_order(client, subject):
    content = "## 想法\n\n先看单调性。\n\n## 公式\n\n$$a^2+b^2$$\n\n## 提醒\n\n检查端点。"
    created = client.post(reverse("knowledge-card-create"), {
        "name": "自定义内容", "subject": str(subject.pk), "type": "other",
        "core_content": content,
    })

    assert created.status_code == 302
    card = KnowledgeCard.objects.get(name="自定义内容")
    assert card.core_content == content
    edited = client.get(reverse("knowledge-card-edit", args=[card.pk]))
    assert edited.context["form"]["core_content"].value() == content
    detail = client.get(reverse("knowledge-card-detail", args=[card.pk]))
    body = detail.content.decode()
    assert body.index("想法") < body.index("公式") < body.index("提醒")


@pytest.mark.django_db
def test_knowledge_card_preserves_core_content_boundary_whitespace(client, subject):
    content = "\n\n## 定理内容\n\n内容。  \n\n"

    response = client.post(reverse("knowledge-card-create"), {
        "name": "空白保留", "subject": str(subject.pk), "type": "theorem",
        "core_content": content,
    })

    assert response.status_code == 302
    assert KnowledgeCard.objects.get(name="空白保留").core_content == content


@pytest.mark.django_db
def test_knowledge_card_legacy_sections_ignore_headings_inside_code_fences(client, subject):
    content = "## 定理内容\n\n```text\n## 使用条件\n示例文本\n```\n\n正文。"

    response = client.post(reverse("knowledge-card-create"), {
        "name": "代码块", "subject": str(subject.pk), "type": "theorem",
        "core_content": content,
    })

    assert response.status_code == 302
    card = KnowledgeCard.objects.get(name="代码块")
    assert card.conditions == ""
    assert "## 使用条件" in card.formal_statement


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
