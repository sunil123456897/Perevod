from __future__ import annotations


RETRYABLE_STATUS_CODES = {500, 502, 503, 504}
NON_RETRYABLE_STATUS_CODES = {400, 401, 403, 404, 429}
RETRYABLE_ERROR_TOKENS = (
    "connecttimeout",
    "readtimeout",
    "writetimeout",
    "pooltimeout",
    "timeoutexception",
    "connecterror",
    "networkerror",
    "serviceunavailable",
    "unavailable",
    "deadlineexceeded",
)
NON_RETRYABLE_ERROR_TOKENS = (
    "resource_exhausted",
    "quota",
    "permission_denied",
    "unauthenticated",
    "invalid_argument",
    "not_found",
)


def api_status_code(error: Exception) -> int | None:
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return status_code

    error_text = str(error)
    for code in RETRYABLE_STATUS_CODES | NON_RETRYABLE_STATUS_CODES:
        if error_text.startswith(str(code)) or f" {code} " in error_text:
            return code
    return None


def is_retryable_api_error(error: Exception) -> bool:
    status_code = api_status_code(error)
    if status_code is not None:
        return status_code in RETRYABLE_STATUS_CODES

    error_fingerprint = " ".join(
        [
            error.__class__.__module__.lower(),
            error.__class__.__name__.lower(),
            str(error).lower(),
        ]
    )
    if any(token in error_fingerprint for token in NON_RETRYABLE_ERROR_TOKENS):
        return False
    if any(token in error_fingerprint for token in RETRYABLE_ERROR_TOKENS):
        return True
    return isinstance(error, TimeoutError | ConnectionError)
