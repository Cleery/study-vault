import pytest

from question_bank.models import (
    KnowledgeCard,
    MissingKnowledgeCardSuggestion,
    Question,
    QuestionAIAnalysis,
    QuestionAIAnalysisAction,
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
    from question_bank.ai.actions import review_candidate

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
    review_candidate(first, "knowledge_point", str(card.pk), "revoke")
    assert not question.knowledge_cards.filter(pk=card.pk).exists()


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
