import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from google.api_core import exceptions as google_exceptions

from Perevod.api_usage import ApiUsageTracker
from Perevod import doctor
from Perevod.config import PROJECT_ROOT


def _workspace_temp_dir():
    path = Path(PROJECT_ROOT) / f"_test_doctor_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


@pytest.fixture(autouse=True)
def isolate_default_doctor_api_usage(monkeypatch, tmp_path):
    db_path = tmp_path / "api_usage.sqlite3"
    monkeypatch.setattr(
        doctor,
        "ApiUsageTracker",
        lambda: ApiUsageTracker(db_path=str(db_path)),
    )


def test_check_directory_reports_missing_required_path():
    temp_dir = _workspace_temp_dir()
    try:
        result = doctor.check_directory("input_dir", str(temp_dir / "missing"), required=True)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert result.status == doctor.STATUS_FAIL
    assert "not found" in result.message


def test_check_directory_creates_optional_output_path():
    temp_dir = _workspace_temp_dir()
    output_dir = temp_dir / "out"

    try:
        result = doctor.check_directory("output_dir", str(output_dir), required=True, create=True)

        assert result.status == doctor.STATUS_OK
        assert output_dir.is_dir()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_run_doctor_reports_missing_project_inputs(monkeypatch):
    monkeypatch.setattr(doctor, "settings", doctor.settings.model_copy(update={"GOOGLE_API_KEY": ""}))
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object())

    report = doctor.run_doctor(
        project_name="Book",
        project_settings={"input_dir": "", "output_dir": ""},
        check_api=False,
    )

    statuses = {item.name: item.status for item in report.checks}
    assert statuses["GOOGLE_API_KEY"] == doctor.STATUS_WARN
    assert statuses["Gemini model profile"] == doctor.STATUS_OK
    assert statuses["input_dir"] == doctor.STATUS_FAIL
    assert statuses["output_dir"] == doctor.STATUS_FAIL
    assert report.exit_code == 1


def test_check_api_key_warns_for_test_api_key():
    result = doctor._check_api_key({"GOOGLE_API_KEY": "test_api_key"})

    assert result.status == doctor.STATUS_WARN
    assert "test" in result.message.lower()


def test_run_doctor_skips_budget_tracker_for_test_api_key(monkeypatch):
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        doctor,
        "_check_dns_resolution",
        lambda: doctor.DoctorCheck("Gemini DNS", doctor.STATUS_OK, "ok"),
    )
    monkeypatch.setattr(
        doctor,
        "_check_proxy_environment",
        lambda: doctor.DoctorCheck("proxy env", doctor.STATUS_WARN, "none"),
    )
    monkeypatch.setattr(
        doctor,
        "ApiUsageTracker",
        MagicMock(side_effect=AssertionError("usage tracker should not be created")),
    )

    temp_dir = _workspace_temp_dir()
    input_dir = temp_dir / "in"
    output_dir = temp_dir / "out"
    try:
        input_dir.mkdir()
        report = doctor.run_doctor(
            project_name="Book",
            project_settings={
                "input_dir": str(input_dir),
                "output_dir": str(output_dir),
                "GOOGLE_API_KEY": "test_api_key",
            },
            check_api=False,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    checks = {item.name: item for item in report.checks}
    assert checks["Gemini daily budget"].status == doctor.STATUS_WARN
    assert "skipped" in checks["Gemini daily budget"].message


def test_check_model_profile_shows_normalized_free_tier_models():
    result = doctor._check_model_profile(
        {
            "translation_model_name": "gemini-2.5-pro",
            "analysis_model_name": "gemini-2.5-flash-lite",
            "embedding_model_name": "models/text-embedding-004",
            "gemini_free_tier_mode": True,
        }
    )

    assert result.status == doctor.STATUS_OK
    assert "translation=gemini-3-flash-preview" in result.message
    assert "analysis=gemini-3.1-flash-lite-preview" in result.message
    assert "embedding=gemini-embedding-2" in result.message


def test_run_doctor_does_not_fail_when_api_check_is_disabled(monkeypatch):
    monkeypatch.setattr(doctor, "settings", doctor.settings.model_copy(update={"GOOGLE_API_KEY": "key"}))
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(doctor, "_check_dns_resolution", lambda: doctor.DoctorCheck("Gemini DNS", doctor.STATUS_OK, "ok"))
    monkeypatch.setattr(doctor, "_check_proxy_environment", lambda: doctor.DoctorCheck("proxy env", doctor.STATUS_WARN, "none"))

    temp_dir = _workspace_temp_dir()
    input_dir = temp_dir / "in"
    output_dir = temp_dir / "out"
    try:
        input_dir.mkdir()
        report = doctor.run_doctor(
            project_name="Book",
            project_settings={
                "input_dir": str(input_dir),
                "output_dir": str(output_dir),
                "GOOGLE_API_KEY": "key",
            },
            check_api=False,
        )

        assert report.exit_code == 0
        assert output_dir.is_dir()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_run_doctor_check_api_calls_gemini_smoke_test(monkeypatch):
    monkeypatch.setattr(doctor, "settings", doctor.settings.model_copy(update={"GOOGLE_API_KEY": ""}))
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(doctor, "_check_dns_resolution", lambda: doctor.DoctorCheck("Gemini DNS", doctor.STATUS_OK, "ok"))
    monkeypatch.setattr(doctor, "_check_proxy_environment", lambda: doctor.DoctorCheck("proxy env", doctor.STATUS_WARN, "none"))
    smoke_test = MagicMock(return_value=doctor.DoctorCheck("Gemini API", doctor.STATUS_OK, "ok"))
    monkeypatch.setattr(doctor, "_check_api_connectivity", smoke_test)

    temp_dir = _workspace_temp_dir()
    input_dir = temp_dir / "in"
    output_dir = temp_dir / "out"
    try:
        input_dir.mkdir()
        report = doctor.run_doctor(
            project_name="Book",
            project_settings={
                "input_dir": str(input_dir),
                "output_dir": str(output_dir),
                "GOOGLE_API_KEY": "key",
                "translation_model_name": "gemini-test",
            },
            check_api=True,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    smoke_test.assert_called_once_with(True, "key", "gemini-test", 20)
    assert {item.name: item.status for item in report.checks}["Gemini API"] == doctor.STATUS_OK
    assert report.exit_code == 0


def test_run_doctor_skips_api_when_required_local_checks_fail(monkeypatch):
    monkeypatch.setattr(doctor, "settings", doctor.settings.model_copy(update={"GOOGLE_API_KEY": ""}))
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(doctor, "_check_dns_resolution", lambda: doctor.DoctorCheck("Gemini DNS", doctor.STATUS_OK, "ok"))
    monkeypatch.setattr(doctor, "_check_proxy_environment", lambda: doctor.DoctorCheck("proxy env", doctor.STATUS_WARN, "none"))
    smoke_test = MagicMock(return_value=doctor.DoctorCheck("Gemini API", doctor.STATUS_OK, "ok"))
    monkeypatch.setattr(doctor, "_check_api_connectivity", smoke_test)

    report = doctor.run_doctor(
        project_name="Book",
        project_settings={
            "input_dir": "",
            "output_dir": "",
            "GOOGLE_API_KEY": "key",
            "translation_model_name": "gemini-test",
        },
        check_api=True,
    )

    smoke_test.assert_not_called()
    statuses = {item.name: item.status for item in report.checks}
    assert statuses["Gemini API"] == doctor.STATUS_WARN


def test_run_doctor_passes_custom_api_timeout(monkeypatch):
    monkeypatch.setattr(doctor, "settings", doctor.settings.model_copy(update={"GOOGLE_API_KEY": ""}))
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(doctor, "_check_dns_resolution", lambda: doctor.DoctorCheck("Gemini DNS", doctor.STATUS_OK, "ok"))
    monkeypatch.setattr(doctor, "_check_proxy_environment", lambda: doctor.DoctorCheck("proxy env", doctor.STATUS_WARN, "none"))
    smoke_test = MagicMock(return_value=doctor.DoctorCheck("Gemini API", doctor.STATUS_OK, "ok"))
    monkeypatch.setattr(doctor, "_check_api_connectivity", smoke_test)

    temp_dir = _workspace_temp_dir()
    input_dir = temp_dir / "in"
    output_dir = temp_dir / "out"
    try:
        input_dir.mkdir()
        doctor.run_doctor(
            project_name="Book",
            project_settings={
                "input_dir": str(input_dir),
                "output_dir": str(output_dir),
                "GOOGLE_API_KEY": "key",
                "translation_model_name": "gemini-test",
            },
            check_api=True,
            api_timeout=60,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    smoke_test.assert_called_once_with(True, "key", "gemini-test", 60)


def test_run_doctor_normalizes_saved_pro_model_for_api_check(monkeypatch):
    monkeypatch.setattr(doctor, "settings", doctor.settings.model_copy(update={"GOOGLE_API_KEY": ""}))
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(doctor, "_check_dns_resolution", lambda: doctor.DoctorCheck("Gemini DNS", doctor.STATUS_OK, "ok"))
    monkeypatch.setattr(doctor, "_check_proxy_environment", lambda: doctor.DoctorCheck("proxy env", doctor.STATUS_WARN, "none"))
    smoke_test = MagicMock(return_value=doctor.DoctorCheck("Gemini API", doctor.STATUS_OK, "ok"))
    monkeypatch.setattr(doctor, "_check_api_connectivity", smoke_test)

    temp_dir = _workspace_temp_dir()
    input_dir = temp_dir / "in"
    output_dir = temp_dir / "out"
    try:
        input_dir.mkdir()
        doctor.run_doctor(
            project_name="Book",
            project_settings={
                "input_dir": str(input_dir),
                "output_dir": str(output_dir),
                "GOOGLE_API_KEY": "key",
                "translation_model_name": "gemini-2.5-pro",
            },
            check_api=True,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    smoke_test.assert_called_once_with(True, "key", "gemini-3-flash-preview", 20)


def test_check_proxy_environment_reports_missing_proxy(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)

    result = doctor._check_proxy_environment()

    assert result.status == doctor.STATUS_WARN
    assert "not set" in result.message


def test_check_proxy_environment_redacts_proxy_value(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@127.0.0.1:7890")

    result = doctor._check_proxy_environment()

    assert result.status == doctor.STATUS_OK
    assert "127.0.0.1:7890" in result.message
    assert "secret" not in result.message


def test_check_dns_resolution_reports_resolved_addresses(monkeypatch):
    monkeypatch.setattr(
        doctor.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (None, None, None, None, ("45.155.204.190", 443)),
            (None, None, None, None, ("45.155.204.190", 443)),
        ],
    )

    result = doctor._check_dns_resolution()

    assert result.status == doctor.STATUS_OK
    assert "45.155.204.190" in result.message


def test_check_api_connectivity_fails_without_api_key():
    result = doctor._check_api_connectivity(True, "", "gemini-test", 20)

    assert result.status == doctor.STATUS_FAIL
    assert "GOOGLE_API_KEY" in result.message


def test_check_api_connectivity_fails_fast_for_test_api_key(monkeypatch):
    provider_factory = MagicMock()
    monkeypatch.setattr(doctor, "LLMProvider", provider_factory)

    result = doctor._check_api_connectivity(True, "test_api_key", "gemini-test", 20)

    assert result.status == doctor.STATUS_FAIL
    assert "test" in result.message.lower()
    provider_factory.assert_not_called()


def test_check_api_connectivity_uses_llm_provider(monkeypatch):
    model = MagicMock()
    model.generate_content.return_value.text = "OK"
    provider = MagicMock()
    provider.get_model.return_value = model
    provider_factory = MagicMock(return_value=provider)
    monkeypatch.setattr(doctor, "LLMProvider", provider_factory)

    result = doctor._check_api_connectivity(True, "key", "gemini-test", 30)

    assert result.status == doctor.STATUS_OK
    provider_factory.assert_called_once_with({"doctor": "gemini-test"}, api_key="key")
    provider.get_model.assert_called_once_with("doctor")
    model.generate_content.assert_called_once()


def test_check_api_connectivity_reports_failure(monkeypatch):
    provider = MagicMock()
    provider.get_model.side_effect = RuntimeError("network down")
    monkeypatch.setattr(doctor, "LLMProvider", MagicMock(return_value=provider))

    result = doctor._check_api_connectivity(True, "key", "gemini-test", 20)

    assert result.status == doctor.STATUS_FAIL
    assert "network down" in result.message


def test_check_api_connectivity_reports_timeout(monkeypatch):
    provider = MagicMock()
    provider.get_model.side_effect = TimeoutError("timed out")
    monkeypatch.setattr(doctor, "LLMProvider", MagicMock(return_value=provider))

    result = doctor._check_api_connectivity(True, "key", "gemini-test", 20)

    assert result.status == doctor.STATUS_FAIL
    assert "timed out after 20s" in result.message


def test_check_api_connectivity_reports_windows_connection_timeout(monkeypatch):
    provider = MagicMock()
    provider.get_model.side_effect = OSError(10060, "connection timed out")
    monkeypatch.setattr(doctor, "LLMProvider", MagicMock(return_value=provider))

    result = doctor._check_api_connectivity(True, "key", "gemini-test", 20)

    assert result.status == doctor.STATUS_FAIL
    assert "network/VPN/proxy" in result.message
    assert "10060" in result.message


def test_check_api_connectivity_reports_permission_issue(monkeypatch):
    provider = MagicMock()
    provider.get_model.side_effect = google_exceptions.PermissionDenied("denied")
    monkeypatch.setattr(doctor, "LLMProvider", MagicMock(return_value=provider))

    result = doctor._check_api_connectivity(True, "key", "gemini-test", 20)

    assert result.status == doctor.STATUS_FAIL
    assert "permission/auth" in result.message


def test_check_api_connectivity_reports_quota_issue(monkeypatch):
    provider = MagicMock()
    provider.get_model.side_effect = google_exceptions.ResourceExhausted("quota")
    monkeypatch.setattr(doctor, "LLMProvider", MagicMock(return_value=provider))

    result = doctor._check_api_connectivity(True, "key", "gemini-test", 20)

    assert result.status == doctor.STATUS_FAIL
    assert "quota/rate limit" in result.message


def test_check_api_usage_budget_reports_exhausted_daily_limit(tmp_path):
    db_path = tmp_path / "doctor_api_usage.sqlite3"
    try:
        tracker = ApiUsageTracker(
            db_path=str(db_path),
            daily_limits={"gemini-3-flash-preview": 1},
        )
        tracker.reserve_call("gemini-3-flash-preview", "generateContent")

        result = doctor._check_api_usage_budget(
            {"translation_model_name": "gemini-3-flash-preview"},
            usage_tracker=tracker,
        )

        assert result.status == doctor.STATUS_FAIL
        assert "gemini-3-flash-preview: 1/1" in result.message
    finally:
        if db_path.exists():
            db_path.unlink()


def test_format_report_is_human_readable():
    report = doctor.DoctorReport(
        checks=[
            doctor.DoctorCheck("python", doctor.STATUS_OK, "3.12"),
            doctor.DoctorCheck("api", doctor.STATUS_WARN, "skipped"),
        ],
        exit_code=0,
    )

    text = doctor.format_report(report)

    assert "[OK] python: 3.12" in text
    assert "[WARN] api: skipped" in text
