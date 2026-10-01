from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_base_template_has_navigation_mathjax_and_assistant(client):
    response = client.get(reverse("question-list"))
    body = response.content.decode()
    assert response.status_code == 200
    assert "题目检索" in body
    assert "MathJax" in body
    assert "aria-label=\"学习助手\"" in body


def test_visual_assets_exist():
    root = Path(__file__).resolve().parents[2]
    assert (root / "static/question_bank/css/app.css").exists()
    assert (root / "static/question_bank/js/app.js").exists()
    assert (root / "static/question_bank/assets/character-placeholder.svg").exists()
