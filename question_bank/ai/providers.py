"""Provider boundaries and the OpenAI-compatible relay implementation."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import socket
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .exceptions import AIProviderError, AIProviderTimeoutError, AIResultValidationError
from .schemas import AnalysisResult


logger = logging.getLogger(__name__)

RETRYABLE_HTTP_STATUS = frozenset({408, 429, 500, 502, 503, 504})
CHAT_COMPLETIONS_PATH = ("chat", "completions")


class AnalysisProvider(Protocol):
    provider_name: str
    model_name: str

    def analyze(
        self,
        *,
        question_images: Sequence[Any],
        solution_images: Sequence[Any],
        corrected_text: dict[str, str],
        knowledge_cards: Sequence[Any],
        personal_notes: str,
    ) -> dict[str, Any]: ...


class PlaceholderProvider:
    """Stable local provider used when no remote model is selected."""

    provider_name = "placeholder"
    model_name = "placeholder-analysis-model"

    def analyze(
        self,
        *,
        question_images: Sequence[Any],
        solution_images: Sequence[Any],
        corrected_text: dict[str, str],
        knowledge_cards: Sequence[Any],
        personal_notes: str,
    ) -> dict[str, Any]:
        return {
            "status": "awaiting_review",
            "recognized_statement": corrected_text.get("statement", ""),
            "recognized_solution": corrected_text.get("solution", ""),
            "knowledge_points": [],
            "suggested_tags": [],
            "missing_cards": [],
        }


def _normalize_endpoint(base_url: str) -> str:
    parts = urlsplit(base_url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("AI_BASE_URL must be an absolute HTTP(S) URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("AI_BASE_URL must not contain credentials, query, or fragment")
    path_parts = [part for part in parts.path.split("/") if part]
    if tuple(path_parts[-2:]) != CHAT_COMPLETIONS_PATH:
        path_parts.extend(CHAT_COMPLETIONS_PATH)
    path = "/" + "/".join(path_parts)
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _open_url(request: Request, *, timeout: int):
    return urlopen(request, timeout=timeout)


def _image_data_url(attachment: Any) -> str:
    source = getattr(attachment, "file", attachment)
    name = str(getattr(source, "name", ""))
    mime_type = mimetypes.guess_type(name)[0]
    if not mime_type or not mime_type.startswith("image/"):
        raise AIProviderError("image has an unsupported MIME type")

    content = None
    try:
        if hasattr(source, "open"):
            source.open("rb")
        if hasattr(source, "seek"):
            source.seek(0)
        content = source.read()
    except (OSError, ValueError) as exc:
        raise AIProviderError("image could not be read") from exc
    finally:
        if hasattr(source, "close"):
            source.close()
    if not isinstance(content, bytes):
        raise AIProviderError("image could not be read")
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _card_payload(card: Any) -> dict[str, str]:
    return {
        "id": str(getattr(card, "pk", "")),
        "name": str(getattr(card, "name", "") or ""),
        "type": str(
            getattr(card, "card_type", getattr(card, "type", "")) or ""
        ),
        "formal_statement": str(getattr(card, "formal_statement", "") or ""),
        "conditions": str(getattr(card, "conditions", "") or ""),
        "proof": str(getattr(card, "proof", "") or ""),
        "usage_signals": str(getattr(card, "usage_signals", "") or ""),
    }


def _strip_markdown_fence(content: str) -> str:
    value = content.strip()
    if not value.startswith("```"):
        return value
    first_newline = value.find("\n")
    if first_newline < 0 or not value.endswith("```"):
        return value
    return value[first_newline + 1 : -3].strip()


class RelayProvider:
    """Synchronous client for an OpenAI-compatible chat completions relay."""

    provider_name = "relay"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout_seconds: int = 60,
        max_retries: int = 2,
        max_response_bytes: int = 2 * 1024 * 1024,
        transport: Callable[..., Any] | None = None,
        retry_backoff_seconds: float = 0.25,
    ):
        if not api_key:
            raise ValueError("AI_API_KEY is required for relay provider")
        if not model_name.strip():
            raise ValueError("AI_ANALYSIS_MODEL is required for relay provider")
        if timeout_seconds <= 0:
            raise ValueError("AI_TIMEOUT_SECONDS must be positive")
        if not 0 <= max_retries <= 5:
            raise ValueError("AI_MAX_RETRIES must be in 0..5")
        if max_response_bytes <= 0:
            raise ValueError("AI_MAX_RESPONSE_BYTES must be positive")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")

        self.endpoint = _normalize_endpoint(base_url)
        self._api_key = api_key
        self.model_name = model_name.strip()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_response_bytes = max_response_bytes
        self._transport = transport or _open_url
        self._retry_backoff_seconds = retry_backoff_seconds

    def _messages(
        self,
        *,
        question_images: Sequence[Any],
        solution_images: Sequence[Any],
        corrected_text: dict[str, str],
        knowledge_cards: Sequence[Any],
        personal_notes: str,
    ) -> list[dict[str, Any]]:
        instructions = (
            "分析数学题目和解答，返回严格 JSON 对象。字段必须为 status、"
            "recognized_statement、recognized_solution、knowledge_points、"
            "suggested_tags、missing_cards。status 使用 awaiting_review。"
            "候选置信度范围为 0 到 1，标签 category 仅允许 topic、method、signal。"
        )
        context = {
            "corrected_text": {
                "statement": str(corrected_text.get("statement", "")),
                "solution": str(corrected_text.get("solution", "")),
            },
            "personal_notes": personal_notes,
            "knowledge_cards": [_card_payload(card) for card in knowledge_cards],
        }
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": "输入上下文："
                + json.dumps(context, ensure_ascii=False, separators=(",", ":")),
            }
        ]
        for index, attachment in enumerate(question_images, start=1):
            content.append({"type": "text", "text": f"题目图片 {index}"})
            content.append(
                {"type": "image_url", "image_url": {"url": _image_data_url(attachment)}}
            )
        for index, attachment in enumerate(solution_images, start=1):
            content.append({"type": "text", "text": f"解答图片 {index}"})
            content.append(
                {"type": "image_url", "image_url": {"url": _image_data_url(attachment)}}
            )
        return [
            {"role": "system", "content": instructions},
            {"role": "user", "content": content},
        ]

    def _request(self, messages: list[dict[str, Any]]) -> tuple[bytes, str]:
        request_id = uuid.uuid4().hex
        payload = json.dumps(
            {
                "model": self.model_name,
                "messages": messages,
                "response_format": {"type": "json_object"},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            self.endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Request-ID": request_id,
            },
            method="POST",
        )

        for attempt in range(self.max_retries + 1):
            try:
                with self._transport(request, timeout=self.timeout_seconds) as response:
                    status = getattr(response, "status", 200)
                    if not 200 <= status < 300:
                        raise HTTPError(
                            self.endpoint, status, "relay error", response.headers, response
                        )
                    advertised = response.headers.get("Content-Length")
                    if advertised is not None:
                        try:
                            advertised_size = int(advertised)
                        except (TypeError, ValueError):
                            advertised_size = 0
                        if advertised_size > self.max_response_bytes:
                            raise AIProviderError("relay response exceeded size limit")
                    body = response.read(self.max_response_bytes + 1)
                    if len(body) > self.max_response_bytes:
                        raise AIProviderError("relay response exceeded size limit")
                    return body, request_id
            except HTTPError as exc:
                category = f"http_{exc.code}"
                retryable = exc.code in RETRYABLE_HTTP_STATUS
                logger.warning(
                    "AI relay request failed request_id=%s category=%s",
                    request_id,
                    category,
                )
                if not retryable or attempt >= self.max_retries:
                    raise AIProviderError(
                        f"relay request failed with HTTP status {exc.code}"
                    ) from None
            except (TimeoutError, socket.timeout):
                logger.warning(
                    "AI relay request failed request_id=%s category=timeout", request_id
                )
                if attempt >= self.max_retries:
                    raise AIProviderTimeoutError("relay request timed out") from None
            except URLError as exc:
                category = (
                    "timeout"
                    if isinstance(exc.reason, (TimeoutError, socket.timeout))
                    else "network"
                )
                logger.warning(
                    "AI relay request failed request_id=%s category=%s",
                    request_id,
                    category,
                )
                if attempt >= self.max_retries:
                    if category == "timeout":
                        raise AIProviderTimeoutError("relay request timed out") from None
                    raise AIProviderError("relay network request failed") from None
            except AIProviderError:
                raise
            except (OSError, ValueError):
                logger.warning(
                    "AI relay request failed request_id=%s category=network", request_id
                )
                if attempt >= self.max_retries:
                    raise AIProviderError("relay network request failed") from None

            if self._retry_backoff_seconds:
                time.sleep(self._retry_backoff_seconds * (2**attempt))

        raise AIProviderError("relay request failed")

    def analyze(
        self,
        *,
        question_images: Sequence[Any],
        solution_images: Sequence[Any],
        corrected_text: dict[str, str],
        knowledge_cards: Sequence[Any],
        personal_notes: str,
    ) -> dict[str, Any]:
        messages = self._messages(
            question_images=question_images,
            solution_images=solution_images,
            corrected_text=corrected_text,
            knowledge_cards=knowledge_cards,
            personal_notes=personal_notes,
        )
        body, request_id = self._request(messages)
        try:
            envelope = json.loads(body.decode("utf-8"))
            if not isinstance(envelope, Mapping):
                raise ValueError
            choices = envelope.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ValueError
            first = choices[0]
            if not isinstance(first, Mapping):
                raise ValueError
            message = first.get("message")
            if not isinstance(message, Mapping):
                raise ValueError
            content = message.get("content")
            if not isinstance(content, str):
                raise ValueError
            result_payload = json.loads(_strip_markdown_fence(content))
            result = AnalysisResult.from_dict(result_payload)
        except (UnicodeDecodeError, json.JSONDecodeError, AIResultValidationError, ValueError):
            logger.warning(
                "AI relay request failed request_id=%s category=invalid_response",
                request_id,
            )
            raise AIProviderError("relay returned an invalid response") from None
        return result.to_dict()


def provider_for_config(config: Any) -> AnalysisProvider:
    if getattr(config, "provider", "relay") == "relay":
        return RelayProvider(
            base_url=getattr(config, "base_url", ""),
            api_key=getattr(config, "api_key", ""),
            model_name=getattr(config, "analysis_model", ""),
            timeout_seconds=getattr(config, "timeout_seconds", 60),
            max_retries=getattr(config, "max_retries", 2),
            max_response_bytes=getattr(config, "max_response_bytes", 2 * 1024 * 1024),
        )
    return PlaceholderProvider()
