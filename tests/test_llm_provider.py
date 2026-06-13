from unittest.mock import MagicMock, patch

import pytest

from Perevod.api_usage import ApiUsageLimitExceeded
from Perevod.utils.api_errors import GeminiAPIError
from Perevod.llm_provider import GeminiEmbeddingAdapter, GeminiModelAdapter, LLMProvider


class ConnectTimeout(Exception):
    pass


ConnectTimeout.__module__ = "httpx"


class TrackingLock:
    def __init__(self):
        self.locked = False
        self.enter_count = 0

    def __enter__(self):
        self.locked = True
        self.enter_count += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self.locked = False


class GuardedCallTimes(dict):
    def __init__(self, lock):
        super().__init__()
        self.lock = lock

    def get(self, key, default=None):
        assert self.lock.locked
        return super().get(key, default)

    def __setitem__(self, key, value):
        assert self.lock.locked
        super().__setitem__(key, value)


@patch("Perevod.llm_provider.genai.Client")
def test_llm_provider_rejects_blank_model_name(mock_client):
    provider = LLMProvider({"translation": "   "}, api_key="fake")

    with pytest.raises(ValueError, match="translation"):
        provider.get_model("translation")


@patch("Perevod.llm_provider.genai.Client")
def test_llm_provider_uses_google_genai_client(mock_client):
    provider = LLMProvider({"translation": "gemini-2.5-flash"}, api_key="fake")

    model = provider.get_model("translation")

    mock_client.assert_called_once_with(api_key="fake")
    assert isinstance(model, GeminiModelAdapter)
    assert provider.get_model("translation") is model
    assert provider.usage_tracker is None


@patch("Perevod.llm_provider.genai.Client")
def test_llm_provider_tracks_usage_for_real_looking_api_keys(mock_client):
    usage_tracker = MagicMock()

    provider = LLMProvider(
        {"translation": "gemini-3-flash-preview"},
        api_key="AIza-real-looking-key",
        usage_tracker=usage_tracker,
    )

    assert provider.usage_tracker is usage_tracker
    assert provider.get_model("translation").min_interval_seconds == 12.0


def test_gemini_model_adapter_preserves_generate_content_contract():
    client = MagicMock()
    response = MagicMock(text="ok")
    client.models.generate_content.return_value = response
    adapter = GeminiModelAdapter(client, "gemini-2.5-flash")

    result = adapter.generate_content(
        "prompt",
        generation_config={"temperature": 0.2, "top_p": 0.8},
        request_options={"timeout": 30},
    )

    assert result is response
    kwargs = client.models.generate_content.call_args.kwargs
    assert kwargs["model"] == "gemini-2.5-flash"
    assert kwargs["contents"] == "prompt"
    assert kwargs["config"].temperature == 0.2
    assert kwargs["config"].top_p == 0.8
    assert kwargs["config"].http_options.timeout == 30000


def test_gemini_model_adapter_treats_timeout_as_seconds_for_callers():
    client = MagicMock()
    client.models.generate_content.return_value = MagicMock(text="ok")
    adapter = GeminiModelAdapter(client, "gemini-2.5-flash")

    adapter.generate_content("prompt", request_options={"timeout": 120})

    kwargs = client.models.generate_content.call_args.kwargs
    assert kwargs["config"].http_options.timeout == 120000


def test_gemini_model_adapter_applies_default_timeout_to_direct_calls():
    client = MagicMock()
    client.models.generate_content.return_value = MagicMock(text="ok")
    adapter = GeminiModelAdapter(client, "gemini-3-flash-preview")

    adapter.generate_content("prompt")

    kwargs = client.models.generate_content.call_args.kwargs
    assert kwargs["config"].http_options.timeout == 300000


def test_gemini_model_adapter_retries_temporary_server_errors():
    client = MagicMock()
    response = MagicMock(text="ok")
    temporary_error = RuntimeError(
        "503 UNAVAILABLE. {'error': {'status': 'UNAVAILABLE'}}"
    )
    client.models.generate_content.side_effect = [temporary_error, response]
    sleep = MagicMock()
    adapter = GeminiModelAdapter(client, "gemini-3-flash-preview")

    result = adapter.generate_content(
        "prompt",
        max_retries=2,
        initial_delay=3,
        sleep_func=sleep,
    )

    assert result is response
    assert client.models.generate_content.call_count == 2
    sleep.assert_called_once_with(3)


def test_gemini_model_adapter_retries_transport_timeouts():
    client = MagicMock()
    response = MagicMock(text="ok")
    client.models.generate_content.side_effect = [
        ConnectTimeout("connect timed out"),
        response,
    ]
    sleep = MagicMock()
    adapter = GeminiModelAdapter(client, "gemini-3-flash-preview")

    result = adapter.generate_content(
        "prompt",
        max_retries=2,
        initial_delay=3,
        sleep_func=sleep,
    )

    assert result is response
    assert client.models.generate_content.call_count == 2
    sleep.assert_called_once_with(3)


def test_gemini_model_adapter_reserves_usage_for_each_api_attempt():
    client = MagicMock()
    response = MagicMock(text="ok")
    temporary_error = RuntimeError(
        "503 UNAVAILABLE. {'error': {'status': 'UNAVAILABLE'}}"
    )
    client.models.generate_content.side_effect = [temporary_error, response]
    sleep = MagicMock()
    usage_tracker = MagicMock()
    usage_tracker.reserve_call.side_effect = ["reservation-1", "reservation-2"]
    adapter = GeminiModelAdapter(
        client,
        "gemini-3-flash-preview",
        usage_tracker=usage_tracker,
    )

    adapter.generate_content(
        "prompt",
        max_retries=2,
        initial_delay=3,
        sleep_func=sleep,
    )

    assert usage_tracker.reserve_call.call_count == 2
    usage_tracker.reserve_call.assert_called_with(
        "gemini-3-flash-preview",
        "generateContent",
    )
    usage_tracker.release_call.assert_called_once_with(
        "gemini-3-flash-preview",
        "generateContent",
        reservation_id="reservation-1",
    )
    usage_tracker.record_call.assert_called_once_with(
        "gemini-3-flash-preview",
        "generateContent",
        reservation_id="reservation-2",
    )
    usage_tracker.check_call_available.assert_not_called()


def test_gemini_model_adapter_records_specific_usage_reservation():
    client = MagicMock()
    client.models.generate_content.return_value = MagicMock(text="ok")
    usage_tracker = MagicMock()
    usage_tracker.reserve_call.return_value = "reservation-1"
    adapter = GeminiModelAdapter(
        client,
        "gemini-3-flash-preview",
        usage_tracker=usage_tracker,
    )

    adapter.generate_content("prompt")

    usage_tracker.record_call.assert_called_once_with(
        "gemini-3-flash-preview",
        "generateContent",
        reservation_id="reservation-1",
    )


def test_gemini_model_adapter_rate_limits_tracked_text_calls():
    client = MagicMock()
    client.models.generate_content.return_value = MagicMock(text="ok")
    usage_tracker = MagicMock()
    sleep = MagicMock()
    time_values = iter([100.0, 105.0, 112.0])
    adapter = GeminiModelAdapter(
        client,
        "gemini-3-flash-preview",
        usage_tracker=usage_tracker,
        min_interval_seconds=12.0,
        last_call_times={},
        time_func=lambda: next(time_values),
    )

    adapter.generate_content("first", sleep_func=sleep)
    adapter.generate_content("second", sleep_func=sleep)

    sleep.assert_called_once_with(7.0)
    assert client.models.generate_content.call_count == 2


def test_gemini_model_adapter_guards_shared_rate_limit_state_with_lock():
    client = MagicMock()
    client.models.generate_content.return_value = MagicMock(text="ok")
    usage_tracker = MagicMock()
    lock = TrackingLock()
    adapter = GeminiModelAdapter(
        client,
        "gemini-3-flash-preview",
        usage_tracker=usage_tracker,
        min_interval_seconds=12.0,
        last_call_times=GuardedCallTimes(lock),
        rate_limit_lock=lock,
    )

    adapter.generate_content("first", sleep_func=MagicMock())

    assert lock.enter_count == 1


def test_gemini_model_adapter_stops_before_api_when_daily_limit_is_exhausted():
    client = MagicMock()
    usage_tracker = MagicMock()
    usage_tracker.reserve_call.side_effect = ApiUsageLimitExceeded(
        "limit exhausted"
    )
    adapter = GeminiModelAdapter(
        client,
        "gemini-3-flash-preview",
        usage_tracker=usage_tracker,
    )

    with pytest.raises(ApiUsageLimitExceeded, match="limit exhausted"):
        adapter.generate_content("prompt")

    client.models.generate_content.assert_not_called()
    usage_tracker.record_call.assert_not_called()
    usage_tracker.release_call.assert_not_called()


def test_gemini_model_adapter_does_not_retry_daily_quota_errors():
    client = MagicMock()
    quota_error = RuntimeError(
        "429 RESOURCE_EXHAUSTED. {'error': {'status': 'RESOURCE_EXHAUSTED'}}"
    )
    client.models.generate_content.side_effect = quota_error
    sleep = MagicMock()
    adapter = GeminiModelAdapter(client, "gemini-3-flash-preview")

    with pytest.raises(RuntimeError, match="429 RESOURCE_EXHAUSTED"):
        adapter.generate_content(
            "prompt",
            max_retries=3,
            initial_delay=3,
            sleep_func=sleep,
        )

    assert client.models.generate_content.call_count == 1
    sleep.assert_not_called()


def test_gemini_model_adapter_raises_structured_sanitized_errors():
    client = MagicMock()
    client.models.generate_content.side_effect = RuntimeError(
        "401 UNAUTHENTICATED api_key=AIza-secret-key"
    )
    adapter = GeminiModelAdapter(client, "gemini-3-flash-preview")

    with pytest.raises(GeminiAPIError) as exc_info:
        adapter.generate_content("prompt")

    error = exc_info.value
    assert error.model_name == "gemini-3-flash-preview"
    assert error.operation == "generateContent"
    assert error.status_code == 401
    assert error.category == "auth"
    assert error.retryable is False
    assert "AIza-secret-key" not in str(error)
    assert "api_key=[REDACTED]" in str(error)


def test_gemini_embedding_adapter_preserves_embed_content_contract():
    client = MagicMock()
    response = MagicMock()
    client.models.embed_content.return_value = response
    adapter = GeminiEmbeddingAdapter(client, "gemini-embedding-2")

    result = adapter.embed_content(
        ["one", "two"],
        "RETRIEVAL_DOCUMENT",
        output_dimensionality=768,
        request_options={"timeout": 45},
    )

    assert result is response
    kwargs = client.models.embed_content.call_args.kwargs
    assert kwargs["model"] == "gemini-embedding-2"
    assert kwargs["contents"] == ["one", "two"]
    assert kwargs["config"].task_type == "RETRIEVAL_DOCUMENT"
    assert kwargs["config"].output_dimensionality == 768
    assert kwargs["config"].http_options.timeout == 45000


def test_gemini_embedding_adapter_reserves_usage_and_retries_temporary_errors():
    client = MagicMock()
    response = MagicMock()
    temporary_error = RuntimeError(
        "503 UNAVAILABLE. {'error': {'status': 'UNAVAILABLE'}}"
    )
    client.models.embed_content.side_effect = [temporary_error, response]
    sleep = MagicMock()
    usage_tracker = MagicMock()
    usage_tracker.reserve_call.side_effect = ["reservation-1", "reservation-2"]
    adapter = GeminiEmbeddingAdapter(
        client,
        "gemini-embedding-2",
        usage_tracker=usage_tracker,
    )

    assert (
        adapter.embed_content(
            ["one"],
            "RETRIEVAL_QUERY",
            max_retries=2,
            initial_delay=3,
            sleep_func=sleep,
        )
        is response
    )

    assert client.models.embed_content.call_count == 2
    sleep.assert_called_once_with(3)
    usage_tracker.release_call.assert_called_once_with(
        "gemini-embedding-2",
        "embedContent",
        reservation_id="reservation-1",
    )
    usage_tracker.record_call.assert_called_once_with(
        "gemini-embedding-2",
        "embedContent",
        reservation_id="reservation-2",
    )


def test_gemini_embedding_adapter_does_not_retry_quota_errors():
    client = MagicMock()
    client.models.embed_content.side_effect = RuntimeError(
        "429 RESOURCE_EXHAUSTED. {'error': {'status': 'RESOURCE_EXHAUSTED'}}"
    )
    sleep = MagicMock()
    adapter = GeminiEmbeddingAdapter(client, "gemini-embedding-2")

    with pytest.raises(RuntimeError, match="RESOURCE_EXHAUSTED"):
        adapter.embed_content(
            ["one"],
            "RETRIEVAL_QUERY",
            max_retries=2,
            initial_delay=3,
            sleep_func=sleep,
        )

    assert client.models.embed_content.call_count == 1
    sleep.assert_not_called()


def test_gemini_embedding_adapter_raises_structured_sanitized_errors():
    client = MagicMock()
    client.models.embed_content.side_effect = RuntimeError(
        "404 NOT_FOUND api_key=AIza-secret-key"
    )
    adapter = GeminiEmbeddingAdapter(client, "gemini-embedding-2")

    with pytest.raises(GeminiAPIError) as exc_info:
        adapter.embed_content(["one"], "RETRIEVAL_QUERY")

    error = exc_info.value
    assert error.model_name == "gemini-embedding-2"
    assert error.operation == "embedContent"
    assert error.status_code == 404
    assert error.category == "model_not_found"
    assert error.retryable is False
    assert "AIza-secret-key" not in str(error)
