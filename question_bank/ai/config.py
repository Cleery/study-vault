"""Environment-backed configuration for AI analysis.

The defaults deliberately keep remote calls disabled.  Secrets are read at
runtime and are never included in the representation of :class:`AIConfig`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _env_nonnegative_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if 0 <= parsed <= 1 else default


@dataclass(frozen=True)
class AIConfig:
    """Configuration required by providers and the analysis service."""

    enabled: bool = False
    provider: str = "relay"
    base_url: str = "https://placeholder.example/v1"
    api_key: str = ""
    vision_model: str = "placeholder-vision-model"
    analysis_model: str = "placeholder-analysis-model"
    timeout_seconds: int = 60
    max_retries: int = 2
    max_response_bytes: int = 2 * 1024 * 1024
    raw_response_retention_days: int = 30
    auto_link_threshold: float = 0.9
    review_threshold: float = 0.7

    @classmethod
    def from_env(cls) -> "AIConfig":
        config = cls(
            enabled=_env_bool("AI_ENABLED", cls.enabled),
            provider=os.getenv("AI_PROVIDER", cls.provider).strip() or cls.provider,
            base_url=os.getenv("AI_BASE_URL", cls.base_url).strip() or cls.base_url,
            api_key=os.getenv("AI_API_KEY", cls.api_key),
            vision_model=os.getenv("AI_VISION_MODEL", cls.vision_model).strip()
            or cls.vision_model,
            analysis_model=os.getenv("AI_ANALYSIS_MODEL", cls.analysis_model).strip()
            or cls.analysis_model,
            timeout_seconds=_env_int("AI_TIMEOUT_SECONDS", cls.timeout_seconds),
            max_retries=_env_nonnegative_int("AI_MAX_RETRIES", cls.max_retries),
            max_response_bytes=_env_int(
                "AI_MAX_RESPONSE_BYTES", cls.max_response_bytes
            ),
            raw_response_retention_days=_env_int(
                "AI_RAW_RESPONSE_RETENTION_DAYS", cls.raw_response_retention_days
            ),
            auto_link_threshold=_env_float(
                "AI_AUTO_LINK_THRESHOLD", cls.auto_link_threshold
            ),
            review_threshold=_env_float("AI_REVIEW_THRESHOLD", cls.review_threshold),
        )
        return config.validated()

    def validated(self) -> "AIConfig":
        if not 0 <= self.review_threshold <= self.auto_link_threshold <= 1:
            raise ValueError(
                "AI_REVIEW_THRESHOLD must be <= AI_AUTO_LINK_THRESHOLD, both in 0..1"
            )
        if self.timeout_seconds <= 0:
            raise ValueError("AI_TIMEOUT_SECONDS must be positive")
        if self.max_retries < 0 or self.max_retries > 5:
            raise ValueError("AI_MAX_RETRIES must be in 0..5")
        if self.max_response_bytes <= 0:
            raise ValueError("AI_MAX_RESPONSE_BYTES must be positive")
        if self.raw_response_retention_days <= 0:
            raise ValueError("AI_RAW_RESPONSE_RETENTION_DAYS must be positive")
        return self

    def __repr__(self) -> str:
        # Keep accidental debug output from exposing the provider secret.
        return (
            "AIConfig("
            f"enabled={self.enabled!r}, provider={self.provider!r}, "
            f"base_url={self.base_url!r}, api_key={'***' if self.api_key else ''!r}, "
            f"vision_model={self.vision_model!r}, analysis_model={self.analysis_model!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, "
            f"max_retries={self.max_retries!r}, "
            f"max_response_bytes={self.max_response_bytes!r}, "
            f"raw_response_retention_days={self.raw_response_retention_days!r}, "
            f"auto_link_threshold={self.auto_link_threshold!r}, "
            f"review_threshold={self.review_threshold!r})"
        )
