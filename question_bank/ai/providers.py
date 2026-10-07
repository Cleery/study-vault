"""Provider boundaries and the OpenAI-compatible relay implementation."""

from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import logging
import mimetypes
import queue
import socket
import ssl
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request

from .exceptions import AIProviderError, AIProviderTimeoutError, AIResultValidationError
from .schemas import AnalysisResult


logger = logging.getLogger(__name__)

RETRYABLE_HTTP_STATUS = frozenset({408, 429, 500, 502, 503, 504})
CHAT_COMPLETIONS_PATH = ("chat", "completions")
ALLOWED_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
REDIRECT_HTTP_STATUS = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 3
READ_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class ResolvedAddress:
    family: int
    sockaddr: tuple[Any, ...]
    ip_address: str


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


def _address_is_internal(address: str) -> bool:
    parsed = ipaddress.ip_address(address.split("%", 1)[0])
    return not parsed.is_global


def _validate_target_name(
    endpoint: str,
    *,
    allow_private: bool,
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


def _resolve_target_addresses(
    endpoint: str,
    *,
    allow_private: bool,
    resolver: Callable[..., Any],
    deadline: float,
    monotonic: Callable[[], float],
) -> list[ResolvedAddress]:
    _validate_target_name(endpoint, allow_private=allow_private)
    parts = urlsplit(endpoint)
    hostname = parts.hostname or ""
    try:
        parsed_hostname = ipaddress.ip_address(hostname.split("%", 1)[0])
        family = socket.AF_INET6 if parsed_hostname.version == 6 else socket.AF_INET
        sockaddr = (
            (str(parsed_hostname), parts.port or 443, 0, 0)
            if family == socket.AF_INET6
            else (str(parsed_hostname), parts.port or 443)
        )
        return [ResolvedAddress(family, sockaddr, str(parsed_hostname))]
    except ValueError:
        pass

    results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            results.put((True, resolver(hostname, parts.port or 443)))
        except BaseException as exc:
            results.put((False, exc))

    threading.Thread(target=resolve, daemon=True).start()
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise AIProviderTimeoutError("relay request deadline exceeded during DNS")
    try:
        succeeded, value = results.get(timeout=remaining)
    except queue.Empty:
        raise AIProviderTimeoutError("relay request deadline exceeded during DNS") from None
    if not succeeded:
        raise AIProviderError("relay hostname could not be resolved") from None
    addresses = value
    if not addresses:
        raise AIProviderError("relay hostname could not be resolved")
    resolved: list[ResolvedAddress] = []
    seen: set[tuple[int, tuple[Any, ...]]] = set()
    for entry in addresses:
        family, socktype, _, _, raw_sockaddr = entry
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        if socktype not in {0, socket.SOCK_STREAM}:
            continue
        sockaddr = tuple(raw_sockaddr)
        address = str(sockaddr[0])
        if not allow_private and _address_is_internal(address):
            raise AIProviderError("relay target resolved to a private network")
        key = (family, sockaddr)
        if key not in seen:
            resolved.append(ResolvedAddress(family, sockaddr, address))
            seen.add(key)
    if not resolved:
        raise AIProviderError("relay hostname could not be resolved")
    return resolved


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


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to a validated IP with hostname TLS verification."""

    def __init__(
        self,
        *,
        hostname: str,
        port: int,
        family: int,
        sockaddr: tuple[Any, ...],
        timeout: float,
    ):
        super().__init__(
            host=hostname,
            port=port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._family = family
        self._sockaddr = sockaddr

    def connect_tcp(self, *, timeout: float) -> None:
        raw_socket = socket.socket(self._family, socket.SOCK_STREAM)
        try:
            raw_socket.settimeout(timeout)
            if self.source_address:
                raw_socket.bind(self.source_address)
            raw_socket.connect(self._sockaddr)
        except BaseException:
            raw_socket.close()
            raise
        self.sock = raw_socket

    def start_tls(self, *, timeout: float) -> None:
        if self.sock is None:
            raise http.client.NotConnected()
        raw_socket = self.sock
        raw_socket.settimeout(timeout)
        try:
            self.sock = self._context.wrap_socket(
                raw_socket,
                server_hostname=self.host,
            )
        except BaseException:
            raw_socket.close()
            self.sock = None
            raise

    def set_timeout(self, timeout: float) -> None:
        self.timeout = timeout
        if self.sock is not None:
            self.sock.settimeout(timeout)

    def connect(self) -> None:
        self.connect_tcp(timeout=self.timeout)
        self.start_tls(timeout=self.timeout)


class _PinnedResponse:
    def __init__(self, response: Any, connection: Any):
        self._response = response
        self._connection = connection
        self.status = response.status
        self.headers = response.headers

    def read(self, size: int = -1) -> bytes:
        return self._response.read(size)

    def set_timeout(self, timeout: float) -> None:
        sock = getattr(self._connection, "sock", None)
        if sock is None:
            fp = getattr(self._response, "fp", None)
            raw = getattr(fp, "raw", None)
            sock = getattr(raw, "_sock", None)
        if sock is not None:
            sock.settimeout(timeout)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


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
        connection_factory: Callable[..., Any] = PinnedHTTPSConnection,
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
        _validate_target_name(
            self.endpoint,
            allow_private=allow_private_base_url,
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
        self._transport = transport
        self._connection_factory = connection_factory
        self._retry_backoff_seconds = retry_backoff_seconds
        self._monotonic = monotonic
        self._sleep = sleep

    def _open_response(
        self,
        request: Request,
        *,
        endpoint: str,
        address: ResolvedAddress,
        deadline: float,
        timeout: float,
    ):
        if self._transport is not None:
            return self._transport(request, timeout=timeout)
        parts = urlsplit(endpoint)
        connection = self._connection_factory(
            hostname=parts.hostname or "",
            port=parts.port or 443,
            family=address.family,
            sockaddr=address.sockaddr,
            timeout=timeout,
        )
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        headers = dict(request.header_items())
        headers["Host"] = parts.netloc
        wrapped_response = None
        try:
            if hasattr(connection, "connect_tcp"):
                self._run_connection_stage(
                    lambda timeout: connection.connect_tcp(timeout=timeout),
                    connection=connection,
                    deadline=deadline,
                    phase="TCP connect",
                )
                self._run_connection_stage(
                    lambda timeout: connection.start_tls(timeout=timeout),
                    connection=connection,
                    deadline=deadline,
                    phase="TLS handshake",
                )
            else:
                self._run_connection_stage(
                    lambda timeout: connection.connect(),
                    connection=connection,
                    deadline=deadline,
                    phase="connection",
                )
            self._run_connection_stage(
                lambda timeout: self._send_request(
                    connection,
                    request,
                    path=path,
                    headers=headers,
                    timeout=timeout,
                ),
                connection=connection,
                deadline=deadline,
                phase="request send",
            )
            response = self._run_connection_stage(
                lambda timeout: self._get_response(
                    connection,
                    timeout=timeout,
                ),
                connection=connection,
                deadline=deadline,
                phase="response headers",
            )
            wrapped_response = _PinnedResponse(response, connection)
            return wrapped_response
        finally:
            if wrapped_response is None:
                connection.close()

    @staticmethod
    def _set_connection_timeout(connection: Any, timeout: float) -> None:
        if hasattr(connection, "set_timeout"):
            connection.set_timeout(timeout)
            return
        connection.timeout = timeout
        sock = getattr(connection, "sock", None)
        if sock is not None:
            sock.settimeout(timeout)

    def _run_connection_stage(
        self,
        operation: Callable[[float], Any],
        *,
        connection: Any,
        deadline: float,
        phase: str,
    ) -> Any:
        return self._run_deadline_operation(
            operation,
            deadline=deadline,
            on_timeout=connection.close,
            phase=phase,
        )

    def _run_deadline_operation(
        self,
        operation: Callable[[float], Any],
        *,
        deadline: float,
        on_timeout: Callable[[], None],
        phase: str,
    ) -> Any:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            on_timeout()
            raise AIProviderTimeoutError(
                f"relay request deadline exceeded during {phase}"
            )
        timeout = min(self.timeout_seconds, remaining)
        results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def run() -> None:
            try:
                results.put((True, operation(timeout)))
            except BaseException as exc:
                results.put((False, exc))

        threading.Thread(target=run, daemon=True).start()
        try:
            succeeded, value = results.get(timeout=remaining)
        except queue.Empty:
            on_timeout()
            raise AIProviderTimeoutError(
                f"relay request deadline exceeded during {phase}"
            ) from None
        if self._monotonic() >= deadline:
            on_timeout()
            raise AIProviderTimeoutError(
                f"relay request deadline exceeded during {phase}"
            )
        if not succeeded:
            raise value
        return value

    def _send_request(
        self,
        connection: Any,
        request: Request,
        *,
        path: str,
        headers: dict[str, str],
        timeout: float,
    ) -> None:
        self._set_connection_timeout(connection, timeout)
        connection.request(request.method, path, body=request.data, headers=headers)

    def _get_response(self, connection: Any, *, timeout: float) -> Any:
        self._set_connection_timeout(connection, timeout)
        return connection.getresponse()

    def _read_response(self, response: Any, *, deadline: float) -> bytes:
        chunks: list[bytes] = []
        size = 0
        while True:
            read_size = min(
                READ_CHUNK_BYTES,
                self.max_response_bytes + 1 - size,
            )
            chunk = self._run_deadline_operation(
                lambda timeout: self._read_response_chunk(
                    response,
                    size=read_size,
                    timeout=timeout,
                ),
                deadline=deadline,
                on_timeout=lambda: self._close_response(response),
                phase="response body",
            )
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > self.max_response_bytes:
                raise AIProviderError("relay response exceeded size limit")
        return b"".join(chunks)

    @staticmethod
    def _read_response_chunk(
        response: Any,
        *,
        size: int,
        timeout: float,
    ) -> bytes:
        if hasattr(response, "set_timeout"):
            response.set_timeout(timeout)
        return response.read(size)

    @staticmethod
    def _close_response(response: Any) -> None:
        close = getattr(response, "close", None)
        if close is not None:
            close()

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
        request_headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Request-ID": request_id,
        }

        for attempt in range(self.max_retries + 1):
            endpoint = self.endpoint
            redirect_count = 0
            try:
                while True:
                    addresses = _resolve_target_addresses(
                        endpoint,
                        allow_private=self._allow_private_base_url,
                        resolver=self._resolver,
                        deadline=deadline,
                        monotonic=self._monotonic,
                    )
                    remaining = deadline - self._monotonic()
                    if remaining <= 0:
                        raise AIProviderTimeoutError("relay request deadline exceeded")
                    request = Request(
                        endpoint,
                        data=payload,
                        headers=request_headers,
                        method="POST",
                    )
                    response = None
                    last_network_error: BaseException | None = None
                    candidate_addresses = (
                        addresses[:1] if self._transport is not None else addresses
                    )
                    for address in candidate_addresses:
                        try:
                            response = self._open_response(
                                request,
                                endpoint=endpoint,
                                address=address,
                                deadline=deadline,
                                timeout=min(self.timeout_seconds, remaining),
                            )
                            break
                        except (AIProviderError, http.client.HTTPException):
                            raise
                        except (TimeoutError, socket.timeout, OSError) as exc:
                            last_network_error = exc
                            if self._monotonic() >= deadline:
                                raise AIProviderTimeoutError(
                                    "relay request deadline exceeded"
                                ) from None
                    if response is None:
                        if last_network_error is not None:
                            raise last_network_error
                        raise AIProviderError("relay network request failed")

                    redirected_endpoint = None
                    with response:
                        status = getattr(response, "status", 200)
                        if status in REDIRECT_HTTP_STATUS:
                            location = response.headers.get("Location")
                            if not location:
                                raise AIProviderError("relay redirect missing location")
                            redirected = urljoin(endpoint, location)
                            if _origin(endpoint) != _origin(redirected):
                                raise AIProviderError("cross-origin redirect blocked")
                            redirect_count += 1
                            if redirect_count > MAX_REDIRECTS:
                                raise AIProviderError("relay redirect limit exceeded")
                            redirected_endpoint = redirected
                        if not 200 <= status < 300:
                            if redirected_endpoint is None:
                                raise HTTPError(
                                    endpoint,
                                    status,
                                    "relay error",
                                    response.headers,
                                    response,
                                )
                        if redirected_endpoint is None:
                            advertised = response.headers.get("Content-Length")
                            if advertised is not None:
                                try:
                                    advertised_size = int(advertised)
                                except (TypeError, ValueError):
                                    advertised_size = 0
                                if advertised_size > self.max_response_bytes:
                                    raise AIProviderError(
                                        "relay response exceeded size limit"
                                    )
                            return self._read_response(
                                response, deadline=deadline
                            ), request_id
                    endpoint = redirected_endpoint
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
            except http.client.HTTPException:
                logger.warning(
                    "AI relay request failed request_id=%s category=protocol",
                    request_id,
                )
                raise AIProviderError("relay protocol failure") from None
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
