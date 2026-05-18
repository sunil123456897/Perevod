import argparse
import importlib.util
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from google.api_core import exceptions as google_exceptions

from Perevod.api_usage import (
    ApiUsageTracker,
    is_placeholder_api_key,
    should_track_api_usage,
)
from Perevod.config import (
    normalize_embedding_model,
    normalize_model_configs,
    settings,
)
from Perevod.llm_provider import LLMProvider
from Perevod.project_manager import ProjectManager

STATUS_OK = "OK"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    message: str


@dataclass(frozen=True)
class DoctorReport:
    checks: list[DoctorCheck]
    exit_code: int


def check_directory(name: str, value: str | None, *, required: bool, create: bool = False) -> DoctorCheck:
    if not value:
        status = STATUS_FAIL if required else STATUS_WARN
        return DoctorCheck(name, status, "not configured")

    path = Path(value)
    if path.exists() and path.is_dir():
        return DoctorCheck(name, STATUS_OK, str(path))

    if create:
        try:
            path.mkdir(parents=True, exist_ok=True)
            return DoctorCheck(name, STATUS_OK, f"created {path}")
        except OSError as exc:
            return DoctorCheck(name, STATUS_FAIL, f"cannot create: {exc}")

    return DoctorCheck(name, STATUS_FAIL, f"not found: {path}")


def _check_python() -> DoctorCheck:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info < (3, 10):
        return DoctorCheck("python", STATUS_FAIL, f"{version}; Python >= 3.10 required")
    return DoctorCheck("python", STATUS_OK, version)


def _check_package(import_name: str, label: str | None = None) -> DoctorCheck:
    package_label = label or import_name
    if importlib.util.find_spec(import_name) is None:
        return DoctorCheck(package_label, STATUS_FAIL, "not installed")
    return DoctorCheck(package_label, STATUS_OK, "installed")


def _check_chromadb() -> DoctorCheck:
    if importlib.util.find_spec("chromadb") is None:
        return DoctorCheck("chromadb", STATUS_FAIL, "not installed")
    try:
        import chromadb.config
    except Exception as exc:
        return DoctorCheck("chromadb", STATUS_FAIL, f"import failed: {exc}")
    if getattr(chromadb.config, "is_thin_client", False):
        return DoctorCheck(
            "chromadb",
            STATUS_FAIL,
            "chromadb-client is installed; local PersistentClient is unavailable",
        )
    return DoctorCheck("chromadb", STATUS_OK, "local client available")


def _check_api_key(project_settings: dict) -> DoctorCheck:
    api_key = project_settings.get("GOOGLE_API_KEY") or settings.GOOGLE_API_KEY
    if not api_key:
        return DoctorCheck("GOOGLE_API_KEY", STATUS_WARN, "not configured; API checks skipped")
    if is_placeholder_api_key(api_key):
        return DoctorCheck(
            "GOOGLE_API_KEY",
            STATUS_WARN,
            "test/fake API key configured; live API checks require a real key",
        )
    return DoctorCheck("GOOGLE_API_KEY", STATUS_OK, "configured")


def _redact_proxy_url(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return "<set>"
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


def _check_proxy_environment() -> DoctorCheck:
    configured = []
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        value = os.environ.get(name)
        if value:
            configured.append(f"{name}={_redact_proxy_url(value)}")
    if configured:
        return DoctorCheck("proxy env", STATUS_OK, "; ".join(configured))
    return DoctorCheck(
        "proxy env",
        STATUS_WARN,
        "HTTP_PROXY/HTTPS_PROXY not set for this process",
    )


def _check_dns_resolution() -> DoctorCheck:
    host = "generativelanguage.googleapis.com"
    try:
        addresses = sorted(
            {
                item[4][0]
                for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                if item[4]
            }
        )
    except OSError as exc:
        return DoctorCheck("Gemini DNS", STATUS_FAIL, f"{host} resolution failed: {exc}")
    if not addresses:
        return DoctorCheck("Gemini DNS", STATUS_FAIL, f"{host} resolved to no addresses")
    return DoctorCheck("Gemini DNS", STATUS_OK, f"{host} -> {', '.join(addresses)}")


def _project_model_names(project_settings: dict) -> set[str]:
    model_configs, embedding_model, _free_tier_mode = _project_model_profile(
        project_settings
    )
    return {
        model_name
        for model_name in [*model_configs.values(), embedding_model]
        if model_name
    }


def _project_model_profile(project_settings: dict) -> tuple[dict[str, str], str, bool]:
    free_tier_mode = project_settings.get(
        "gemini_free_tier_mode",
        settings.gemini_free_tier_mode,
    )
    model_configs = normalize_model_configs(
        {
            "analysis": project_settings.get(
                "analysis_model_name",
                settings.analysis_model_name,
            ),
            "curation": project_settings.get(
                "curation_model_name",
                settings.curation_model_name,
            ),
            "translation": project_settings.get(
                "translation_model_name",
                settings.translation_model_name,
            ),
            "qa": project_settings.get("qa_model_name", settings.qa_model_name),
            "judge": project_settings.get("judge_model_name", settings.qa_model_name),
            "editor": project_settings.get(
                "editor_model_name",
                settings.translation_model_name,
            ),
            "summarization": project_settings.get(
                "summarization_model_name",
                settings.summarization_model_name,
            ),
        },
        free_tier_mode=free_tier_mode,
    )
    embedding_model = normalize_embedding_model(
        project_settings.get("embedding_model_name", settings.embedding_model_name),
        free_tier_mode=free_tier_mode,
    )
    return model_configs, embedding_model, free_tier_mode


def _check_model_profile(project_settings: dict) -> DoctorCheck:
    model_configs, embedding_model, free_tier_mode = _project_model_profile(
        project_settings
    )
    ordered_tasks = [
        "analysis",
        "curation",
        "translation",
        "qa",
        "judge",
        "editor",
        "summarization",
    ]
    task_summary = "; ".join(
        f"{task}={model_configs[task]}"
        for task in ordered_tasks
        if model_configs.get(task)
    )
    return DoctorCheck(
        "Gemini model profile",
        STATUS_OK,
        f"free_tier={free_tier_mode}; {task_summary}; embedding={embedding_model}",
    )


def _check_api_usage_budget(
    project_settings: dict,
    usage_tracker: ApiUsageTracker | None = None,
) -> DoctorCheck:
    api_key = project_settings.get("GOOGLE_API_KEY") or settings.GOOGLE_API_KEY
    if usage_tracker is None and not should_track_api_usage(api_key):
        reason = (
            "GOOGLE_API_KEY is not configured"
            if not (api_key or "").strip()
            else "test API key does not use quota tracking"
        )
        return DoctorCheck("Gemini daily budget", STATUS_WARN, f"skipped; {reason}")

    tracker = usage_tracker or ApiUsageTracker()
    statuses = []
    messages = []
    for model_name in sorted(_project_model_names(project_settings)):
        used_calls, daily_limit = tracker.get_daily_usage(
            model_name,
            include_reserved=True,
        )
        if daily_limit is None:
            continue
        if used_calls >= daily_limit:
            statuses.append(STATUS_FAIL)
        elif used_calls >= daily_limit * 0.8:
            statuses.append(STATUS_WARN)
        else:
            statuses.append(STATUS_OK)
        messages.append(f"{model_name}: {used_calls}/{daily_limit}")

    if not messages:
        return DoctorCheck("Gemini daily budget", STATUS_WARN, "no tracked models")
    if STATUS_FAIL in statuses:
        status = STATUS_FAIL
    elif STATUS_WARN in statuses:
        status = STATUS_WARN
    else:
        status = STATUS_OK
    return DoctorCheck("Gemini daily budget", status, "; ".join(messages))


def _check_api_connectivity(
    enabled: bool, api_key: str, model_name: str, api_timeout: int
) -> DoctorCheck:
    if not enabled:
        return DoctorCheck(
            "Gemini API",
            STATUS_WARN,
            "skipped; use --check-api when VPN/API access is ready",
        )
    if not api_key:
        return DoctorCheck("Gemini API", STATUS_FAIL, "GOOGLE_API_KEY is not configured")
    if is_placeholder_api_key(api_key):
        return DoctorCheck(
            "Gemini API",
            STATUS_FAIL,
            "GOOGLE_API_KEY is a test/fake placeholder; live API check requires a real key",
        )
    if not model_name:
        return DoctorCheck("Gemini API", STATUS_FAIL, "translation_model_name is not configured")

    try:
        provider = LLMProvider({"doctor": model_name}, api_key=api_key)
        model = provider.get_model("doctor")
        response = model.generate_content(
            "Reply with exactly: OK",
            generation_config={"temperature": 0.0, "top_p": 1.0},
            request_options={"timeout": api_timeout},
        )
        text = getattr(response, "text", "").strip()
        if not text:
            return DoctorCheck("Gemini API", STATUS_FAIL, "empty response")
        return DoctorCheck("Gemini API", STATUS_OK, f"model {model_name} responded")
    except TimeoutError as exc:
        if getattr(exc, "winerror", None) == 10060 or getattr(exc, "errno", None) == 10060:
            return DoctorCheck(
                "Gemini API",
                STATUS_FAIL,
                f"network/VPN/proxy connection timeout (WinError 10060): {exc}",
            )
        return DoctorCheck(
            "Gemini API",
            STATUS_FAIL,
            f"timed out after {api_timeout}s; check VPN/network access to Gemini",
        )
    except google_exceptions.DeadlineExceeded:
        return DoctorCheck(
            "Gemini API",
            STATUS_FAIL,
            f"timed out after {api_timeout}s; check VPN/network access to Gemini",
        )
    except google_exceptions.PermissionDenied as exc:
        return DoctorCheck("Gemini API", STATUS_FAIL, f"permission/auth error: {exc}")
    except google_exceptions.Unauthenticated as exc:
        return DoctorCheck("Gemini API", STATUS_FAIL, f"permission/auth error: {exc}")
    except google_exceptions.ResourceExhausted as exc:
        return DoctorCheck("Gemini API", STATUS_FAIL, f"quota/rate limit error: {exc}")
    except google_exceptions.ServiceUnavailable as exc:
        return DoctorCheck("Gemini API", STATUS_FAIL, f"service/network unavailable: {exc}")
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10060 or getattr(exc, "errno", None) == 10060:
            return DoctorCheck(
                "Gemini API",
                STATUS_FAIL,
                f"network/VPN/proxy connection timeout (WinError 10060): {exc}",
            )
        return DoctorCheck("Gemini API", STATUS_FAIL, f"network error: {exc}")
    except Exception as exc:
        return DoctorCheck("Gemini API", STATUS_FAIL, str(exc))


def run_doctor(
    project_name: str,
    project_settings: dict,
    *,
    check_api: bool = False,
    api_timeout: int = 20,
) -> DoctorReport:
    api_key = project_settings.get("GOOGLE_API_KEY") or settings.GOOGLE_API_KEY
    model_name = project_settings.get("translation_model_name", settings.translation_model_name)
    model_name = normalize_model_configs(
        {"doctor": model_name},
        free_tier_mode=project_settings.get(
            "gemini_free_tier_mode", settings.gemini_free_tier_mode
        ),
    )["doctor"]
    checks = [
        _check_python(),
        _check_package("customtkinter"),
        _check_package("sqlalchemy", "SQLAlchemy"),
        _check_package("langgraph"),
        _check_package("pymorphy3"),
        _check_chromadb(),
        _check_api_key(project_settings),
        _check_model_profile(project_settings),
        _check_api_usage_budget(project_settings),
        _check_proxy_environment(),
        _check_dns_resolution(),
        check_directory("input_dir", project_settings.get("input_dir"), required=True),
        check_directory("output_dir", project_settings.get("output_dir"), required=True, create=True),
    ]
    local_failed = any(check.status == STATUS_FAIL for check in checks)
    if check_api and local_failed:
        checks.append(
            DoctorCheck(
                "Gemini API",
                STATUS_WARN,
                "skipped because required local checks failed",
            )
        )
    else:
        checks.append(_check_api_connectivity(check_api, api_key, model_name, api_timeout))
    exit_code = 1 if any(check.status == STATUS_FAIL for check in checks) else 0
    return DoctorReport(checks=checks, exit_code=exit_code)


def format_report(report: DoctorReport) -> str:
    lines = ["Novel Translator environment check:"]
    lines.extend(f"[{check.status}] {check.name}: {check.message}" for check in report.checks)
    return os.linesep.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check Novel Translator environment.")
    parser.add_argument("--project", default="Default", help="Название проекта для проверки.")
    parser.add_argument("--input-dir", help="Переопределить папку исходных глав.")
    parser.add_argument("--output-dir", help="Переопределить папку готового перевода.")
    parser.add_argument("--check-api", action="store_true", help="Зарезервировано для live API проверки.")
    parser.add_argument(
        "--api-timeout",
        type=int,
        default=20,
        help="Таймаут live API проверки Gemini в секундах.",
    )
    args = parser.parse_args(argv)

    project_settings = ProjectManager().get_project_settings(args.project)
    overrides = {
        "input_dir": args.input_dir,
        "output_dir": args.output_dir,
    }
    project_settings.update({key: value for key, value in overrides.items() if value is not None})
    report = run_doctor(
        args.project,
        project_settings,
        check_api=args.check_api,
        api_timeout=args.api_timeout,
    )
    print(format_report(report))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
