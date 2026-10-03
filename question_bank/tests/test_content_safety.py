import io
import re

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from question_bank.markdown import render_markdown
from question_bank.models import Question, question_attachment_upload_to
from question_bank.validators import MAX_IMAGE_SIZE, validate_image_upload


def image_file(name, image_format, content_type):
    stream = io.BytesIO()
    Image.new("RGB", (3, 3), color=(10, 20, 30)).save(stream, format=image_format)
    return SimpleUploadedFile(name, stream.getvalue(), content_type=content_type)


@pytest.mark.parametrize(
    ("name", "image_format", "content_type"),
    [
        ("figure.png", "PNG", "image/png"),
        ("figure.jpeg", "JPEG", "image/jpeg"),
        ("figure.webp", "WEBP", "image/webp"),
    ],
)
def test_image_validator_accepts_supported_images(name, image_format, content_type):
    validate_image_upload(image_file(name, image_format, content_type))


def test_image_validator_rejects_files_over_ten_megabytes():
    upload = SimpleUploadedFile(
        "large.png", b"0" * (MAX_IMAGE_SIZE + 1), content_type="image/png"
    )

    with pytest.raises(ValidationError, match="10 MB"):
        validate_image_upload(upload)


@pytest.mark.parametrize(
    "upload",
    [
        SimpleUploadedFile("fake.png", b"plain text", content_type="image/png"),
        SimpleUploadedFile("figure.svg", b"<svg></svg>", content_type="image/svg+xml"),
        SimpleUploadedFile("figure.png", b"not a jpeg", content_type="image/jpeg"),
    ],
)
def test_image_validator_rejects_forged_or_unsupported_files(upload):
    with pytest.raises(ValidationError):
        validate_image_upload(upload)


def test_attachment_upload_path_uses_random_safe_filename():
    question = Question()
    first = question_attachment_upload_to(question, "../../same.PNG")
    second = question_attachment_upload_to(question, "same.PNG")

    assert first != second
    assert first.startswith("questions/None/")
    assert re.fullmatch(r"questions/None/[0-9a-f]{32}\.png", first)


def test_markdown_rendering_removes_raw_html_and_scripts_but_keeps_mathjax():
    source = (
        "<script>alert('x')</script><div onclick=\"evil()\">Text</div>\n\n"
        "**bold** $x^2$ $$\\sum_{i=1}^n i$$ \\(a+b\\) \\[x\\]"
    )

    rendered = render_markdown(source)

    assert "<script" not in rendered.lower()
    assert "onclick" not in rendered.lower()
    assert "<div" not in rendered.lower()
    assert "<strong>bold</strong>" in rendered
    for formula in ("$x^2$", "$$\\sum_{i=1}^n i$$", "\\(a+b\\)", "\\[x\\]"):
        assert formula in rendered


def test_mathjax_formula_cannot_restore_raw_html_after_sanitizing():
    rendered = render_markdown(r"$<img src=x onerror=alert(1)>$")

    assert "<img" not in rendered.lower()
    assert "$&lt;img src=x onerror=alert(1)&gt;$" in rendered


def csrf_client():
    client = Client(enforce_csrf_checks=True)
    client.cookies["csrftoken"] = "a" * 32
    return client


def csrf_token(client):
    return client.cookies["csrftoken"].value


def test_markdown_preview_requires_post():
    response = Client().get(reverse("markdown-preview"))

    assert response.status_code == 405


def test_markdown_preview_rejects_missing_csrf_token():
    client = Client(enforce_csrf_checks=True)

    missing = client.post(reverse("markdown-preview"), {"source": "**bold**"})

    assert missing.status_code == 403


@pytest.mark.django_db
def test_markdown_preview_rejects_invalid_csrf_token_with_cookie():
    client = Client(enforce_csrf_checks=True)
    client.get(reverse("question-create"))
    assert "csrftoken" in client.cookies
    invalid_token = "b" * 32
    assert invalid_token != client.cookies["csrftoken"].value

    invalid = client.post(
        reverse("markdown-preview"),
        {"source": "**bold**", "csrfmiddlewaretoken": invalid_token},
    )

    assert invalid.status_code == 403


def test_markdown_preview_returns_sanitized_html_and_preserves_mathjax():
    client = csrf_client()
    source = '<script>alert(1)</script><span onclick="evil()">Text</span> **bold** $x^2$'

    response = client.post(
        reverse("markdown-preview"),
        {"source": source, "csrfmiddlewaretoken": csrf_token(client)},
    )

    assert response.status_code == 200
    html = response.json()["html"]
    assert "<script" not in html.lower()
    assert "onclick" not in html.lower()
    assert "<span" not in html.lower()
    assert "<strong>bold</strong>" in html
    assert "$x^2$" in html


def test_markdown_preview_rejects_source_over_100000_characters():
    client = csrf_client()

    response = client.post(
        reverse("markdown-preview"),
        {"source": "x" * 100001, "csrfmiddlewaretoken": csrf_token(client)},
    )

    assert response.status_code == 400
