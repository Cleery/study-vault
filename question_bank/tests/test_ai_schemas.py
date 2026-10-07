import pytest


def _valid_payload():
    return {
        "status": "awaiting_review",
        "recognized_statement": "设 f 在 [a,b] 上连续。",
        "recognized_solution": "由介值定理可得。",
        "knowledge_points": [
            {
                "name": "介值定理",
                "confidence": 0.96,
                "reason": "题目给出闭区间连续条件。",
                "matched_card_id": "card-1",
            }
        ],
        "suggested_tags": [
            {"name": "连续性", "category": "topic", "confidence": 0.91}
        ],
        "missing_cards": [],
    }


def test_valid_analysis_result_is_parsed_and_serialized():
    from question_bank.ai.schemas import AnalysisResult, AnalysisStatus

    result = AnalysisResult.from_dict(_valid_payload())

    assert result.status is AnalysisStatus.AWAITING_REVIEW
    assert result.knowledge_points[0].name == "介值定理"
    assert result.suggested_tags[0].category == "topic"
    assert result.to_dict()["status"] == "awaiting_review"


@pytest.mark.parametrize("category", ["other", "knowledge", "methodology", ""])
def test_unknown_tag_category_is_rejected(category):
    from question_bank.ai.exceptions import AIResultValidationError
    from question_bank.ai.schemas import AnalysisResult

    payload = _valid_payload()
    payload["suggested_tags"][0]["category"] = category

    with pytest.raises(AIResultValidationError):
        AnalysisResult.from_dict(payload)


@pytest.mark.parametrize("confidence", [-0.01, 1.01, "high", None])
def test_confidence_must_be_between_zero_and_one(confidence):
    from question_bank.ai.exceptions import AIResultValidationError
    from question_bank.ai.schemas import AnalysisResult

    payload = _valid_payload()
    payload["knowledge_points"][0]["confidence"] = confidence

    with pytest.raises(AIResultValidationError):
        AnalysisResult.from_dict(payload)


def test_candidate_limits_are_enforced():
    from question_bank.ai.exceptions import AIResultValidationError
    from question_bank.ai.schemas import AnalysisResult

    payload = _valid_payload()
    payload["knowledge_points"] = [
        {"name": str(index), "confidence": 0.5, "reason": "r"}
        for index in range(11)
    ]
    with pytest.raises(AIResultValidationError):
        AnalysisResult.from_dict(payload)

    payload = _valid_payload()
    payload["suggested_tags"] = [
        {"name": str(index), "category": "topic", "confidence": 0.5}
        for index in range(21)
    ]
    with pytest.raises(AIResultValidationError):
        AnalysisResult.from_dict(payload)


def test_required_fields_and_status_are_validated():
    from question_bank.ai.exceptions import AIResultValidationError
    from question_bank.ai.schemas import AnalysisResult

    payload = _valid_payload()
    del payload["recognized_statement"]
    with pytest.raises(AIResultValidationError):
        AnalysisResult.from_dict(payload)

    payload = _valid_payload()
    payload["status"] = "unknown"
    with pytest.raises(AIResultValidationError):
        AnalysisResult.from_dict(payload)


def test_configuration_uses_safe_defaults(monkeypatch):
    from question_bank.ai.config import AIConfig

    for key in (
        "AI_ENABLED",
        "AI_PROVIDER",
        "AI_BASE_URL",
        "AI_API_KEY",
        "AI_VISION_MODEL",
        "AI_ANALYSIS_MODEL",
        "AI_TIMEOUT_SECONDS",
        "AI_AUTO_LINK_THRESHOLD",
        "AI_REVIEW_THRESHOLD",
    ):
        monkeypatch.delenv(key, raising=False)

    config = AIConfig.from_env()

    assert config.enabled is False
    assert config.provider == "relay"
    assert config.base_url == "https://placeholder.example/v1"
    assert config.api_key == ""
    assert config.vision_model == "placeholder-vision-model"
    assert config.analysis_model == "placeholder-analysis-model"
    assert config.timeout_seconds == 60
    assert config.auto_link_threshold == 0.9
    assert config.review_threshold == 0.7
