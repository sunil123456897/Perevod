from unittest.mock import MagicMock

import pytest
from google.api_core import exceptions as google_exceptions

from Perevod.utils import llm
from Perevod.utils.llm import generate_text, safe_json_loads, tool_translate_chunk


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


def test_safe_json_loads_extracts_json_from_markdown_response():
    response_text = """
    Here is the result:

    ```json
    {"found_terms": [{"english_term": "Dawnkeep"}]}
    ```
    """

    assert safe_json_loads(response_text) == {
        "found_terms": [{"english_term": "Dawnkeep"}]
    }


def test_safe_json_loads_preserves_explicit_default_for_invalid_json():
    assert safe_json_loads("{invalid json", default=[]) == []
    assert safe_json_loads("", default=None) == {}


def test_safe_json_loads_returns_default_for_non_string_payload():
    assert safe_json_loads(None, default={"found_terms": []}) == {"found_terms": []}


def test_tool_translate_chunk_returns_empty_string_when_response_text_unavailable():
    response = MagicMock()
    type(response).text = property(
        lambda self: (_ for _ in ()).throw(ValueError("blocked response"))
    )
    response.prompt_feedback = "blocked"

    model = MagicMock()
    model.generate_content.return_value = response
    settings = MagicMock(temperature=0.5, top_p=0.9)

    assert tool_translate_chunk(model, "prompt", settings) == ""


def test_tool_translate_chunk_accepts_dict_settings():
    response = MagicMock()
    response.text = "Перевод"
    model = MagicMock()
    model.generate_content.return_value = response

    assert tool_translate_chunk(model, "prompt", {"temperature": 0.2, "top_p": 0.8}) == "Перевод"
    model.generate_content.assert_called_once_with(
        "prompt",
        generation_config={"temperature": 0.2, "top_p": 0.8},
        request_options={"timeout": 300},
    )


def test_generate_text_retries_transient_errors_without_sleeping_for_tests():
    response = MagicMock(text="ok")
    model = MagicMock()
    model.generate_content.side_effect = [
        google_exceptions.ServiceUnavailable("temporary"),
        response,
    ]
    sleep = MagicMock()

    result = generate_text(
        model,
        "prompt",
        {"temperature": 0.1, "top_p": 0.2},
        max_retries=2,
        initial_delay=0.5,
        sleep_func=sleep,
    )

    assert result == "ok"
    assert model.generate_content.call_count == 2
    sleep.assert_called_once_with(0.5)


def test_generate_text_passes_timeout_request_option():
    response = MagicMock(text="ok")
    model = MagicMock()
    model.generate_content.return_value = response

    generate_text(model, "prompt", {"request_timeout": 30})

    model.generate_content.assert_called_once_with(
        "prompt",
        generation_config={"temperature": 0.7, "top_p": 0.9},
        request_options={"timeout": 30},
    )


def test_generate_text_raises_after_retry_budget_is_exhausted():
    model = MagicMock()
    model.generate_content.side_effect = google_exceptions.ServiceUnavailable("down")

    with pytest.raises(google_exceptions.ServiceUnavailable):
        generate_text(
            model,
            "prompt",
            {},
            max_retries=2,
            initial_delay=0,
            sleep_func=MagicMock(),
        )

    assert model.generate_content.call_count == 2


def test_generate_text_does_not_retry_quota_errors():
    model = MagicMock()
    model.generate_content.side_effect = google_exceptions.ResourceExhausted("quota")
    sleep = MagicMock()

    with pytest.raises(google_exceptions.ResourceExhausted):
        generate_text(
            model,
            "prompt",
            {},
            max_retries=3,
            initial_delay=0,
            sleep_func=sleep,
        )

    assert model.generate_content.call_count == 1
    sleep.assert_not_called()


def test_generate_text_throttles_gemini_3_flash_rpm(monkeypatch):
    llm._LAST_REQUEST_AT.clear()
    times = iter([100.0, 105.0, 112.0])
    monkeypatch.setattr(llm.time, "monotonic", lambda: next(times))

    response = MagicMock(text="ok")
    model = MagicMock()
    model.model_name = "gemini-3-flash-preview"
    model.generate_content.return_value = response
    sleep = MagicMock()

    generate_text(model, "prompt", {}, sleep_func=sleep)
    generate_text(model, "prompt", {}, sleep_func=sleep)

    sleep.assert_called_once_with(7.0)
    assert model.generate_content.call_count == 2


def test_generate_text_does_not_throttle_unknown_model():
    llm._LAST_REQUEST_AT.clear()
    response = MagicMock(text="ok")
    model = MagicMock()
    model.model_name = "other-model"
    model.generate_content.return_value = response
    sleep = MagicMock()

    generate_text(model, "prompt", {}, sleep_func=sleep)
    generate_text(model, "prompt", {}, sleep_func=sleep)

    sleep.assert_not_called()


def test_generate_text_guards_shared_rate_limit_state_with_lock(monkeypatch):
    lock = TrackingLock()
    monkeypatch.setattr(llm, "_LAST_REQUEST_AT", GuardedCallTimes(lock))
    monkeypatch.setattr(llm, "_RATE_LIMIT_LOCK", lock)
    response = MagicMock(text="ok")
    model = MagicMock()
    model.model_name = "gemini-3-flash-preview"
    model.generate_content.return_value = response

    generate_text(model, "prompt", {}, sleep_func=MagicMock())

    assert lock.enter_count == 1
