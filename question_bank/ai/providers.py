"""Provider boundaries and the OpenAI-compatible relay implementation."""

from __future__ import annotations

import base64
import ipaddress
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
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .exceptions import AIProviderError, AIProviderTimeoutError, AIResultValidationError
from .schemas import AnalysisResult


logger = logging.getLogger(__name__)

RETRYABLE_HTTP_STATUS = frozenset({408, 429, 500, 502, 503, 504})
CHAT_COMPLETIONS_PATH = ("chat", "completions")
ALLOWED_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})


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


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    default_port = 443 if parts.scheme == "https" else 80
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port or default_port


class SameOriginRedirectHandler(HTTPRedirectHandler):
    """Permit redirects only when credentials remain on the same origin."""

    def __init__(
        self,
        *,
        allow_private: bool = False,
        resolver: Callable[..., Any] = socket.getaddrinfo,
    ):
        super().__init__()
        self._allow_private = allow_private
        self._resolver = resolver

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _origin(req.full_url) != _origin(newurl):
            raise HTTPError(newurl, code, "cross-origin redirect blocked", headers, fp)
        _validate_public_target(
            newurl,
            allow_private=self._allow_private,
            resolver=self._resolver,
        )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _address_is_internal(address: str) -> bool:
    parsed = ipaddress.ip_address(address.split("%", 1)[0])
    return not parsed.is_global


def _validate_public_target(
    endpoint: str,
    *,
    allow_private: bool,
    resolver: Callable[..., Any],
) -> None:
    if allow_private:
        return
    parts = urlsplit(endpoint)
    hostname = parts.hostname or ""
    if hostname.lower() == "localhost" or hostname.lower().endswith(".local"):
        raise ValueError("AI_BASE_URL must not target a private network")
    try:
        is_internal_literal = _address_is_internal(hostname)
    except ValueError:
        is_internal_literal = None
    if is_internal_literal is not None:
        if is_internal_literal:
            raise ValueError("AI_BASE_URL must not target a private network")
        return
    try:
        addresses = resolver(hostname, parts.port or 443)
    except OSError as exc:
        raise ValueError("AI_BASE_URL hostname could not be resolved") from exc
    if not addresses:
        raise ValueError("AI_BASE_URL hostname could not be resolved")
    for entry in addresses:
        sockaddr = entry[4]
        if _address_is_internal(str(sockaddr[0])):
            raise ValueError("AI_BASE_URL must not target a private network")


def _normalize_endpoint(base_url: str) -> str:
    parts = urlsplit(base_url.strip())
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("AI_BASE_URL must use HTTPS")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("AI_BASE_URL must not contain credentials, query, or fragment")
    path_parts = [part for part in parts.path.split("/") if part]
    if tuple(path_parts[-2:]) != CHAT_COMPLETIONS_PATH:
        path_parts.extend(CHAT_COMPLETIONS_PATH)
    path = "/" + "/".join(path_parts)
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _default_transport(*, allow_private: bool, resolver: Callable[..., Any]):
    opener = build_opener(
        SameOriginRedirectHandler(
            allow_private=allow_private,
            resolver=resolver,
        )
    )
    return opener.open


def _image_data_url(attachment: Any, *, byte_limit: int) -> tuple[str, int]:
    source = getattr(attachment, "file", attachment)
    name = str(getattr(source, "name", ""))
    mime_type = mimetypes.guess_type(name)[0]
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise AIProviderError("image has an unsupported MIME type")

    content = None
    try:
        if hasattr(source, "open"):
            source.open("rb")
        if hasattr(source, "seek"):
            source.seek(0)
        content = source.read(byte_limit + 1)
    except (OSError, ValueError) as exc:
        raise AIProviderError("image could not be read") from exc
    finally:
        if hasattr(source, "close"):
            source.close()
    if not isinstance(content, bytes):
        raise AIProviderError("image could not be read")
    if len(content) > byte_limit:
        raise AIProviderError("image payload exceeded total image byte limit")
    detected_mime = None
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        detected_mime = "image/png"
    elif content.startswith(b"\xff\xd8\xff"):
        detected_mime = "image/jpeg"
    elif len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        detected_mime = "image/webp"
    if detected_mime != mime_type:
        raise AIProviderError("image has an unsupported MIME type")
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime_type};base64,{encoded}", len(content)


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
        total_timeout_seconds: int = 90,
        max_retries: int = 2,
        max_response_bytes: int = 2 * 1024 * 1024,
        max_image_count: int = 20,
        max_image_bytes: int = 40 * 1024 * 1024,
        allow_private_base_url: bool = False,
        transport: Callable[..., Any] | None = None,
        retry_backoff_seconds: float = 0.25,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        resolver: Callable[..., Any] = socket.getaddrinfo,
    ):
        if not api_key:
            raise ValueError("AI_API_KEY is required for relay provider")
        if not model_name.strip():
            raise ValueError("AI_ANALYSIS_MODEL is required for relay provider")
        if timeout_seconds <= 0:
            raise ValueError("AI_TIMEOUT_SECONDS must be positive")
        if total_timeout_seconds <= 0:
            raise ValueError("AI_TOTAL_TIMEOUT_SECONDS must be positive")
        if not 0 <= max_retries <= 5:
            raise ValueError("AI_MAX_RETRIES must be in 0..5")
        if max_response_bytes <= 0:
            raise ValueError("AI_MAX_RESPONSE_BYTES must be positive")
        if max_image_count <= 0:
            raise ValueError("AI_MAX_IMAGE_COUNT must be positive")
        if max_image_bytes <= 0:
            raise ValueError("AI_MAX_IMAGE_BYTES must be positive")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")

        self.endpoint = _normalize_endpoint(base_url)
        _validate_public_target(
            self.endpoint,
            allow_private=allow_private_base_url,
            resolver=resolver,
        )
        self._api_key = api_key
        self.model_name = model_name.strip()
        self.timeout_seconds = timeout_seconds
        self.total_timeout_seconds = total_timeout_seconds
        self.max_retries = max_retries
        self.max_response_bytes = max_response_bytes
        self.max_image_count = max_image_count
        self.max_image_bytes = max_image_bytes
        self._allow_private_base_url = allow_private_base_url
        self._resolver = resolver
        self._transport = transport or _default_transport(
            allow_private=allow_private_base_url,
            resolver=resolver,
        )
        self._retry_backoff_seconds = retry_backoff_seconds
        self._monotonic = monotonic
        self._sleep = sleep

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
        images = list(question_images) + list(solution_images)
        if len(images) > self.max_image_count:
            raise AIProviderError("image count limit exceeded")
        remaining_bytes = self.max_image_bytes
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": "输入上下文："
                + json.dumps(context, ensure_ascii=False, separators=(",", ":")),
            }
        ]
        for index, attachment in enumerate(question_images, start=1):
            content.append({"type": "text", "text": f"题目图片 {index}"})
            data_url, consumed = _image_data_url(
                attachment, byte_limit=remaining_bytes
            )
            remaining_bytes -= consumed
            content.append(
                {"type": "image_url", "image_url": {"url": data_url}}
            )
        for index, attachment in enumerate(solution_images, start=1):
            content.append({"type": "text", "text": f"解答图片 {index}"})
            data_url, consumed = _image_data_url(
                attachment, byte_limit=remaining_bytes
            )
            remaining_bytes -= consumed
            content.append(
                {"type": "image_url", "image_url": {"url": data_url}}
            )
        return [
            {"role": "system", "content": instructions},
            {"role": "user", "content": content},
        ]

    def _request(
        self, messages: list[dict[str, Any]], *, deadline: float
    ) -> tuple[bytes, str]:
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
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise AIProviderTimeoutError("relay request deadline exceeded")
            _validate_public_target(
                self.endpoint,
                allow_private=self._allow_private_base_url,
                resolver=self._resolver,
            )
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise AIProviderTimeoutError("relay request deadline exceeded")
            try:
                with self._transport(
                    request, timeout=min(self.timeout_seconds, remaining)
                ) as response:
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
                    if self._monotonic() >= deadline:
                        raise AIProviderTimeoutError("relay request deadline exceeded")
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
            except AIProviderTimeoutError:
                raise
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

            delay = self._retry_backoff_seconds * (2**attempt)
            if delay:
                remaining = deadline - self._monotonic()
                if delay >= remaining:
                    raise AIProviderTimeoutError("relay request deadline exceeded")
                self._sleep(delay)

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
        started = self._monotonic()
        messages = self._messages(
            question_images=question_images,
            solution_images=solution_images,
            corrected_text=corrected_text,
            knowledge_cards=knowledge_cards,
            personal_notes=personal_notes,
        )
        body, request_id = self._request(
            messages, deadline=started + self.total_timeout_seconds
        )
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
            total_timeout_seconds=getattr(config, "total_timeout_seconds", 90),
            max_retries=getattr(config, "max_retries", 2),
            max_response_bytes=getattr(config, "max_response_bytes", 2 * 1024 * 1024),
            max_image_count=getattr(config, "max_image_count", 20),
            max_image_bytes=getattr(config, "max_image_bytes", 40 * 1024 * 1024),
            allow_private_base_url=getattr(config, "allow_private_base_url", False),
        )
    return PlaceholderProvider()
