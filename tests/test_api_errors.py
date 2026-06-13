from Perevod.utils.api_errors import api_status_code, is_retryable_api_error


class ConnectTimeout(Exception):
    pass


ConnectTimeout.__module__ = "httpx"


def test_api_status_code_detects_retryable_status_from_text():
    assert api_status_code(RuntimeError("503 UNAVAILABLE")) == 503


def test_is_retryable_api_error_accepts_transport_timeout():
    assert is_retryable_api_error(ConnectTimeout("connect timed out")) is True


def test_is_retryable_api_error_rejects_quota_and_auth_errors():
    assert is_retryable_api_error(RuntimeError("429 RESOURCE_EXHAUSTED quota")) is False
    assert is_retryable_api_error(RuntimeError("401 UNAUTHENTICATED")) is False


def test_is_retryable_api_error_rejects_schema_errors():
    error = RuntimeError("500 INTERNAL response_schema validation failed")

    assert is_retryable_api_error(error) is False
