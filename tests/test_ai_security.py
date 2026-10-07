from collections import UserDict
from datetime import timedelta
import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from question_bank.ai.config import AIConfig
from question_bank.ai.service import analyze_question
from question_bank.models import Question, QuestionAIAnalysis, Subject


@pytest.fixture
def subject(db):
    return Subject.objects.create(name="数学分析")


class SecretFailingProvider:
    provider_name = "secret-provider"
    model_name = "secret-model"

    def __init__(self, secret):
        self.secret = secret

    def analyze(self, **kwargs):
        raise RuntimeError(f"Authorization: Bearer {self.secret}")


class SecretEchoProvider:
    provider_name = "secret-echo"
    model_name = "secret-model"

    def __init__(self, secret):
        self.secret = secret

    def analyze(self, **kwargs):
        return {
            "status": "awaiting_review",
            "recognized_statement": f"response {self.secret}",
            "recognized_solution": "safe",
            "knowledge_points": [],
            "suggested_tags": [],
            "missing_cards": [],
        }


class SecretKeyProvider(SecretEchoProvider):
    def analyze(self, **kwargs):
        return {self.secret: "value"}


@pytest.mark.django_db
def test_api_key_never_reaches_database_html_or_logs(client, subject, caplog):
    secret = "test-api-key-must-not-leak"
    question = Question.objects.create(subject=subject, title="密钥保护题")

    with caplog.at_level("DEBUG"):
        analysis = analyze_question(
            question,
            provider=SecretFailingProvider(secret),
            config=AIConfig(enabled=True, api_key=secret),
        )
        response = client.get(reverse("question-analysis", args=[question.pk]))

    assert secret not in analysis.error_message
    assert secret not in response.content.decode()
    assert secret not in caplog.text


@pytest.mark.django_db
def test_provider_response_cannot_echo_api_key_into_database(subject):
    secret = "echoed-test-api-key"
    question = Question.objects.create(subject=subject, title="Provider 回显密钥")

    analysis = analyze_question(
        question,
        provider=SecretEchoProvider(secret),
        config=AIConfig(enabled=True, api_key=secret),
    )

    question.refresh_from_db()
    assert secret not in analysis.recognized_statement
    assert secret not in str(analysis.raw_response)
    assert secret not in question.recognized_statement


@pytest.mark.django_db
def test_provider_metadata_cannot_echo_api_key_into_database(subject):
    secret = "metadata-test-api-key"
    provider = SecretEchoProvider(secret)
    provider.provider_name = f"relay-{secret}"
    provider.model_name = f"model-{secret}"
    question = Question.objects.create(subject=subject, title="Provider 元数据密钥")

    analysis = analyze_question(
        question,
        provider=provider,
        config=AIConfig(enabled=True, api_key=secret),
    )

    assert secret not in analysis.provider
    assert secret not in analysis.model


@pytest.mark.django_db
def test_provider_response_key_cannot_echo_api_key_into_database(subject):
    secret = "json-key-test-api-key"
    question = Question.objects.create(subject=subject, title="Provider 键名密钥")

    analysis = analyze_question(
        question,
        provider=SecretKeyProvider(secret),
        config=AIConfig(enabled=True, api_key=secret),
    )

    assert analysis.status == Question.AI_STATUS_FAILED
    assert secret not in str(analysis.raw_response)


def test_secret_redaction_handles_arbitrary_mapping_implementations():
    from question_bank.ai.service import _redact_secret

    secret = "mapping-secret"
    payload = UserDict(
        {
            f"header-{secret}": UserDict({"authorization": f"Bearer {secret}"}),
        }
    )

    redacted = _redact_secret(payload, secret)

    assert isinstance(redacted, dict)
    assert secret not in json.dumps(redacted, ensure_ascii=False)


@pytest.mark.django_db
def test_analysis_page_context_exposes_only_whitelisted_analysis_fields(client, subject):
    question = Question.objects.create(subject=subject, title="页面白名单")
    QuestionAIAnalysis.objects.create(
        question=question,
        version=1,
        input_fingerprint="a" * 64,
        status=Question.AI_STATUS_FAILED,
        provider="private-provider",
        model="private-model",
        raw_response={"authorization": "private-token"},
        error_message="可展示错误",
    )

    response = client.get(reverse("question-analysis", args=[question.pk]))

    assert response.status_code == 200
    assert "latest_analysis" not in response.context
    assert response.context["analysis_summary"] == {
        "version": 1,
        "status": Question.AI_STATUS_FAILED,
        "error_message": "可展示错误",
    }
    body = response.content.decode()
    assert "private-provider" not in body
    assert "private-model" not in body
    assert "private-token" not in body


@pytest.mark.django_db
def test_question_detail_context_exposes_only_whitelisted_analysis_fields(client, subject):
    question = Question.objects.create(subject=subject, title="详情白名单")
    QuestionAIAnalysis.objects.create(
        question=question,
        version=1,
        input_fingerprint="b" * 64,
        status=Question.AI_STATUS_FAILED,
        raw_response={"authorization": "detail-private-token"},
        error_message="公开错误",
    )

    response = client.get(reverse("question-detail", args=[question.pk]))

    assert "latest_analysis" not in response.context
    assert response.context["analysis_summary"] == {
        "version": 1,
        "status": Question.AI_STATUS_FAILED,
        "error_message": "公开错误",
    }
    assert "detail-private-token" not in response.content.decode()


def test_deployment_documents_ai_retention_cleanup_and_provider_policy_review():
    readme = Path("deploy/README.md").read_text(encoding="utf-8")

    assert "cleanup_ai_raw_responses" in readme
    assert "30 天" in readme
    assert "数据保留" in readme
    assert "训练政策" in readme


@pytest.mark.django_db
def test_cleanup_command_uses_default_retention(subject):
    question = Question.objects.create(subject=subject, title="清理命令")
    analysis = QuestionAIAnalysis.objects.create(
        question=question,
        version=1,
        input_fingerprint="c" * 64,
        raw_response={"private": "expired"},
    )
    QuestionAIAnalysis.objects.filter(pk=analysis.pk).update(
        created_at=timezone.now() - timedelta(days=60),
        completed_at=timezone.now() - timedelta(days=31),
    )

    call_command("cleanup_ai_raw_responses")

    analysis.refresh_from_db()
    assert analysis.raw_response == {}
