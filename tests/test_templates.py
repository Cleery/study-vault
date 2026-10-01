import pytest
from django.urls import reverse

from question_bank.models import Question, Subject


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
