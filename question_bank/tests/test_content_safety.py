import io
import re

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
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
