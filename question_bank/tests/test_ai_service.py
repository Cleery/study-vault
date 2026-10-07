import importlib
from datetime import timedelta
import json

import pytest
from django.apps import apps
from django.db import OperationalError
from django.utils import timezone

from question_bank.models import (
    KnowledgeCard,
    MissingKnowledgeCardSuggestion,
    Question,
    QuestionAIAnalysis,
    QuestionAIAnalysisAction,
    QuestionAttachment,
    Tag,
)


@pytest.fixture
def subject(db):
    from question_bank.models import Subject

    return Subject.objects.create(name="数学分析")


class SpyProvider:
    provider_name = "spy"
    model_name = "spy-model"

    def __init__(self):
        self.calls = 0

    def analyze(self, *, question_images, solution_images, corrected_text, knowledge_cards, personal_notes):
        self.calls += 1
        return {
            "status": "awaiting_review",
            "recognized_statement": corrected_text.get("statement", "") or "识别题干",
            "recognized_solution": corrected_text.get("solution", "") or "识别解法",
            "knowledge_points": [],
            "suggested_tags": [
                {"name": "证明", "category": "method", "confidence": 0.6, "reason": "模拟"}
            ],
            "missing_cards": [],
        }


class RaisingProvider:
    provider_name = "raising"
    model_name = "raising-model"

    def __init__(self, error):
        self.error = error

    def analyze(self, **kwargs):
        raise self.error


@pytest.mark.django_db
def test_ai_disabled_does_not_call_provider(subject):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    question = Question.objects.create(subject=subject, title="待分析")
    provider = SpyProvider()
    analysis = analyze_question(question, provider=provider, config=AIConfig(enabled=False))

    assert provider.calls == 0
    assert analysis.status == Question.AI_STATUS_PENDING
    assert analysis.provider == ""


@pytest.mark.django_db
def test_provider_construction_failure_marks_analysis_failed(subject, monkeypatch):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    question = Question.objects.create(subject=subject, title="Provider 配置错误")
    monkeypatch.setattr(
        "question_bank.ai.service.provider_for_config",
        lambda config: (_ for _ in ()).throw(ValueError("secret constructor detail")),
    )

    analysis = analyze_question(
        question,
        config=AIConfig(
            enabled=True,
            provider="placeholder",
            api_key="constructor-secret",
        ),
    )

    question.refresh_from_db()
    assert analysis.status == Question.AI_STATUS_FAILED
    assert question.ai_status == Question.AI_STATUS_FAILED
    assert analysis.error_message == "AI Provider 配置无效，请检查服务配置。"
    assert "constructor-secret" not in analysis.error_message


def test_raw_response_retention_defaults_to_thirty_days(monkeypatch):
    from question_bank.ai.config import AIConfig

    monkeypatch.delenv("AI_RAW_RESPONSE_RETENTION_DAYS", raising=False)
    assert AIConfig.from_env().raw_response_retention_days == 30


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (TimeoutError("request timed out with secret-token"), "AI Provider 请求超时，请稍后重试。"),
        (RuntimeError("relay rejected secret-token"), "AI 分析暂时不可用，请稍后重试。"),
    ],
)
def test_provider_failures_are_retryable_and_sanitized(subject, error, expected_message):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    question = Question.objects.create(
        subject=subject,
        title="Provider 失败题",
        recognized_statement="人工校对题干",
        recognized_solution="人工校对解答",
    )
    image = QuestionAttachment.objects.create(
        question=question,
        file="questions/provider-failure.png",
        file_kind="image",
        attachment_role="question",
    )

    analysis = analyze_question(
        question,
        provider=RaisingProvider(error),
        config=AIConfig(enabled=True, api_key="secret-token"),
    )

    question.refresh_from_db()
    assert analysis.status == Question.AI_STATUS_FAILED
    assert question.ai_status == Question.AI_STATUS_FAILED
    assert analysis.error_message == expected_message
    assert "secret-token" not in analysis.error_message
    assert question.recognized_statement == "人工校对题干"
    assert question.recognized_solution == "人工校对解答"
    assert question.attachments.filter(pk=image.pk).exists()


@pytest.mark.django_db
def test_schema_failure_does_not_write_partial_provider_result(subject):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    question = Question.objects.create(
        subject=subject,
        title="Schema 失败题",
        recognized_statement="保留题干",
        recognized_solution="保留解答",
    )

    class InvalidSchemaProvider(SpyProvider):
        def analyze(self, **kwargs):
            return {
                "status": "awaiting_review",
                "recognized_statement": "不得写回的题干",
                "recognized_solution": "不得写回的解答",
                "knowledge_points": [],
                "suggested_tags": [{"name": "坏标签", "category": "invalid", "confidence": 0.8}],
                "missing_cards": [],
            }

    analysis = analyze_question(
        question,
        provider=InvalidSchemaProvider(),
        config=AIConfig(enabled=True),
    )

    question.refresh_from_db()
    assert analysis.status == Question.AI_STATUS_FAILED
    assert analysis.error_message == "AI 返回格式无效，请重新分析。"
    assert analysis.knowledge_points == {}
    assert analysis.suggested_tags == {}
    assert analysis.raw_response["recognized_statement"] == "不得写回的题干"
    assert question.recognized_statement == "保留题干"
    assert question.recognized_solution == "保留解答"


@pytest.mark.django_db
def test_candidate_retrieval_failure_preserves_question_and_relations(subject, monkeypatch):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    question = Question.objects.create(
        subject=subject,
        title="候选检索失败题",
        recognized_statement="保留题干",
    )
    card = KnowledgeCard.objects.create(subject=subject, name="人工卡片", type="theorem")
    question.knowledge_cards.add(card)
    monkeypatch.setattr(
        "question_bank.ai.service._keyword_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("database secret-token")),
    )

    analysis = analyze_question(
        question,
        provider=SpyProvider(),
        config=AIConfig(enabled=True, api_key="secret-token"),
    )

    question.refresh_from_db()
    assert analysis.status == Question.AI_STATUS_FAILED
    assert analysis.error_message == "候选知识卡片检索失败，请稍后重试。"
    assert list(question.knowledge_cards.all()) == [card]
    assert question.recognized_statement == "保留题干"


@pytest.mark.django_db
def test_failed_analysis_can_retry_same_input_as_new_version(subject):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    question = Question.objects.create(subject=subject, title="失败重试", recognized_statement="相同输入")
    config = AIConfig(enabled=True)
    failed = analyze_question(
        question,
        provider=RaisingProvider(TimeoutError("timeout")),
        config=config,
    )
    retried = analyze_question(question, provider=SpyProvider(), config=config)

    question.refresh_from_db()
    assert failed.status == Question.AI_STATUS_FAILED
    assert retried.pk != failed.pk
    assert retried.version == failed.version + 1
    assert retried.status == Question.AI_STATUS_AWAITING_REVIEW
    assert question.ai_status == Question.AI_STATUS_AWAITING_REVIEW


@pytest.mark.django_db
def test_relay_analysis_is_queued_without_constructing_provider(subject, monkeypatch):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    question = Question.objects.create(subject=subject, title="Relay 入队")
    monkeypatch.setattr(
        "question_bank.ai.service.provider_for_config",
        lambda config: (_ for _ in ()).throw(
            AssertionError("web request constructed relay provider")
        ),
    )

    analysis = analyze_question(
        question,
        config=AIConfig(enabled=True, provider="relay", api_key="test-key"),
    )

    question.refresh_from_db()
    assert analysis.status == Question.AI_STATUS_PENDING
    assert analysis.attempt_count == 0
    assert question.ai_status == Question.AI_STATUS_ANALYZING


@pytest.mark.django_db
def test_worker_processes_pending_analysis_once(subject, monkeypatch):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question, process_next_ai_task

    question = Question.objects.create(
        subject=subject,
        title="Worker 成功",
        recognized_statement="人工文本",
    )
    analysis = analyze_question(
        question,
        config=AIConfig(enabled=True, provider="relay", api_key="test-key"),
    )
    provider = SpyProvider()
    monkeypatch.setattr(
        "question_bank.ai.service.provider_for_config",
        lambda config: provider,
    )

    processed = process_next_ai_task(
        config=AIConfig(enabled=True, provider="relay", api_key="test-key")
    )

    analysis.refresh_from_db()
    assert processed == analysis.pk
    assert provider.calls == 1
    assert analysis.status == Question.AI_STATUS_AWAITING_REVIEW
    assert analysis.attempt_count == 1
    assert process_next_ai_task(
        config=AIConfig(enabled=True, provider="relay", api_key="test-key")
    ) is None


@pytest.mark.django_db
def test_worker_requeues_retryable_failure_and_recovers_after_restart(
    subject, monkeypatch
):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question, process_next_ai_task

    question = Question.objects.create(subject=subject, title="Worker 重试")
    analysis = analyze_question(
        question,
        config=AIConfig(enabled=True, provider="relay", api_key="test-key"),
    )
    providers = iter(
        [RaisingProvider(TimeoutError("temporary")), SpyProvider()]
    )
    monkeypatch.setattr(
        "question_bank.ai.service.provider_for_config",
        lambda config: next(providers),
    )
    config = AIConfig(
        enabled=True,
        provider="relay",
        api_key="test-key",
        max_retries=2,
    )

    assert process_next_ai_task(config=config) == analysis.pk
    analysis.refresh_from_db()
    assert analysis.status == Question.AI_STATUS_PENDING
    assert analysis.attempt_count == 1
    QuestionAIAnalysis.objects.filter(pk=analysis.pk).update(
        next_attempt_at=timezone.now() - timedelta(seconds=1)
    )

    assert process_next_ai_task(config=config) == analysis.pk
    analysis.refresh_from_db()
    assert analysis.status == Question.AI_STATUS_AWAITING_REVIEW
    assert analysis.attempt_count == 2


@pytest.mark.django_db
def test_worker_recovers_stale_analyzing_task(subject, monkeypatch):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question, process_next_ai_task

    question = Question.objects.create(subject=subject, title="Worker 重启恢复")
    analysis = analyze_question(
        question,
        config=AIConfig(enabled=True, provider="relay", api_key="test-key"),
    )
    QuestionAIAnalysis.objects.filter(pk=analysis.pk).update(
        status=Question.AI_STATUS_ANALYZING,
        started_at=timezone.now() - timedelta(minutes=5),
    )
    monkeypatch.setattr(
        "question_bank.ai.service.provider_for_config",
        lambda config: SpyProvider(),
    )

    processed = process_next_ai_task(
        config=AIConfig(
            enabled=True,
            provider="relay",
            api_key="test-key",
            total_timeout_seconds=30,
        )
    )

    analysis.refresh_from_db()
    assert processed == analysis.pk
    assert analysis.status == Question.AI_STATUS_AWAITING_REVIEW


@pytest.mark.django_db
def test_high_confidence_knowledge_candidate_is_auto_linked_with_ownership(subject):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question
    from question_bank.models import AIReviewOwnership

    question = Question.objects.create(subject=subject, title="高置信度自动关联")
    card = KnowledgeCard.objects.create(subject=subject, name="罗尔定理", type="theorem")

    class AutoProvider(SpyProvider):
        def analyze(self, **kwargs):
            return {
                "status": "awaiting_review",
                "recognized_statement": "题干",
                "recognized_solution": "解答",
                "knowledge_points": [{
                    "name": card.name,
                    "matched_card_id": str(card.pk),
                    "confidence": 0.96,
                    "reason": "直接使用",
                }],
                "suggested_tags": [{
                    "name": "证明",
                    "category": "method",
                    "confidence": 0.99,
                }],
                "missing_cards": [{
                    "name": "新卡片",
                    "card_type": "other",
                    "confidence": 0.99,
                }],
            }

    analysis = analyze_question(
        question,
        provider=AutoProvider(),
        config=AIConfig(enabled=True, auto_link_threshold=0.9),
    )

    assert question.knowledge_cards.filter(pk=card.pk).exists()
    ownership = AIReviewOwnership.objects.get(
        question=question, target_type="knowledge_point", target_id=card.pk
    )
    assert ownership.owning_analysis_id == analysis.pk
    assert analysis.actions.filter(
        action_type="confirm", candidate_type="knowledge_point", candidate_key=str(card.pk)
    ).exists()
    assert not analysis.actions.filter(candidate_type="tag").exists()
    assert not analysis.actions.filter(candidate_type="missing_card").exists()
    assert analysis.status == Question.AI_STATUS_AWAITING_REVIEW


@pytest.mark.django_db
def test_low_confidence_knowledge_candidate_is_not_auto_linked(subject):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    question = Question.objects.create(subject=subject, title="低置信度不自动关联")
    card = KnowledgeCard.objects.create(subject=subject, name="拉格朗日中值定理", type="theorem")

    class LowProvider(SpyProvider):
        def analyze(self, **kwargs):
            return {
                "status": "awaiting_review",
                "recognized_statement": "题干",
                "recognized_solution": "解答",
                "knowledge_points": [{
                    "name": card.name,
                    "matched_card_id": str(card.pk),
                    "confidence": 0.69,
                }],
                "suggested_tags": [],
                "missing_cards": [],
            }

    analysis = analyze_question(
        question,
        provider=LowProvider(),
        config=AIConfig(enabled=True, auto_link_threshold=0.9),
    )

    assert not question.knowledge_cards.filter(pk=card.pk).exists()
    assert not analysis.actions.exists()
    assert analysis.status == Question.AI_STATUS_AWAITING_REVIEW


@pytest.mark.django_db
def test_analysis_is_completed_after_all_candidates_are_reviewed(subject):
    from question_bank.ai.actions import review_candidate
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    question = Question.objects.create(subject=subject, title="全部候选已审核")
    card = KnowledgeCard.objects.create(subject=subject, name="柯西定理", type="theorem")

    class ReviewProvider(SpyProvider):
        def analyze(self, **kwargs):
            return {
                "status": "awaiting_review",
                "recognized_statement": "题干",
                "recognized_solution": "解答",
                "knowledge_points": [{
                    "name": card.name,
                    "matched_card_id": str(card.pk),
                    "confidence": 0.8,
                }],
                "suggested_tags": [{
                    "name": "极限",
                    "category": "topic",
                    "confidence": 0.8,
                }],
                "missing_cards": [],
            }

    analysis = analyze_question(
        question,
        provider=ReviewProvider(),
        config=AIConfig(enabled=True),
    )
    review_candidate(analysis, "knowledge_point", str(card.pk), "ignore")
    review_candidate(analysis, "tag", "topic:极限", "ignore")
    analysis.refresh_from_db()
    question.refresh_from_db()

    assert analysis.status == Question.AI_STATUS_COMPLETED
    assert question.ai_status == Question.AI_STATUS_COMPLETED


@pytest.mark.django_db
def test_worker_keeps_failure_after_retry_budget_is_exhausted(subject, monkeypatch):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question, process_next_ai_task

    question = Question.objects.create(subject=subject, title="Worker 重试耗尽")
    analysis = analyze_question(
        question,
        config=AIConfig(enabled=True, provider="relay", api_key="test-key"),
    )
    monkeypatch.setattr(
        "question_bank.ai.service.provider_for_config",
        lambda config: RaisingProvider(TimeoutError("temporary")),
    )

    assert process_next_ai_task(
        config=AIConfig(
            enabled=True,
            provider="relay",
            api_key="test-key",
            max_retries=0,
        )
    ) == analysis.pk

    analysis.refresh_from_db()
    question.refresh_from_db()
    assert analysis.status == Question.AI_STATUS_FAILED
    assert analysis.attempt_count == 1
    assert question.ai_status == Question.AI_STATUS_FAILED


@pytest.mark.django_db
def test_process_ai_tasks_once_processes_one_task(monkeypatch):
    from django.core.management import call_command
    from question_bank.management.commands import process_ai_tasks

    calls = []
    monkeypatch.setattr(
        process_ai_tasks,
        "process_next_ai_task",
        lambda: calls.append("processed") or None,
    )

    call_command("process_ai_tasks", "--once")

    assert calls == ["processed"]


@pytest.mark.django_db
def test_raw_response_cleanup_defaults_to_thirty_days_and_keeps_summary(subject):
    from question_bank.ai.service import purge_expired_raw_responses

    question = Question.objects.create(subject=subject, title="原始响应清理")
    old = QuestionAIAnalysis.objects.create(
        question=question,
        version=1,
        input_fingerprint="1" * 64,
        status=Question.AI_STATUS_AWAITING_REVIEW,
        knowledge_points={"items": [{"name": "保留摘要"}]},
        raw_response={"private": "expired"},
    )
    fresh = QuestionAIAnalysis.objects.create(
        question=question,
        version=2,
        input_fingerprint="2" * 64,
        status=Question.AI_STATUS_AWAITING_REVIEW,
        raw_response={"private": "fresh"},
    )
    QuestionAIAnalysis.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(days=60),
        completed_at=timezone.now() - timedelta(days=31),
    )
    QuestionAIAnalysis.objects.filter(pk=fresh.pk).update(
        created_at=timezone.now() - timedelta(days=60),
        completed_at=timezone.now() - timedelta(days=29),
    )
    QuestionAIAnalysisAction.objects.create(
        analysis=old,
        candidate_type="knowledge_point",
        candidate_key="kept",
        action_type="ignore",
    )

    removed = purge_expired_raw_responses()

    old.refresh_from_db()
    fresh.refresh_from_db()
    assert removed == 1
    assert old.raw_response == {}
    assert old.knowledge_points == {"items": [{"name": "保留摘要"}]}
    assert old.actions.filter(candidate_key="kept", action_type="ignore").exists()
    assert fresh.raw_response == {"private": "fresh"}


@pytest.mark.django_db
def test_placeholder_provider_returns_stable_valid_structure():
    from question_bank.ai.providers import PlaceholderProvider
    from question_bank.ai.schemas import AnalysisResult

    provider = PlaceholderProvider()
    payload = provider.analyze(
        question_images=[], solution_images=[],
        corrected_text={"statement": "题干", "solution": "解法"},
        knowledge_cards=[], personal_notes="",
    )
    result = AnalysisResult.from_dict(payload)
    assert result.recognized_statement == "题干"
    assert result.recognized_solution == "解法"
    assert result.to_dict() == AnalysisResult.from_dict(payload).to_dict()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field_name, field_value",
    [
        ("name", "介值定理"),
        ("formal_statement", "连续函数取遍中间值"),
        ("conditions", "闭区间连续且端点异号"),
        ("proof", "构造辅助函数后使用连续性"),
        ("usage_signals", "看到闭区间和异号"),
    ],
)
def test_keyword_matching_uses_all_knowledge_card_fields(subject, field_name, field_value):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    card = KnowledgeCard.objects.create(
        subject=subject,
        name="介值定理",
        card_type="theorem",
        formal_statement="连续函数取遍中间值",
        conditions="闭区间连续且端点异号",
        proof="构造辅助函数后使用连续性",
        usage_signals="看到闭区间和异号",
    )
    question = Question.objects.create(
        subject=subject,
        title="连续函数题",
        recognized_statement=field_value,
        recognized_solution="",
    )
    analysis = analyze_question(
        question, config=AIConfig(enabled=True, provider="placeholder")
    )
    items = analysis.knowledge_points["items"]
    assert any(item["matched_card_id"] == str(card.pk) for item in items)


@pytest.mark.django_db
def test_provider_receives_only_limited_local_keyword_candidates(subject):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    matched = [
        KnowledgeCard.objects.create(
            subject=subject,
            name=f"候选定理{i}",
            type="theorem",
        )
        for i in range(4)
    ]
    unrelated = KnowledgeCard.objects.create(
        subject=subject,
        name="不得外发的整科卡片",
        type="theorem",
        proof="私密证明全文",
    )
    question = Question.objects.create(
        subject=subject,
        title="本地候选",
        recognized_statement="候选定理0 候选定理1 候选定理2 候选定理3",
    )
    captured = {}

    class CandidateCapturingProvider(SpyProvider):
        def analyze(self, **kwargs):
            captured["cards"] = list(kwargs["knowledge_cards"])
            return super().analyze(**kwargs)

    analyze_question(
        question,
        provider=CandidateCapturingProvider(),
        config=AIConfig(enabled=True, max_candidate_cards=2),
    )

    assert len(captured["cards"]) == 2
    assert set(captured["cards"]).issubset(set(matched))
    assert unrelated not in captured["cards"]


def test_candidate_and_request_limits_load_from_environment(monkeypatch):
    from question_bank.ai.config import AIConfig

    monkeypatch.setenv("AI_MAX_CANDIDATE_CARDS", "7")
    monkeypatch.setenv("AI_MAX_REQUEST_BYTES", "12345")

    config = AIConfig.from_env()

    assert config.max_candidate_cards == 7
    assert config.max_request_bytes == 12345


@pytest.mark.django_db
def test_invalid_provider_response_raw_envelope_is_redacted_and_retained(subject):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.exceptions import AIProviderResponseError
    from question_bank.ai.service import analyze_question

    secret = "raw-envelope-secret"
    question = Question.objects.create(subject=subject, title="无效响应审计")

    class InvalidEnvelopeProvider:
        provider_name = "invalid-envelope"
        model_name = "invalid-model"

        def analyze(self, **kwargs):
            raise AIProviderResponseError(
                "invalid schema",
                raw_response={"id": "raw-1", "content": f"Bearer {secret}"},
            )

    analysis = analyze_question(
        question,
        provider=InvalidEnvelopeProvider(),
        config=AIConfig(enabled=True, api_key=secret, max_response_bytes=4096),
    )

    assert analysis.status == Question.AI_STATUS_FAILED
    assert analysis.raw_response["id"] == "raw-1"
    assert secret not in json.dumps(analysis.raw_response, ensure_ascii=False)


@pytest.mark.django_db
def test_analysis_is_idempotent_and_text_change_creates_version(subject):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    question = Question.objects.create(subject=subject, title="版本题", recognized_statement="原文")
    config = AIConfig(enabled=True, provider="placeholder")
    first = analyze_question(question, config=config)
    repeated = analyze_question(question, config=config)
    assert repeated.pk == first.pk
    assert QuestionAIAnalysis.objects.filter(question=question).count() == 1

    question.recognized_statement = "修改后的文本"
    question.save(update_fields=["recognized_statement", "updated_at"])
    second = analyze_question(question, config=config)
    assert second.pk != first.pk
    assert second.version == first.version + 1
    assert QuestionAIAnalysis.objects.filter(question=question).count() == 2


@pytest.mark.django_db
def test_force_analysis_creates_new_version_for_identical_input(subject):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    question = Question.objects.create(
        subject=subject,
        title="强制重分析",
        recognized_statement="保持相同的校对文本",
    )
    config = AIConfig(enabled=True, provider="placeholder")

    first = analyze_question(question, config=config)
    forced = analyze_question(question, config=config, force=True)

    assert forced.pk != first.pk
    assert forced.version == first.version + 1


@pytest.mark.django_db
def test_provider_recognition_never_overwrites_user_corrected_fields(subject):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    question = Question.objects.create(
        subject=subject,
        title="识别字段隔离",
        recognized_statement="人工题干",
        recognized_solution="人工解答",
    )

    class ModelRecognitionProvider(SpyProvider):
        def analyze(self, **kwargs):
            payload = super().analyze(**kwargs)
            payload["recognized_statement"] = "模型题干"
            payload["recognized_solution"] = "模型解答"
            return payload

    analysis = analyze_question(
        question,
        provider=ModelRecognitionProvider(),
        config=AIConfig(enabled=True),
    )

    question.refresh_from_db()
    assert analysis.recognized_statement == "模型题干"
    assert analysis.recognized_solution == "模型解答"
    assert question.recognized_statement == "人工题干"
    assert question.recognized_solution == "人工解答"


@pytest.mark.django_db
def test_domain_review_rejects_non_latest_analysis(subject):
    from question_bank.ai.actions import StaleAnalysis, review_candidate

    question = Question.objects.create(subject=subject, title="领域层旧版本")
    card = KnowledgeCard.objects.create(subject=subject, name="旧版本卡片", type="theorem")
    old = _review_analysis(
        question,
        knowledge=[
            {
                "name": card.name,
                "matched_card_id": str(card.pk),
                "confidence": 0.8,
            }
        ],
    )
    QuestionAIAnalysis.objects.create(
        question=question,
        version=2,
        input_fingerprint="2" * 64,
        status=Question.AI_STATUS_AWAITING_REVIEW,
    )

    with pytest.raises(StaleAnalysis):
        review_candidate(old, "knowledge_point", str(card.pk), "confirm")

    assert not question.knowledge_cards.filter(pk=card.pk).exists()


@pytest.mark.django_db
def test_old_provider_result_does_not_write_back_after_input_changes(subject):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    question = Question.objects.create(subject=subject, title="并发题", recognized_statement="旧文本")

    class MutatingProvider(SpyProvider):
        def analyze(self, **kwargs):
            Question.objects.filter(pk=question.pk).update(recognized_statement="新文本")
            return super().analyze(**kwargs)

    original = analyze_question(question, provider=MutatingProvider(), config=AIConfig(enabled=True))
    question.refresh_from_db()
    assert question.recognized_statement == "新文本"
    assert original.status == Question.AI_STATUS_PENDING
    assert original.knowledge_points == {}
    assert "未写回" in original.error_message


@pytest.mark.django_db
def test_stale_caller_instance_uses_locked_current_input(subject):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    stale_question = Question.objects.create(
        subject=subject,
        title="锁前旧对象",
        recognized_statement="旧题干",
        recognized_solution="旧解答",
        personal_signals="旧信号",
    )
    Question.objects.filter(pk=stale_question.pk).update(
        recognized_statement="新题干",
        recognized_solution="新解答",
        personal_signals="新信号",
    )
    captured = {}

    class CapturingProvider(SpyProvider):
        def analyze(self, **kwargs):
            captured.update(kwargs)
            return super().analyze(**kwargs)

    analysis = analyze_question(
        stale_question,
        provider=CapturingProvider(),
        config=AIConfig(enabled=True),
    )

    stale_question.refresh_from_db()
    assert captured["corrected_text"] == {"statement": "新题干", "solution": "新解答"}
    assert captured["personal_notes"] == "新信号"
    assert analysis.recognized_statement == "新题干"
    assert stale_question.recognized_statement == "新题干"


@pytest.mark.django_db
def test_old_analysis_cannot_write_back_after_newer_version_exists(subject):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    question = Question.objects.create(subject=subject, title="并发版本", recognized_statement="同一输入")

    class NewerVersionProvider(SpyProvider):
        def analyze(self, **kwargs):
            first = QuestionAIAnalysis.objects.get(question=question, version=1)
            QuestionAIAnalysis.objects.create(
                question=question,
                version=2,
                input_fingerprint=first.input_fingerprint,
                status=Question.AI_STATUS_PENDING,
            )
            return super().analyze(**kwargs)

    old = analyze_question(question, provider=NewerVersionProvider(), config=AIConfig(enabled=True))
    question.refresh_from_db()
    assert old.version == 1
    assert old.knowledge_points == {}
    assert question.latest_ai_analysis.version == 2
    assert question.ai_status != Question.AI_STATUS_AWAITING_REVIEW


def _review_analysis(question, *, knowledge=None, tags=None, missing=None):
    return QuestionAIAnalysis.objects.create(
        question=question,
        version=1,
        input_fingerprint="f" * 64,
        status=Question.AI_STATUS_AWAITING_REVIEW,
        knowledge_points={"items": knowledge or []},
        suggested_tags={"items": tags or []},
        missing_cards={"items": missing or []},
    )


@pytest.mark.django_db
def test_confirming_knowledge_candidate_is_idempotent(subject):
    from question_bank.ai.actions import review_candidate

    question = Question.objects.create(subject=subject, title="关联知识点")
    card = KnowledgeCard.objects.create(subject=subject, name="介值定理", type="theorem")
    analysis = _review_analysis(
        question,
        knowledge=[{"name": card.name, "matched_card_id": str(card.pk), "confidence": 0.96}],
    )

    review_candidate(analysis, "knowledge_point", str(card.pk), "confirm")
    review_candidate(analysis, "knowledge_point", str(card.pk), "confirm")

    assert list(question.knowledge_cards.all()) == [card]
    assert QuestionAIAnalysisAction.objects.filter(
        analysis=analysis,
        candidate_type="knowledge_point",
        candidate_key=str(card.pk),
        action_type="confirm",
    ).count() == 1


@pytest.mark.django_db
def test_ignoring_knowledge_candidate_does_not_link_it(subject):
    from question_bank.ai.actions import review_candidate

    question = Question.objects.create(subject=subject, title="忽略知识点")
    card = KnowledgeCard.objects.create(subject=subject, name="夹逼准则", type="theorem")
    analysis = _review_analysis(
        question,
        knowledge=[{"name": card.name, "matched_card_id": str(card.pk), "confidence": 0.8}],
    )

    review_candidate(analysis, "knowledge_point", str(card.pk), "ignore")

    assert question.knowledge_cards.count() == 0
    assert analysis.actions.filter(action_type="ignore", candidate_key=str(card.pk)).exists()


@pytest.mark.django_db
def test_revoking_knowledge_candidate_removes_link_and_records_action(subject):
    from question_bank.ai.actions import review_candidate

    question = Question.objects.create(subject=subject, title="撤销知识点")
    card = KnowledgeCard.objects.create(subject=subject, name="罗尔定理", type="theorem")
    analysis = _review_analysis(
        question,
        knowledge=[{"name": card.name, "matched_card_id": str(card.pk), "confidence": 0.93}],
    )
    confirm = review_candidate(analysis, "knowledge_point", str(card.pk), "confirm")
    review_candidate(analysis, "knowledge_point", str(card.pk), "revoke")

    assert question.knowledge_cards.count() == 0
    assert confirm.payload["relation_added"] is True
    assert analysis.actions.filter(action_type="revoke", candidate_key=str(card.pk)).exists()


@pytest.mark.django_db
def test_revoking_candidate_keeps_preexisting_manual_knowledge_link(subject):
    from question_bank.ai.actions import review_candidate

    question = Question.objects.create(subject=subject, title="保留人工关联")
    card = KnowledgeCard.objects.create(subject=subject, name="柯西中值定理", type="theorem")
    question.knowledge_cards.add(card)
    analysis = _review_analysis(
        question,
        knowledge=[{"name": card.name, "matched_card_id": str(card.pk), "confidence": 0.93}],
    )

    confirm = review_candidate(analysis, "knowledge_point", str(card.pk), "confirm")
    review_candidate(analysis, "knowledge_point", str(card.pk), "revoke")

    assert confirm.payload["relation_added"] is False
    assert question.knowledge_cards.filter(pk=card.pk).exists()


@pytest.mark.django_db
def test_second_revoke_keeps_knowledge_link_manually_restored_after_revoke(subject):
    from question_bank.ai.actions import review_candidate

    question = Question.objects.create(subject=subject, title="撤销后人工重连")
    card = KnowledgeCard.objects.create(subject=subject, name="泰勒定理", type="theorem")
    analysis = _review_analysis(
        question,
        knowledge=[{"name": card.name, "matched_card_id": str(card.pk), "confidence": 0.94}],
    )

    review_candidate(analysis, "knowledge_point", str(card.pk), "confirm")
    review_candidate(analysis, "knowledge_point", str(card.pk), "revoke")
    confirm = analysis.actions.get(
        action_type="confirm", candidate_type="knowledge_point", candidate_key=str(card.pk)
    )
    confirm.refresh_from_db()
    assert confirm.payload["relation_owned"] is False
    question.knowledge_cards.add(card)
    review_candidate(analysis, "knowledge_point", str(card.pk), "revoke")

    assert question.knowledge_cards.filter(pk=card.pk).exists()


@pytest.mark.django_db
def test_later_confirm_can_own_knowledge_link_after_manual_link_was_removed(subject):
    from question_bank.ai.actions import review_candidate

    question = Question.objects.create(subject=subject, title="人工解除后重新确认")
    card = KnowledgeCard.objects.create(subject=subject, name="积分中值定理", type="theorem")
    question.knowledge_cards.add(card)
    analysis = _review_analysis(
        question,
        knowledge=[{"name": card.name, "matched_card_id": str(card.pk), "confidence": 0.91}],
    )

    first = review_candidate(analysis, "knowledge_point", str(card.pk), "confirm")
    assert first.payload["relation_added"] is False
    question.knowledge_cards.remove(card)
    second = review_candidate(analysis, "knowledge_point", str(card.pk), "confirm")
    second.refresh_from_db()
    assert second.payload["relation_added"] is True
    review_candidate(analysis, "knowledge_point", str(card.pk), "revoke")

    assert not question.knowledge_cards.filter(pk=card.pk).exists()


@pytest.mark.django_db
def test_tag_suggestion_only_becomes_formal_after_confirmation(subject):
    from question_bank.ai.actions import review_candidate

    question = Question.objects.create(subject=subject, title="确认标签")
    analysis = _review_analysis(
        question,
        tags=[{"name": "  放缩  ", "category": "method", "confidence": 0.9}],
    )
    key = "method:放缩"

    assert Tag.objects.filter(name="放缩").count() == 0
    review_candidate(analysis, "tag", key, "confirm")

    tag = Tag.objects.get(name="放缩")
    assert tag.kind == "method"
    assert list(question.tags.all()) == [tag]


@pytest.mark.django_db
def test_second_revoke_keeps_tag_manually_restored_after_revoke(subject):
    from question_bank.ai.actions import review_candidate

    question = Question.objects.create(subject=subject, title="标签撤销后人工重连")
    analysis = _review_analysis(
        question,
        tags=[{"name": "放缩", "category": "method", "confidence": 0.9}],
    )

    review_candidate(analysis, "tag", "method:放缩", "confirm")
    tag = Tag.objects.get(name="放缩", kind="method")
    review_candidate(analysis, "tag", "method:放缩", "revoke")
    confirm = analysis.actions.get(
        action_type="confirm", candidate_type="tag", candidate_key="method:放缩"
    )
    confirm.refresh_from_db()
    assert confirm.payload["relation_owned"] is False
    question.tags.add(tag)
    review_candidate(analysis, "tag", "method:放缩", "revoke")

    assert question.tags.filter(pk=tag.pk).exists()


@pytest.mark.django_db
def test_later_confirm_can_own_tag_after_manual_link_was_removed(subject):
    from question_bank.ai.actions import review_candidate

    question = Question.objects.create(subject=subject, title="标签人工解除后重确认")
    tag = Tag.objects.create(name="构造函数", kind="method")
    question.tags.add(tag)
    analysis = _review_analysis(
        question,
        tags=[{"name": tag.name, "category": "method", "confidence": 0.9}],
    )

    first = review_candidate(analysis, "tag", "method:构造函数", "confirm")
    assert first.payload["relation_added"] is False
    question.tags.remove(tag)
    second = review_candidate(analysis, "tag", "method:构造函数", "confirm")
    second.refresh_from_db()
    assert second.payload["relation_added"] is True
    review_candidate(analysis, "tag", "method:构造函数", "revoke")

    assert not question.tags.filter(pk=tag.pk).exists()


@pytest.mark.django_db
def test_tag_review_uses_tag_id_after_rename_and_name_reuse(subject):
    from question_bank.ai.actions import review_candidate

    question = Question.objects.create(subject=subject, title="标签改名复用")
    analysis = _review_analysis(
        question,
        tags=[{"name": "原标签", "category": "method", "confidence": 0.9}],
    )
    review_candidate(analysis, "tag", "method:原标签", "confirm")
    old_tag = Tag.objects.get(name="原标签", kind="method")
    old_tag.name = "改名标签"
    old_tag.save(update_fields=["name", "updated_at"])
    replacement = Tag.objects.create(name="原标签", kind="method")

    review_candidate(analysis, "tag", "method:原标签", "revoke")

    assert not question.tags.filter(pk=old_tag.pk).exists()
    assert not question.tags.filter(pk=replacement.pk).exists()
    action = analysis.actions.get(action_type="confirm", candidate_type="tag")
    assert action.payload["tag_id"] == str(old_tag.pk)


@pytest.mark.django_db
def test_review_ownership_is_shared_across_analysis_versions(subject):
    from question_bank.ai.actions import StaleAnalysis, review_candidate

    question = Question.objects.create(subject=subject, title="跨版本所有权")
    card = KnowledgeCard.objects.create(subject=subject, name="跨版本定理", type="theorem")
    first = _review_analysis(
        question,
        knowledge=[{"name": card.name, "matched_card_id": str(card.pk), "confidence": 0.9}],
    )
    review_candidate(first, "knowledge_point", str(card.pk), "confirm")
    second = QuestionAIAnalysis.objects.create(
        question=question,
        version=2,
        input_fingerprint="2" * 64,
        status=Question.AI_STATUS_AWAITING_REVIEW,
        knowledge_points={"items": [{"name": card.name, "matched_card_id": str(card.pk), "confidence": 0.9}]},
    )
    second_result = review_candidate(second, "knowledge_point", str(card.pk), "confirm")
    assert second_result.payload["relation_owned"] is False
    review_candidate(second, "knowledge_point", str(card.pk), "revoke")
    assert question.knowledge_cards.filter(pk=card.pk).exists()
    with pytest.raises(StaleAnalysis):
        review_candidate(first, "knowledge_point", str(card.pk), "revoke")
    assert question.knowledge_cards.filter(pk=card.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("candidate_type,item,key", [
    ("missing_card", {"name": "无效类型", "card_type": "bogus", "confidence": 0.8}, "bogus:无效类型"),
    ("missing_card", {"name": "超长键", "card_type": "other", "confidence": 0.8}, "other:" + "x" * 300),
])
def test_review_rejects_invalid_candidate_boundaries(subject, candidate_type, item, key):
    from question_bank.ai.actions import InvalidCandidate, review_candidate

    question = Question.objects.create(subject=subject, title="候选边界")
    analysis = _review_analysis(question, missing=[item])
    with pytest.raises(InvalidCandidate):
        review_candidate(analysis, candidate_type, key, "confirm")


@pytest.mark.django_db
def test_review_state_is_scoped_to_each_candidate(subject):
    from question_bank.ai.actions import review_candidate

    question = Question.objects.create(subject=subject, title="多个候选独立审核")
    first = KnowledgeCard.objects.create(subject=subject, name="第一候选", type="theorem")
    second = KnowledgeCard.objects.create(subject=subject, name="第二候选", type="theorem")
    analysis = _review_analysis(
        question,
        knowledge=[
            {"name": first.name, "matched_card_id": str(first.pk), "confidence": 0.9},
            {"name": second.name, "matched_card_id": str(second.pk), "confidence": 0.8},
        ],
    )

    review_candidate(analysis, "knowledge_point", str(first.pk), "confirm")
    review_candidate(analysis, "knowledge_point", str(second.pk), "ignore")
    review_candidate(analysis, "knowledge_point", str(first.pk), "revoke")

    assert not question.knowledge_cards.filter(pk=first.pk).exists()
    assert analysis.actions.filter(candidate_key=str(second.pk), action_type="ignore").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("candidate_type", ["knowledge_point", "tag"])
def test_confirm_restores_owned_relation_removed_outside_review(subject, candidate_type):
    from question_bank.ai.actions import review_candidate

    question = Question.objects.create(subject=subject, title="恢复审核拥有的关联")
    if candidate_type == "knowledge_point":
        target = KnowledgeCard.objects.create(subject=subject, name="恢复定理", type="theorem")
        analysis = _review_analysis(
            question,
            knowledge=[{"name": target.name, "matched_card_id": str(target.pk), "confidence": 0.9}],
        )
        key = str(target.pk)
        manager = question.knowledge_cards
    else:
        analysis = _review_analysis(
            question,
            tags=[{"name": "恢复标签", "category": "method", "confidence": 0.9}],
        )
        key = "method:恢复标签"
        target = None
        manager = question.tags

    first = review_candidate(analysis, candidate_type, key, "confirm")
    if target is None:
        target = Tag.objects.get(name="恢复标签", kind="method")
    manager.remove(target)
    first.payload = []
    first.save(update_fields=["payload", "updated_at"])
    second = review_candidate(analysis, candidate_type, key, "confirm")

    assert manager.filter(pk=target.pk).exists()
    assert second.action_type == "confirm"


@pytest.mark.django_db
def test_locked_concurrent_tag_creation_returns_retryable_domain_error(subject, monkeypatch):
    from question_bank.ai.actions import InvalidCandidate, review_candidate

    question = Question.objects.create(subject=subject, title="并发标签锁")
    analysis = _review_analysis(
        question,
        tags=[{"name": "并发标签", "category": "method", "confidence": 0.9}],
    )

    def locked_create(**kwargs):
        raise OperationalError("database is locked")

    monkeypatch.setattr(Tag.objects, "create", locked_create)
    with pytest.raises(InvalidCandidate, match="请重试"):
        review_candidate(analysis, "tag", "method:并发标签", "confirm")


@pytest.mark.django_db
def test_missing_card_can_be_confirmed_again_after_revoke(subject):
    from question_bank.ai.actions import review_candidate

    question = Question.objects.create(subject=subject, title="待建立卡片恢复")
    analysis = _review_analysis(
        question,
        missing=[{"name": "恢复卡片", "card_type": "other", "confidence": 0.8}],
    )
    key = "other:恢复卡片"
    review_candidate(analysis, "missing_card", key, "confirm")
    review_candidate(analysis, "missing_card", key, "revoke")
    review_candidate(analysis, "missing_card", key, "confirm")

    suggestion = analysis.missing_card_suggestions.get(candidate_key=key)
    assert suggestion.status == suggestion.STATUS_PENDING


@pytest.mark.django_db
@pytest.mark.parametrize("candidate_type", ["knowledge_point", "tag"])
def test_revoke_keeps_relation_manually_rebuilt_after_external_remove(subject, candidate_type):
    from question_bank.ai.actions import review_candidate

    question = Question.objects.create(subject=subject, title="人工重建关联")
    if candidate_type == "knowledge_point":
        target = KnowledgeCard.objects.create(subject=subject, name="人工重建定理", type="theorem")
        analysis = _review_analysis(
            question,
            knowledge=[{"name": target.name, "matched_card_id": str(target.pk), "confidence": 0.9}],
        )
        key = str(target.pk)
        manager = question.knowledge_cards
    else:
        analysis = _review_analysis(
            question,
            tags=[{"name": "人工重建标签", "category": "method", "confidence": 0.9}],
        )
        key = "method:人工重建标签"
        target = None
        manager = question.tags

    review_candidate(analysis, candidate_type, key, "confirm")
    if target is None:
        target = Tag.objects.get(name="人工重建标签", kind="method")
    manager.remove(target)
    manager.add(target)
    review_candidate(analysis, candidate_type, key, "revoke")

    assert manager.filter(pk=target.pk).exists()


@pytest.mark.django_db
def test_ownership_migration_resolves_legacy_tag_and_uses_latest_confirm(subject):
    from question_bank.ai.actions import StaleAnalysis, review_candidate
    from question_bank.models import AIReviewOwnership

    question = Question.objects.create(subject=subject, title="旧审核迁移")
    tag = Tag.objects.create(name="旧标签", kind="method")
    question.tags.add(tag)
    first = QuestionAIAnalysis.objects.create(
        question=question,
        version=1,
        input_fingerprint="a" * 64,
        status=Question.AI_STATUS_AWAITING_REVIEW,
        suggested_tags={
            "items": [{"name": "旧标签", "category": "method"}],
        },
    )
    second = QuestionAIAnalysis.objects.create(
        question=question, version=2, input_fingerprint="b" * 64
    )
    owned_action = QuestionAIAnalysisAction.objects.create(
        analysis=first,
        action_type="confirm",
        candidate_type="tag",
        candidate_key="method:旧标签",
        payload={
            "candidate": {"name": "旧标签", "category": "method"},
            "relation_owned": True,
        },
    )
    unowned_action = QuestionAIAnalysisAction.objects.create(
        analysis=second,
        action_type="confirm",
        candidate_type="tag",
        candidate_key="method:旧标签",
        payload={
            "candidate": {"name": "旧标签", "category": "method"},
            "relation_owned": False,
        },
    )

    migration = importlib.import_module(
        "question_bank.migrations.0010_aireviewownership"
    )
    migration.backfill_review_ownerships(apps, None)

    ownership = AIReviewOwnership.objects.get(
        question=question, target_type="tag", target_id=tag.pk
    )
    owned_action.refresh_from_db()
    unowned_action.refresh_from_db()
    assert ownership.owning_analysis == first
    assert owned_action.payload["tag_id"] == str(tag.pk)
    assert unowned_action.payload["tag_id"] == str(tag.pk)

    with pytest.raises(StaleAnalysis):
        review_candidate(first, "tag", "method:旧标签", "revoke")
    assert question.tags.filter(pk=tag.pk).exists()


@pytest.mark.django_db
def test_unknown_tag_category_is_rejected_without_creating_tag(subject):
    from question_bank.ai.actions import InvalidCandidate, review_candidate

    question = Question.objects.create(subject=subject, title="未知标签")
    analysis = _review_analysis(
        question,
        tags=[{"name": "越权类别", "category": "unknown", "confidence": 0.9}],
    )

    with pytest.raises(InvalidCandidate):
        review_candidate(analysis, "tag", "unknown:越权类别", "confirm")

    assert Tag.objects.count() == 0


@pytest.mark.django_db
def test_same_tag_name_can_coexist_in_different_categories(subject):
    from question_bank.ai.actions import review_candidate

    question = Question.objects.create(subject=subject, title="标签类别冲突")
    existing = Tag.objects.create(name="放缩", kind="custom")
    analysis = _review_analysis(
        question,
        tags=[{"name": "放缩", "category": "method", "confidence": 0.9}],
    )

    review_candidate(analysis, "tag", "method:放缩", "confirm")

    method_tag = Tag.objects.get(name="放缩", kind="method")
    assert method_tag.pk != existing.pk
    assert list(question.tags.all()) == [method_tag]


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["confirm", "ignore", "revoke"])
def test_unknown_tag_category_is_rejected_for_every_action(subject, action):
    from question_bank.ai.actions import InvalidCandidate, review_candidate

    question = Question.objects.create(subject=subject, title="未知类别全部动作")
    analysis = _review_analysis(
        question,
        tags=[{"name": "越权类别", "category": "unknown", "confidence": 0.9}],
    )

    with pytest.raises(InvalidCandidate):
        review_candidate(analysis, "tag", "unknown:越权类别", action)

    assert not analysis.actions.exists()


@pytest.mark.django_db
def test_missing_card_confirmation_creates_pending_suggestion_not_formal_card(subject):
    from question_bank.ai.actions import review_candidate

    question = Question.objects.create(subject=subject, title="待建卡片")
    analysis = _review_analysis(
        question,
        missing=[{
            "name": "局部放缩技巧",
            "card_type": "other",
            "confidence": 0.72,
            "reason": "解答使用了局部估计",
            "proof": "模型生成的证明不应发布",
        }],
    )

    review_candidate(analysis, "missing_card", "other:局部放缩技巧", "confirm")

    suggestion = MissingKnowledgeCardSuggestion.objects.get(analysis=analysis)
    assert suggestion.status == MissingKnowledgeCardSuggestion.STATUS_PENDING
    assert suggestion.name == "局部放缩技巧"
    assert suggestion.reason == "解答使用了局部估计"
    assert KnowledgeCard.objects.count() == 0
