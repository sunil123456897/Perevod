from __future__ import annotations

from dataclasses import dataclass
import re


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
    # httpx transport/protocol exceptions are transient network failures but
    # do NOT inherit from the builtin ConnectionError, so they must be matched
    # by class name (lower-cased in the error fingerprint) or message tokens.
    "remoteprotocolerror",
    "localprotocolerror",
    "protocolerror",
    "transporterror",
    "proxyerror",
    "readerror",
    "writeerror",
    "closeerror",
    "server disconnected",
    "peer closed",
    "connection reset",
    "connection aborted",
    "connection refused",
    "remotedisconnected",
    "remoteendclosed",
    "incompleteread",
)
NON_RETRYABLE_ERROR_TOKENS = (
    "resource_exhausted",
    "quota",
    "permission_denied",
    "unauthenticated",
    "invalid_argument",
    "not_found",
    "schema",
)
API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_-]+")
API_KEY_FIELD_RE = re.compile(r"(?i)(api[_-]?key=)(\S+)")


@dataclass(frozen=True)
class ApiErrorInfo:
    category: str
    retryable: bool
    status_code: int | None
    message: str


class GeminiAPIError(RuntimeError):
    def __init__(
        self,
        *,
        model_name: str,
        operation: str,
        info: ApiErrorInfo,
        original_error: Exception,
    ):
        self.model_name = model_name
        self.operation = operation
        self.category = info.category
        self.retryable = info.retryable
        self.status_code = info.status_code
        self.original_error = original_error
        super().__init__(
            f"Gemini API error [{operation}] model={model_name} "
            f"category={info.category} retryable={info.retryable} "
            f"status={info.status_code}: {info.message}"
        )


def sanitize_api_error_message(message: str) -> str:
    sanitized = API_KEY_FIELD_RE.sub(r"\1[REDACTED]", message)
    return API_KEY_RE.sub("[REDACTED_API_KEY]", sanitized)


def gemini_api_error_metadata(error: Exception) -> dict:
    if not isinstance(error, GeminiAPIError):
        return {}
    return {
        "error_category": error.category,
        "error_retryable": error.retryable,
        "error_status_code": error.status_code,
        "error_operation": error.operation,
        "error_model": error.model_name,
    }


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
    retryable = getattr(error, "retryable", None)
    if isinstance(retryable, bool):
        return retryable

    return classify_api_error(error).retryable


def classify_api_error(error: Exception) -> ApiErrorInfo:
    error_fingerprint = " ".join(
        [
            error.__class__.__module__.lower(),
            error.__class__.__name__.lower(),
            str(error).lower(),
        ]
    )
    status_code = api_status_code(error)
    if any(token in error_fingerprint for token in NON_RETRYABLE_ERROR_TOKENS):
        return ApiErrorInfo(
            category=_api_error_category(error_fingerprint, status_code),
            retryable=False,
            status_code=status_code,
            message=sanitize_api_error_message(str(error)),
        )

    if status_code is not None:
        retryable = status_code in RETRYABLE_STATUS_CODES
        return ApiErrorInfo(
            category="transient" if retryable else "non_retryable",
            retryable=retryable,
            status_code=status_code,
            message=sanitize_api_error_message(str(error)),
        )

    if any(token in error_fingerprint for token in RETRYABLE_ERROR_TOKENS):
        return ApiErrorInfo(
            category="network",
            retryable=True,
            status_code=None,
            message=sanitize_api_error_message(str(error)),
        )
    # httpx/httpcore transport errors (RemoteProtocolError, ConnectError,
    # ReadTimeout, etc.) all derive from httpx.HTTPError but not from the
    # builtin ConnectionError/TimeoutError, so detect them explicitly.
    try:
        import httpx  # local import to avoid hard dependency at import time

        if isinstance(error, httpx.TransportError):
            return ApiErrorInfo(
                category="network",
                retryable=True,
                status_code=None,
                message=sanitize_api_error_message(str(error)),
            )
    except Exception:  # pragma: no cover - httpx always present in this project
        pass
    retryable = isinstance(error, TimeoutError | ConnectionError)
    return ApiErrorInfo(
        category="network" if retryable else "unknown",
        retryable=retryable,
        status_code=None,
        message=sanitize_api_error_message(str(error)),
    )


def _api_error_category(error_fingerprint: str, status_code: int | None) -> str:
    if status_code in {401, 403} or any(
        token in error_fingerprint
        for token in ("permission_denied", "unauthenticated")
    ):
        return "auth"
    if status_code == 429 or any(
        token in error_fingerprint for token in ("resource_exhausted", "quota")
    ):
        return "quota"
    if status_code == 404 or "not_found" in error_fingerprint:
        return "model_not_found"
    if "schema" in error_fingerprint:
        return "schema"
    if status_code == 400 or "invalid_argument" in error_fingerprint:
        return "invalid_request"
    return "non_retryable"
