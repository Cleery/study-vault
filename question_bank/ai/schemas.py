"""Strict, JSON-compatible schemas for provider analysis responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .exceptions import AIResultValidationError


class AnalysisStatus(str, Enum):
    PENDING = "pending"
    ANALYZING = "analyzing"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    FAILED = "failed"


TAG_CATEGORIES = frozenset({"topic", "method", "signal"})
MAX_KNOWLEDGE_POINTS = 10
MAX_SUGGESTED_TAGS = 20


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AIResultValidationError(f"{label}必须是对象")
    return value


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise AIResultValidationError(f"缺少必填字段或类型错误：{key}")
    return value


def _confidence(value: Any, label: str = "confidence") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AIResultValidationError(f"{label}必须是 0 到 1 之间的数字")
    result = float(value)
    if not 0 <= result <= 1:
        raise AIResultValidationError(f"{label}必须是 0 到 1 之间的数字")
    return result


@dataclass(frozen=True)
class KnowledgePointCandidate:
    name: str
    confidence: float
    reason: str = ""
    matched_card_id: str | None = None

    @classmethod
    def from_dict(cls, value: Any) -> "KnowledgePointCandidate":
        data = _mapping(value, "knowledge_points候选")
        name = _required_text(data, "name")
        if not name.strip():
            raise AIResultValidationError("知识点名称不能为空")
        reason = data.get("reason", "")
        if not isinstance(reason, str):
            raise AIResultValidationError("知识点匹配依据必须是文本")
        matched_card_id = data.get("matched_card_id")
        if matched_card_id is not None and not isinstance(matched_card_id, str):
            raise AIResultValidationError("matched_card_id必须是字符串或空值")
        return cls(name=name, confidence=_confidence(data.get("confidence")), reason=reason, matched_card_id=matched_card_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "confidence": self.confidence,
            "reason": self.reason,
            "matched_card_id": self.matched_card_id,
        }


@dataclass(frozen=True)
class TagSuggestion:
    name: str
    category: str
    confidence: float
    reason: str = ""

    @classmethod
    def from_dict(cls, value: Any) -> "TagSuggestion":
        data = _mapping(value, "suggested_tags候选")
        name = _required_text(data, "name")
        if not name.strip():
            raise AIResultValidationError("标签名称不能为空")
        category = _required_text(data, "category")
        if category not in TAG_CATEGORIES:
            raise AIResultValidationError(f"未知标签类别：{category}")
        reason = data.get("reason", "")
        if not isinstance(reason, str):
            raise AIResultValidationError("标签匹配依据必须是文本")
        return cls(name=name, category=category, confidence=_confidence(data.get("confidence")), reason=reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "confidence": self.confidence,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MissingCardCandidate:
    name: str
    confidence: float
    reason: str = ""
    card_type: str = "other"

    @classmethod
    def from_dict(cls, value: Any) -> "MissingCardCandidate":
        data = _mapping(value, "missing_cards候选")
        name = _required_text(data, "name")
        if not name.strip():
            raise AIResultValidationError("待建立知识卡片名称不能为空")
        reason = data.get("reason", "")
        card_type = data.get("card_type", "other")
        if not isinstance(reason, str) or not isinstance(card_type, str):
            raise AIResultValidationError("待建立知识卡片字段类型错误")
        return cls(name=name, confidence=_confidence(data.get("confidence")), reason=reason, card_type=card_type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "confidence": self.confidence,
            "reason": self.reason,
            "card_type": self.card_type,
        }


@dataclass(frozen=True)
class AnalysisResult:
    status: AnalysisStatus
    recognized_statement: str
    recognized_solution: str
    knowledge_points: list[KnowledgePointCandidate] = field(default_factory=list)
    suggested_tags: list[TagSuggestion] = field(default_factory=list)
    missing_cards: list[MissingCardCandidate] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: Any) -> "AnalysisResult":
        data = _mapping(value, "分析结果")
        try:
            status = AnalysisStatus(data.get("status"))
        except ValueError as exc:
            raise AIResultValidationError("分析状态无效") from exc
        statement = _required_text(data, "recognized_statement")
        solution = _required_text(data, "recognized_solution")
        knowledge_values = data.get("knowledge_points")
        tags_values = data.get("suggested_tags")
        missing_values = data.get("missing_cards")
        if not isinstance(knowledge_values, list) or not isinstance(tags_values, list) or not isinstance(missing_values, list):
            raise AIResultValidationError("候选字段必须是数组")
        if len(knowledge_values) > MAX_KNOWLEDGE_POINTS:
            raise AIResultValidationError("知识点候选不能超过 10 个")
        if len(tags_values) > MAX_SUGGESTED_TAGS:
            raise AIResultValidationError("标签候选不能超过 20 个")
        return cls(
            status=status,
            recognized_statement=statement,
            recognized_solution=solution,
            knowledge_points=[KnowledgePointCandidate.from_dict(item) for item in knowledge_values],
            suggested_tags=[TagSuggestion.from_dict(item) for item in tags_values],
            missing_cards=[MissingCardCandidate.from_dict(item) for item in missing_values],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "recognized_statement": self.recognized_statement,
            "recognized_solution": self.recognized_solution,
            "knowledge_points": [item.to_dict() for item in self.knowledge_points],
            "suggested_tags": [item.to_dict() for item in self.suggested_tags],
            "missing_cards": [item.to_dict() for item in self.missing_cards],
        }
