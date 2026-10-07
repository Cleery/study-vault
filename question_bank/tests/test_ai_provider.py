import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request

import pytest


VALID_RESULT = {
    "status": "awaiting_review",
    "recognized_statement": "识别题干",
    "recognized_solution": "识别解答",
    "knowledge_points": [],
    "suggested_tags": [],
    "missing_cards": [],
}


class FakeResponse:
    def __init__(self, payload, *, headers=None):
        self.status = 200
        self.headers = headers or {}
        self._stream = io.BytesIO(payload)

    def read(self, size=-1):
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class SequenceTransport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class NamedBytesIO(io.BytesIO):
    def __init__(self, content, name):
        super().__init__(content)
        self.name = name


def openai_response(content, *, headers=None):
    body = json.dumps(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "vision-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    return FakeResponse(body, headers=headers)


def image(name, content):
    suffix = name.rsplit(".", 1)[-1].lower()
    if suffix == "png":
        content = b"\x89PNG\r\n\x1a\n" + content
    elif suffix in {"jpg", "jpeg"}:
        content = b"\xff\xd8\xff" + content
    elif suffix == "webp":
        content = b"RIFF\x00\x00\x00\x00WEBP" + content
    return SimpleNamespace(file=NamedBytesIO(content, name))


def provider(transport, **overrides):
    from question_bank.ai.providers import RelayProvider

    values = {
        "base_url": "https://relay.example///v1/",
        "api_key": "unit-test-key",
        "model_name": "vision-model",
        "timeout_seconds": 17,
        "max_retries": 2,
        "max_response_bytes": 1024 * 1024,
        "transport": transport,
        "retry_backoff_seconds": 0,
        "total_timeout_seconds": 30,
        "resolver": lambda host, port: [
            (2, 1, 6, "", ("8.8.8.8", port))
        ],
    }
    values.update(overrides)
    return RelayProvider(**values)


def analyze(relay, **overrides):
    values = {
        "question_images": [
            image("question.png", b"question-one"),
            image("second.jpg", b"question-two"),
        ],
        "solution_images": [image("solution.webp", b"solution-one")],
        "corrected_text": {"statement": "校对题干", "solution": "校对解答"},
        "knowledge_cards": [
            SimpleNamespace(
                pk=7,
                name="介值定理",
                card_type="theorem",
                formal_statement="连续函数取中间值",
                conditions="闭区间连续",
                proof="二分法",
                usage_signals="端点异号",
            )
        ],
        "personal_notes": "我总是遗漏闭区间条件",
    }
    values.update(overrides)
    return relay.analyze(**values)


def test_relay_normalizes_endpoint_and_sends_openai_vision_request():
    fenced = "```json\n" + json.dumps(VALID_RESULT, ensure_ascii=False) + "\n```"
    transport = SequenceTransport(openai_response(fenced))

    result = analyze(provider(transport))

    request, timeout = transport.calls[0]
    headers = dict(request.header_items())
    payload = json.loads(request.data)
    assert request.full_url == "https://relay.example/v1/chat/completions"
    assert headers["Authorization"] == "Bearer unit-test-key"
    assert headers["Content-type"] == "application/json"
    assert timeout == 17
    assert payload["model"] == "vision-model"
    assert payload["response_format"] == {"type": "json_object"}
    assert result == VALID_RESULT

    user_parts = payload["messages"][1]["content"]
    text = "\n".join(part["text"] for part in user_parts if part["type"] == "text")
    urls = [
        part["image_url"]["url"]
        for part in user_parts
        if part["type"] == "image_url"
    ]
    assert "校对题干" in text
    assert "校对解答" in text
    assert "我总是遗漏闭区间条件" in text
    assert "介值定理" in text
    assert "端点异号" in text
    assert "题目图片 1" in text and "题目图片 2" in text and "解答图片 1" in text
    assert urls == [
        "data:image/png;base64,iVBORw0KGgpxdWVzdGlvbi1vbmU=",
        "data:image/jpeg;base64,/9j/cXVlc3Rpb24tdHdv",
        "data:image/webp;base64,UklGRgAAAABXRUJQc29sdXRpb24tb25l",
    ]


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://relay.example", "https://relay.example/chat/completions"),
        ("https://relay.example/v1", "https://relay.example/v1/chat/completions"),
        (
            "https://relay.example/v1/chat/completions/",
            "https://relay.example/v1/chat/completions",
        ),
    ],
)
def test_relay_accepts_common_base_url_forms(base_url, expected):
    transport = SequenceTransport(openai_response(json.dumps(VALID_RESULT)))

    analyze(provider(transport, base_url=base_url))

    assert transport.calls[0][0].full_url == expected


def test_relay_retries_transient_http_failures_a_finite_number_of_times():
    url = "https://relay.example/v1/chat/completions"
    transport = SequenceTransport(
        HTTPError(url, 503, "unavailable", {}, io.BytesIO(b"private body")),
        HTTPError(url, 429, "limited", {}, io.BytesIO(b"private body")),
        openai_response(json.dumps(VALID_RESULT)),
    )

    assert analyze(provider(transport)) == VALID_RESULT
    assert len(transport.calls) == 3


def test_relay_does_not_retry_non_transient_http_failure():
    from question_bank.ai.exceptions import AIProviderError

    secret_body = b"unit-test-key data:image/png;base64,private"
    url = "https://relay.example/v1/chat/completions"
    transport = SequenceTransport(
        HTTPError(url, 400, "bad request", {}, io.BytesIO(secret_body)),
        openai_response(json.dumps(VALID_RESULT)),
    )

    with pytest.raises(AIProviderError) as raised:
        analyze(provider(transport))

    assert len(transport.calls) == 1
    assert "unit-test-key" not in str(raised.value)
    assert "data:image" not in str(raised.value)
    assert "private" not in str(raised.value)


def test_relay_applies_timeout_and_stops_after_retry_limit():
    from question_bank.ai.exceptions import AIProviderTimeoutError

    transport = SequenceTransport(
        TimeoutError("private-1"),
        TimeoutError("private-2"),
        TimeoutError("private-3"),
    )

    with pytest.raises(AIProviderTimeoutError, match="timed out"):
        analyze(provider(transport))

    assert [timeout for _, timeout in transport.calls] == [17, 17, 17]


@pytest.mark.parametrize("advertise_length", [False, True])
def test_relay_rejects_oversized_response_body(advertise_length):
    from question_bank.ai.exceptions import AIProviderError

    body = b"x" * 65
    headers = {"Content-Length": str(len(body))} if advertise_length else {}
    transport = SequenceTransport(FakeResponse(body, headers=headers))

    with pytest.raises(AIProviderError, match="size limit"):
        analyze(provider(transport, max_response_bytes=64, max_retries=0))


@pytest.mark.parametrize(
    "response",
    [
        {"choices": []},
        {"choices": [{"message": {"content": "not-json"}}]},
        {
            "choices": [
                {"message": {"content": json.dumps({"status": "unknown"})}}
            ]
        },
    ],
)
def test_relay_rejects_invalid_openai_or_analysis_schema(response):
    from question_bank.ai.exceptions import AIProviderError

    transport = SequenceTransport(FakeResponse(json.dumps(response).encode()))

    with pytest.raises(AIProviderError, match="invalid response"):
        analyze(provider(transport, max_retries=0))


def test_relay_logs_only_request_id_and_error_category(caplog):
    from question_bank.ai.exceptions import AIProviderError

    personal_notes = "完整个人想法禁止进入日志"
    encoded_image = "cHJpdmF0ZS1pbWFnZQ=="
    url = "https://relay.example/v1/chat/completions"
    transport = SequenceTransport(
        HTTPError(
            url,
            400,
            "unit-test-key",
            {},
            io.BytesIO(f"{personal_notes}{encoded_image}".encode()),
        )
    )

    with caplog.at_level("WARNING"), pytest.raises(AIProviderError):
        analyze(
            provider(transport, max_retries=0),
            question_images=[image("private.png", b"private-image")],
            personal_notes=personal_notes,
        )

    assert "request_id=" in caplog.text
    assert "category=http_400" in caplog.text
    assert "unit-test-key" not in caplog.text
    assert personal_notes not in caplog.text
    assert encoded_image not in caplog.text


def test_provider_for_config_passes_relay_settings():
    from question_bank.ai.config import AIConfig
    from question_bank.ai.providers import provider_for_config

    config = AIConfig(
        enabled=True,
        base_url="https://8.8.8.8/openai/v1/",
        api_key="config-key",
        analysis_model="configured-model",
        timeout_seconds=23,
        max_retries=1,
        max_response_bytes=4096,
    )
    relay = provider_for_config(config)

    assert relay.endpoint == "https://8.8.8.8/openai/v1/chat/completions"
    assert relay.model_name == "configured-model"
    assert relay.timeout_seconds == 23
    assert relay.max_retries == 1
    assert relay.max_response_bytes == 4096


def test_provider_retry_and_response_limits_load_from_environment(monkeypatch):
    from question_bank.ai.config import AIConfig

    monkeypatch.setenv("AI_MAX_RETRIES", "3")
    monkeypatch.setenv("AI_MAX_RESPONSE_BYTES", "8192")

    config = AIConfig.from_env()

    assert config.max_retries == 3
    assert config.max_response_bytes == 8192


def test_provider_retries_can_be_disabled_from_environment(monkeypatch):
    from question_bank.ai.config import AIConfig

    monkeypatch.setenv("AI_MAX_RETRIES", "0")

    assert AIConfig.from_env().max_retries == 0


def test_relay_rejects_cross_origin_redirect_without_forwarding_authorization():
    from question_bank.ai.providers import SameOriginRedirectHandler

    handler = SameOriginRedirectHandler()
    original = Request(
        "https://relay.example/v1/chat/completions",
        headers={"Authorization": "Bearer unit-test-key"},
    )

    with pytest.raises(HTTPError, match="cross-origin redirect blocked"):
        handler.redirect_request(
            original,
            None,
            302,
            "found",
            {},
            "https://attacker.example/collect",
        )


@pytest.mark.parametrize("name", ["image.gif", "image.svg", "image.bmp"])
def test_relay_rejects_image_mime_types_outside_allowlist(name):
    from question_bank.ai.exceptions import AIProviderError

    transport = SequenceTransport(openai_response(json.dumps(VALID_RESULT)))

    with pytest.raises(AIProviderError, match="unsupported MIME"):
        analyze(provider(transport), question_images=[image(name, b"private")])

    assert transport.calls == []


def test_relay_limits_total_image_count_before_sending_request():
    from question_bank.ai.exceptions import AIProviderError

    transport = SequenceTransport(openai_response(json.dumps(VALID_RESULT)))
    images = [image(f"{index}.png", b"x") for index in range(4)]

    with pytest.raises(AIProviderError, match="image count limit"):
        analyze(
            provider(transport, max_image_count=3),
            question_images=images,
            solution_images=[],
        )

    assert transport.calls == []


def test_relay_limits_total_raw_image_bytes_before_sending_request():
    from question_bank.ai.exceptions import AIProviderError

    transport = SequenceTransport(openai_response(json.dumps(VALID_RESULT)))

    with pytest.raises(AIProviderError, match="image byte limit"):
        analyze(
            provider(transport, max_image_bytes=15),
            question_images=[image("one.png", b"123"), image("two.jpg", b"456")],
            solution_images=[],
        )

    assert transport.calls == []


def test_relay_total_deadline_caps_retries_backoff_and_response_read():
    from question_bank.ai.exceptions import AIProviderTimeoutError

    times = iter([0.0, 0.0, 0.0, 2.0, 2.0, 6.0, 6.0, 10.1])
    transport = SequenceTransport(
        TimeoutError("first"),
        TimeoutError("second"),
        openai_response(json.dumps(VALID_RESULT)),
    )
    relay = provider(
        transport,
        timeout_seconds=8,
        total_timeout_seconds=10,
        retry_backoff_seconds=1,
        monotonic=lambda: next(times),
        sleep=lambda seconds: None,
    )

    with pytest.raises(AIProviderTimeoutError, match="deadline"):
        analyze(relay)

    assert [timeout for _, timeout in transport.calls] == [8, 4]


@pytest.mark.parametrize(
    "base_url",
    [
        "http://relay.example/v1",
        "https://localhost/v1",
        "https://relay.local/v1",
        "https://127.0.0.1/v1",
        "https://10.0.0.2/v1",
        "https://169.254.1.1/v1",
        "https://[::1]/v1",
    ],
)
def test_relay_rejects_insecure_or_private_targets_by_default(base_url):
    with pytest.raises(ValueError):
        provider(SequenceTransport(), base_url=base_url)


def test_relay_rejects_hostname_resolving_to_private_address():
    resolver = lambda host, port: [(2, 1, 6, "", ("192.168.1.20", port))]

    with pytest.raises(ValueError, match="private network"):
        provider(
            SequenceTransport(),
            base_url="https://relay.example/v1",
            resolver=resolver,
        )


def test_relay_allows_private_target_only_with_explicit_override():
    relay = provider(
        SequenceTransport(openai_response(json.dumps(VALID_RESULT))),
        base_url="https://127.0.0.1/v1",
        allow_private_base_url=True,
    )

    assert relay.endpoint == "https://127.0.0.1/v1/chat/completions"


def test_relay_revalidates_dns_before_request_to_block_rebinding():
    from question_bank.ai.exceptions import AIProviderError

    resolutions = iter(
        [
            [(2, 1, 6, "", ("8.8.8.8", 443))],
            [(2, 1, 6, "", ("127.0.0.1", 443))],
        ]
    )
    transport = SequenceTransport(openai_response(json.dumps(VALID_RESULT)))
    relay = provider(transport, resolver=lambda host, port: next(resolutions))

    with pytest.raises(ValueError, match="private network"):
        analyze(relay)

    assert transport.calls == []


def test_relay_rejects_response_when_read_finishes_after_total_deadline():
    from question_bank.ai.exceptions import AIProviderTimeoutError

    times = iter([0.0, 0.0, 0.0, 10.1])
    transport = SequenceTransport(openai_response(json.dumps(VALID_RESULT)))
    relay = provider(
        transport,
        total_timeout_seconds=10,
        monotonic=lambda: next(times),
    )

    with pytest.raises(AIProviderTimeoutError, match="deadline"):
        analyze(relay)
