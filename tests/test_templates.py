import io
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from question_bank.models import (
    KnowledgeCard,
    Question,
    QuestionAttachment,
    Section,
    Subject,
    Tag,
)


@pytest.fixture
def subject(db):
    return Subject.objects.create(name="数学分析")


@pytest.fixture
def question(db, subject):
    return Question.objects.create(
        subject=subject,
        title="极限题",
        statement="求极限 $x^2$",
        reference_solution="设 $x=0$",
        draft=False,
    )


def image_file(name, color):
    stream = io.BytesIO()
    Image.new("RGB", (4, 4), color=color).save(stream, format="PNG")
    return SimpleUploadedFile(name, stream.getvalue(), content_type="image/png")


def test_question_analysis_template_has_versioned_post_contract():
    template = Path("question_bank/templates/question_bank/question_analysis.html").read_text(encoding="utf-8")

    assert 'name="analysis_version"' in template
    assert 'name="input_fingerprint"' in template
    assert "data-question-analysis" in template
    assert "question-analysis.js" in template
    assert "question-analysis-review" in template
    assert 'name="candidate_key"' in template
    assert "确认关联" in template


def test_question_analysis_template_uses_sanitized_analysis_summary():
    template = Path("question_bank/templates/question_bank/question_analysis.html").read_text(
        encoding="utf-8"
    )

    assert "analysis_summary" in template
    assert "latest_analysis" not in template
    assert "raw_response" not in template
    assert "api_key" not in template.casefold()


@pytest.mark.django_db
def test_question_list_exposes_study_desk_navigation_search_and_mathjax(client, subject):
    response = client.get(reverse("question-list"), {"q": "极限", "subject": subject.pk})

    assert response.status_code == 200
    body = response.content.decode()
    assert 'aria-label="主导航"' in body
    assert 'href="/"' in body
    assert 'href="/questions/new/"' in body
    assert 'href="/knowledge-cards/"' in body
    assert 'id="question-filter-form"' in body
    assert 'type="search"' in body
    assert 'aria-label="移除筛选条件：关键词: 极限"' in body
    assert 'mathjax@3/es5/tex-mml-chtml.js' in body
    assert 'question_bank/css/app.css' in body
    assert 'question_bank/js/app.js' in body


@pytest.mark.django_db
def test_question_list_prepares_safe_full_statement_and_readable_filter_labels(client, subject):
    section = Section.objects.create(subject=subject, name="函数极限")
    tag = Tag.objects.create(name="夹逼准则", kind="method")
    card = KnowledgeCard.objects.create(
        name="函数极限定义",
        subject=subject,
        section=section,
        type="definition",
    )
    item = Question.objects.create(
        subject=subject,
        section=section,
        title="完整题干",
        statement="**重点** $x^2$ <script>alert('x')</script>",
        mastery="unstable",
        draft=False,
    )
    item.tags.add(tag)
    item.knowledge_cards.add(card)

    response = client.get(
        reverse("question-list"),
        {
            "subject": subject.pk,
            "section": section.pk,
            "tag": tag.pk,
            "knowledge_card": card.pk,
            "mastery": "unstable",
        },
    )

    listed = list(response.context["questions"])[0]
    labels = {filter_item["label"] for filter_item in response.context["active_filters"]}
    assert "<strong>重点</strong>" in listed.statement_html
    assert "$x^2$" in listed.statement_html
    assert "<script" not in listed.statement_html.lower()
    assert labels == {
        "科目: 数学分析",
        "章节: 函数极限",
        "标签: 夹逼准则",
        "知识卡片: 函数极限定义",
        "掌握程度: 可以完成但不稳定",
    }

    due_response = client.get(reverse("question-list"), {"due": "true"})
    assert [item["label"] for item in due_response.context["active_filters"]] == [
        "到期: 仅显示到期题目"
    ]

    mixed_due_response = client.get(
        reverse("question-list"), [("due", "0"), ("overdue", "1")]
    )
    assert mixed_due_response.context["selected_due"] is False
    assert all(
        item["key"] != "due" for item in mixed_due_response.context["active_filters"]
    )


@pytest.mark.django_db
def test_question_list_prefetches_ordered_attachments(
    client, subject, settings, tmp_path, django_assert_num_queries
):
    settings.MEDIA_ROOT = tmp_path / "media"
    item = Question.objects.create(subject=subject, title="多图题", draft=False)
    QuestionAttachment.objects.create(
        question=item,
        file=image_file("second.png", "blue"),
        sort_order=1,
    )
    QuestionAttachment.objects.create(
        question=item,
        file=image_file("first.png", "white"),
        sort_order=0,
    )

    response = client.get(reverse("question-list"))
    listed = list(response.context["questions"])[0]

    with django_assert_num_queries(0):
        attachments = list(listed.attachments.all())
    assert [attachment.sort_order for attachment in attachments] == [0, 1]


@pytest.mark.django_db
def test_question_list_builds_page_free_pagination_query(client, subject):
    tag = Tag.objects.create(name="分页标签")
    for index in range(21):
        item = Question.objects.create(
            subject=subject,
            title=f"分页题 {index:02d}",
            statement="分页关键词",
            draft=False,
        )
        item.tags.add(tag)

    response = client.get(
        f"{reverse('question-list')}?q=分页关键词&subject={subject.pk}&tag={tag.pk}&page=1&page=2"
    )

    query = parse_qs(response.context["pagination_query"])
    assert "page" not in query
    assert query == {
        "q": ["分页关键词"],
        "subject": [str(subject.pk)],
        "tag": [str(tag.pk)],
    }


@pytest.mark.django_db
def test_question_list_uses_top_filters_and_full_statement_without_detail_content(
    client, subject, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path / "media"
    item = Question.objects.create(
        subject=subject,
        title="结果页内容边界",
        statement="**完整题干标记** $x_n \\to 0$",
        personal_solution="个人解答机密文本",
        reference_solution="参考解答机密文本",
        error_note="错误记录机密文本",
        draft=False,
    )
    QuestionAttachment.objects.create(
        question=item,
        file=image_file("statement.png", "white"),
        sort_order=0,
    )

    response = client.get(reverse("question-list"), {"subject": subject.pk})

    body = response.content.decode()
    assert 'class="filter-panel"' in body
    assert 'class="filter-panel__search"' in body
    assert 'class="filter-group"' in body
    assert 'id="question-results"' in body
    assert 'class="question-result-card"' in body
    assert 'class="question-statement"' in body
    assert 'class="question-attachments"' in body
    assert 'data-clear-filters' in body
    assert 'data-filter-key=' in body
    assert "<strong>完整题干标记</strong>" in body
    assert "个人解答机密文本" not in body
    assert "参考解答机密文本" not in body
    assert "错误记录机密文本" not in body


@pytest.mark.django_db
def test_question_list_renders_bounded_native_tag_picker_contract(client, subject):
    category = Tag.objects.create(name="方法")
    child = Tag.objects.create(name="夹逼", parent=category)
    selected = Tag.objects.create(name="已选")
    question = Question.objects.create(subject=subject, title="标签题", draft=False)
    question.tags.add(child)

    response = client.get(
        reverse("question-list"),
        {"subject": subject.pk, "tag": selected.pk, "tag_picker_q": "夹"},
    )

    body = response.content.decode()
    assert 'data-tag-picker' in body
    assert 'name="tag_picker_q"' in body
    assert f'data-tag-suggestions-url="{reverse("tag-suggestions")}"' in body
    assert 'data-selected-tags=' in body
    assert 'data-common-tags=' in body
    assert 'data-tag-category=' in body
    assert 'data-tag-count=' in body
    assert 'data-category-expand' in body
    assert 'data-tag-category-panel' in body
    assert '<fieldset class="tag-picker__selected">' in body
    assert 'class="tag-picker__chip"' in body
    assert 'class="tag-picker__search-submit"' in body
    assert 'class="tag-picker__search-status"' in body
    assert 'role="status" aria-live="polite"' in body
    assert 'All in 方法' in body
    assert f'name="tag" value="{selected.pk}"' in body
    assert 'data-tag-count="0"' in body
    assert 'href="/tags/"' in body
    assert body.index('tag-picker__selected') < body.index('tag-picker__search')
    assert body.index('tag-picker__search') < body.index('tag-picker__common')
    assert body.index('tag-picker__common') < body.index('tag-picker__category')


def test_question_list_css_defines_bounded_tag_picker_layout():
    css = (Path(__file__).parents[1] / "static/question_bank/css/app.css").read_text(
        encoding="utf-8"
    )

    for selector in (
        ".tag-picker", ".tag-picker__selected", ".tag-picker__common",
        ".tag-picker__category", ".tag-picker__count",
    ):
        assert selector in css
    assert "overflow-wrap:anywhere" in css.replace(" ", "")
    assert "min-height:40px" in css.replace(" ", "")
    assert ".tag-picker__category-panel" in css


@pytest.mark.django_db
def test_question_list_omits_image_region_when_question_has_no_images(
    client, subject, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path / "media"
    item = Question.objects.create(subject=subject, title="仅文档附件题", draft=False)
    QuestionAttachment.objects.create(
        question=item,
        file=SimpleUploadedFile("notes.txt", b"notes", content_type="text/plain"),
        file_kind="document",
        sort_order=0,
    )

    response = client.get(reverse("question-list"), {"q": "仅文档附件题"})

    assert 'class="question-attachments"' not in response.content.decode()


@pytest.mark.django_db
def test_question_list_never_previews_solution_images(client, subject, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    item = Question.objects.create(subject=subject, title="隐藏解答图", draft=False)
    QuestionAttachment.objects.create(
        question=item, file=image_file("answer.png", "white"),
        attachment_role="solution", sort_order=0,
    )

    response = client.get(reverse("question-list"), {"q": "隐藏解答图"})

    assert response.status_code == 200
    assert response.context["questions"][0].image_attachments == []
    assert 'class="question-attachments"' not in response.content.decode()


def test_search_page_css_defines_layout_and_responsive_contract():
    css = (Path(__file__).parents[1] / "static/question_bank/css/app.css").read_text(
        encoding="utf-8"
    )

    assert ".filter-panel" in css
    assert ".filter-row" in css
    assert ".filter-option" in css
    assert ".question-result-card" in css
    assert ".question-attachments" in css
    assert "@media(max-width:980px)" in css.replace(" ", "")
    assert "@media(max-width:760px)" in css.replace(" ", "")


@pytest.mark.django_db
def test_filter_interaction_hooks_and_shared_script(client):
    response = client.get(
        reverse("question-list"),
        {
            "keyword": "极限",
            "masteries": "unstarted,mastered",
            "overdue": "1",
        },
    )
    body = response.content.decode()
    javascript = (Path(__file__).parents[1] / "static/question_bank/js/app.js").read_text(
        encoding="utf-8"
    )

    assert 'data-filter-key="q"' in body
    assert 'data-filter-value="极限"' in body
    assert 'data-filter-aliases="q,keyword"' in body
    assert 'name="mastery" value="unstarted" checked' in body
    assert 'name="mastery" value="mastered" checked' in body
    assert 'name="due" value="1" checked' in body
    assert "到期: 仅显示到期题目" in body
    assert 'data-clear-filters' in body
    assert 'id="save-filter-status"' in body
    assert 'aria-live="polite"' in body
    assert "[data-filter-key]" in javascript
    assert "new URL(window.location.href)" in javascript
    assert "if (key === 'q')" in javascript
    assert "split(',')" in javascript
    assert "searchParams.delete('page')" in javascript
    assert "math-question-bank:saved-filters" in javascript
    assert "save-filter-status" in javascript


@pytest.mark.django_db
def test_base_has_keyboard_labels_mobile_structure_and_collapsible_assistant(client):
    response = client.get(reverse("question-list"))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'class="app-shell"' in body
    assert 'class="mobile-toolbar"' in body
    assert 'aria-label="打开学习助手"' in body
    assert 'aria-controls="assistant-panel"' in body
    assert 'id="assistant-panel"' in body
    assert 'data-collapsible-panel' in body
    assert 'alt="学习助手占位图"' in body


@pytest.mark.django_db
def test_question_detail_marks_images_for_keyboard_accessible_preview(client, question):
    response = client.get(reverse("question-detail", args=[question.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'aria-label="题目详情"' in body
    assert 'data-image-preview' in body
    assert 'aria-label="放大查看题目附件 1"' in body
    assert 'data-solution-reveal' in body


@pytest.mark.django_db
def test_review_detail_keeps_reference_solution_reachable_without_javascript(client, question):
    response = client.get(reverse("review-detail", args=[question.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'href="?show_reference=1"' in body
    assert 'data-solution-reveal' in body
    assert 'aria-label="显示参考解答"' in body


@pytest.mark.django_db
def test_question_form_has_image_preview_hook(client):
    response = client.get(reverse("question-create"))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'enctype="multipart/form-data"' in body
    assert 'data-image-input' in body
    assert 'aria-live="polite"' in body


@pytest.mark.django_db
def test_question_workbench_renders_progressive_editor_contract(client):
    response = client.get(reverse("question-create"))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'class="question-workbench"' in body
    assert 'data-question-form' in body
    assert 'data-form-version="1"' in body
    assert 'data-mode="quick"' in body
    assert 'data-form-mode="quick"' in body
    assert 'aria-pressed="true"' in body
    assert 'data-form-mode="complete"' in body
    assert 'class="attachment-dropzone"' in body
    assert 'class="attachment-queue"' in body
    assert 'class="question-form-tabs"' in body
    assert body.count('role="tab"') == 3
    assert body.count('role="tabpanel"') == 3
    assert body.count('<fieldset class="form-fieldset">') == 3
    assert '<legend>题目信息</legend>' in body
    assert '<legend>解答与复习</legend>' in body
    assert '<legend>关联内容</legend>' in body
    assert 'class="markdown-preview"' in body
    assert 'data-draft-recovery' in body
    assert 'data-draft-status' in body
    assert 'data-preview-status' in body
    assert 'data-subject-select' in body
    assert 'data-section-select' in body
    assert 'data-relation-search="tags"' in body
    assert 'data-relation-search="knowledge_cards"' in body
    assert 'name="save_intent" value="draft"' in body
    assert 'name="save_intent" value="publish"' in body
    assert 'question_bank/css/question-form.css' in body
    assert 'type="module"' in body
    assert 'question_bank/js/question-form.js' in body
    for name in (
        "subject", "section", "title", "statement", "personal_solution",
        "reference_solution", "error_note", "mastery", "tags",
        "knowledge_cards", "attachments", "solution_attachments",
    ):
        assert f'name="{name}"' in body
    assert "题目图片" in body
    assert "解答图片" in body
    assert 'name="attachment_protocol" value="enhanced" disabled' in body
    assert 'name="attachment_order"' not in body
    assert 'disabled data-ocr' in body
    assert "后续开放" in body


@pytest.mark.django_db
def test_question_workbench_preserves_existing_attachment_types_and_fallback(client, subject):
    item = Question.objects.create(subject=subject, title="混合附件")
    image = QuestionAttachment.objects.create(
        question=item, file="questions/figure.png", file_kind="image", sort_order=0
    )
    document = QuestionAttachment.objects.create(
        question=item, file="questions/notes.pdf", file_kind="document", sort_order=1
    )
    other = QuestionAttachment.objects.create(
        question=item, file="questions/archive.bin", file_kind="other", sort_order=2
    )

    response = client.get(reverse("question-edit", args=[item.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'name="question_version"' in body
    for attachment in (image, document, other):
        assert attachment.file.name in body
        assert f'name="attachment_position_{attachment.pk}"' in body
        assert f'name="remove_attachment" value="{attachment.pk}"' in body
        assert f'data-attachment-id="{attachment.pk}"' in body
    assert 'data-file-kind="image"' in body
    assert 'data-file-kind="document"' in body
    assert 'data-file-kind="other"' in body
    assert body.count('data-image-zoom') == 1
    assert 'data-attachment-move="up"' in body
    assert 'data-attachment-move="down"' in body
    assert 'data-attachment-drag-handle' in body


@pytest.mark.django_db
def test_question_workbench_shows_error_summary_and_conflict_actions(client, subject):
    invalid = client.post(reverse("question-create"), {"save_intent": "publish"})
    assert invalid.status_code == 200
    invalid_body = invalid.content.decode()
    assert 'class="form-error-summary"' in invalid_body
    assert 'href="#id_title"' in invalid_body

    item = Question.objects.create(subject=subject, title="服务器题目")
    url = reverse("question-edit", args=[item.pk])
    conflict = client.post(url, {
        "save_intent": "publish", "question_version": "stale", "title": "本地题目"
    })
    assert conflict.status_code == 409
    conflict_body = conflict.content.decode()
    assert 'data-conflict' in conflict_body
    assert 'data-preserve-local-draft' in conflict_body
    assert 'name="question_version"' not in conflict_body
    assert 'name="save_intent" value="publish" disabled' in conflict_body


def test_question_workbench_styles_define_image_first_responsive_layout():
    css = (Path(__file__).parents[1] / "static/question_bank/css/question-form.css").read_text(
        encoding="utf-8"
    )

    for selector in (
        ".question-workbench", ".attachment-dropzone", ".attachment-queue",
        ".question-form-tabs", ".markdown-preview",
    ):
        assert selector in css
    assert "grid-template-columns" in css
    assert "@media(max-width:980px)" in css.replace(" ", "")
    assert ".question-workbench.is-enhanced ~ .form-mode-control" not in css
    assert "body:has(.question-workbench.is-enhanced) .form-mode-control" in css


def test_question_workbench_scripts_exist():
    root = Path(__file__).parents[1] / "static/question_bank/js"
    for name in ("question-form.js", "question-form-editor.js", "question-form-attachments.js"):
        assert (root / name).is_file(), name


def test_shared_preview_keeps_other_pages_and_skips_question_workbench():
    source = (Path(__file__).parents[1] / "static/question_bank/js/app.js").read_text(
        encoding="utf-8"
    )
    assert "input[type=file][data-preview]" in source
    assert "input.closest('[data-question-form]')" in source


def test_tag_picker_script_contract_supports_progressive_search_and_navigation():
    source = (Path(__file__).parents[1] / "static/question_bank/js/app.js").read_text(
        encoding="utf-8"
    )

    assert "[data-tag-picker]" in source
    assert "setTimeout" in source and "200" in source
    assert 'credentials: "same-origin"' in source
    assert "AbortController" in source
    assert "data-selected-tags" in source
    assert "selected_tag" in source
    assert "Math.min" in source and "20" in source
    assert "document.createElement" in source
    assert "textContent" in source
    assert "tag-picker-search-status" in source
    assert "加载中" in source and "失败" in source
    assert "keydown" in source
    assert "ArrowDown" in source and "ArrowUp" in source
    assert "DOMParser" in source
    assert "data-tag-category-panel" in source
    assert "aria-expanded" in source and "aria-controls" in source
    assert "data-tag-remove" in source


def test_tag_picker_script_keeps_native_category_links_on_enhancement_failure():
    source = (Path(__file__).parents[1] / "static/question_bank/js/app.js").read_text(
        encoding="utf-8"
    )

    assert "preventDefault" in source
    assert "location.assign" in source or "window.location" in source
    assert "data-tag-category" in source


@pytest.mark.django_db
def test_question_workbench_section_suggestions_expose_subject(client, subject):
    section = Section.objects.create(subject=subject, name="极限")
    response = client.get(reverse("question-create"))
    body = response.content.decode()
    assert f'value="{section.name}" label="{subject.name}"' in body


@pytest.mark.django_db
def test_knowledge_card_list_has_green_create_action_beside_heading(client):
    response = client.get(reverse("knowledge-card-list"))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'class="knowledge-card-list-heading"' in body
    assert 'class="knowledge-card-create-button"' in body
    assert f'href="{reverse("knowledge-card-create")}"' in body
    assert "新建知识卡片" in body

    css = (Path(__file__).parents[1] / "static/question_bank/css/app.css").read_text(
        encoding="utf-8"
    )
    assert ".knowledge-card-create-button" in css
    assert "background:var(--teal)" in css
    assert "@media (max-width:760px)" in css


@pytest.mark.django_db
def test_review_workbench_keeps_card_in_queue_links_and_clear_keeps_queue(client, subject):
    card = KnowledgeCard.objects.create(name="复习筛选卡", subject=subject, type="definition")
    response = client.get(reverse("review-list"), {"queue": "recent", "knowledge_card": card.pk})
    body = response.content.decode()

    for queue in ("due", "recent", "overdue"):
        assert f'?queue={queue}&amp;knowledge_card={card.pk}' in body
    assert f'href="?queue=recent" class="review-workbench__clear"' in body
    assert 'name="queue" value="recent"' in body
    assert 'class="review-workbench"' in body
    assert 'aria-current="page"' in body
    assert 'question_bank/css/review-workbench.css' in body


@pytest.mark.django_db
def test_review_workbench_question_card_starts_review(client, subject):
    from datetime import timedelta
    from django.utils import timezone

    question = Question.objects.create(
        subject=subject,
        title="复习入口题",
        draft=False,
        next_review_at=timezone.now() - timedelta(days=1),
    )
    response = client.get(reverse("review-list"))
    body = response.content.decode()

    assert 'class="review-workbench__question"' in body
    assert f'href="{reverse("review-detail", args=[question.pk])}"' in body
    assert "开始复习" in body
    assert "复习入口题" in body


@pytest.mark.django_db
def test_review_workbench_start_links_have_distinct_accessible_names(client, subject):
    from datetime import timedelta
    from django.utils import timezone

    for title in ("极限练习", "连续练习"):
        Question.objects.create(
            subject=subject,
            title=title,
            draft=False,
            next_review_at=timezone.now() - timedelta(days=1),
        )

    body = client.get(reverse("review-list")).content.decode()

    for title in ("极限练习", "连续练习"):
        assert f'aria-label="开始复习：{title}"' in body
    assert body.count('>开始复习</a>') == 2


@pytest.mark.django_db
def test_review_workbench_empty_state_has_search_entry(client):
    body = client.get(reverse("review-list")).content.decode()
    assert 'class="review-workbench__empty"' in body
    assert f'href="{reverse("question-list")}"' in body


def test_review_workbench_css_defines_responsive_layout():
    css = (Path(__file__).parents[1] / "static/question_bank/css/review-workbench.css").read_text(
        encoding="utf-8"
    )
    assert ".review-workbench" in css
    assert ".review-workbench__queues" in css
    assert ".review-workbench__question" in css
    assert "@media(max-width:760px)" in css.replace(" ", "")
    assert "grid-template-columns:1fr" in css.replace(" ", "")
    assert "min-width:0" in css.replace(" ", "")
    assert "overflow-wrap:anywhere" in css.replace(" ", "")


@pytest.mark.django_db
def test_stats_workbench_shows_four_existing_metrics_and_chinese_mastery(client, subject):
    from datetime import timedelta
    from django.utils import timezone

    Question.objects.create(
        subject=subject,
        title="统计样本",
        mastery="mastered",
        draft=False,
        next_review_at=timezone.now() - timedelta(days=1),
    )
    response = client.get(reverse("stats"))
    body = response.content.decode()

    assert response.status_code == 200
    assert 'class="stats-workbench"' in body
    assert 'question_bank/css/stats-workbench.css' in body
    assert body.count('class="stats-workbench__metric"') == 4
    assert "题目总数" in body and "待复习题数" in body
    assert "近 30 天复习次数" in body and "近 30 天错误次数" in body
    assert "数学分析" in body
    for key, label in Question.MASTERY_CHOICES:
        assert {"key": key, "label": label, "count": response.context["stats"]["questions_by_mastery"][key]} in response.context["mastery_rows"]
        assert label in body
    assert 'data-metric="question_total">1<' in body
    assert 'data-metric="due_questions">1<' in body
    assert 'data-metric="reviews_last_30_days">0<' in body
    assert 'data-metric="errors_last_30_days">0<' in body


@pytest.mark.django_db
def test_stats_workbench_empty_state_has_zero_metrics_and_subject_guidance(client):
    response = client.get(reverse("stats"))
    body = response.content.decode()

    assert response.status_code == 200
    assert body.count('class="stats-workbench__metric"') == 4
    for key in ("question_total", "due_questions", "reviews_last_30_days", "errors_last_30_days"):
        assert f'data-metric="{key}">0<' in body
    assert "暂无数据" in body
    assert f'href="{reverse("question-create")}"' in body


def test_stats_workbench_css_defines_responsive_layout():
    css = (Path(__file__).parents[1] / "static/question_bank/css/stats-workbench.css").read_text(
        encoding="utf-8"
    )
    assert ".stats-workbench" in css
    assert ".stats-workbench__metrics" in css
    assert ".stats-workbench__summary" in css
    assert "@media(max-width:760px)" in css.replace(" ", "")
    assert "grid-template-columns:1fr" in css.replace(" ", "")
