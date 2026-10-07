import pytest

from question_bank.models import KnowledgeCard, Question, QuestionAIAnalysis


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
    analysis = analyze_question(question, config=AIConfig(enabled=True))
    items = analysis.knowledge_points["items"]
    assert any(item["matched_card_id"] == str(card.pk) for item in items)


@pytest.mark.django_db
def test_analysis_is_idempotent_and_text_change_creates_version(subject):
    from question_bank.ai.config import AIConfig
    from question_bank.ai.service import analyze_question

    question = Question.objects.create(subject=subject, title="版本题", recognized_statement="原文")
    config = AIConfig(enabled=True)
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
