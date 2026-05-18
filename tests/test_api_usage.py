import sqlite3
from contextlib import closing
from datetime import date

import pytest

from Perevod.api_usage import (
    ApiUsageLimitExceeded,
    ApiUsageTracker,
    is_placeholder_api_key,
    should_track_api_usage,
)


def _usage_db_path(tmp_path, name: str):
    return tmp_path / f"{name}.sqlite3"


def test_api_usage_tracker_blocks_calls_after_daily_limit(tmp_path):
    db_path = _usage_db_path(tmp_path, "api_usage_limit")
    try:
        tracker = ApiUsageTracker(
            db_path=str(db_path),
            daily_limits={"gemini-test": 2},
            date_provider=lambda: date(2026, 5, 6),
        )

        tracker.reserve_call("gemini-test", "generateContent")
        tracker.record_call("gemini-test", "generateContent")
        tracker.reserve_call("gemini-test", "generateContent")
        tracker.record_call("gemini-test", "generateContent")

        with pytest.raises(ApiUsageLimitExceeded, match="gemini-test"):
            tracker.reserve_call("gemini-test", "generateContent")
    finally:
        if db_path.exists():
            db_path.unlink()


def test_api_usage_tracker_ignores_unknown_models(tmp_path):
    db_path = _usage_db_path(tmp_path, "api_usage_unknown")
    try:
        tracker = ApiUsageTracker(
            db_path=str(db_path),
            daily_limits={"gemini-limited": 1},
            date_provider=lambda: date(2026, 5, 6),
        )

        tracker.reserve_call("custom-model", "generateContent")
        tracker.record_call("custom-model", "generateContent")
        tracker.reserve_call("custom-model", "generateContent")
        tracker.record_call("custom-model", "generateContent")
    finally:
        if db_path.exists():
            db_path.unlink()


def test_api_usage_tracker_resets_by_date(tmp_path):
    db_path = _usage_db_path(tmp_path, "api_usage_date")
    try:
        current_date = [date(2026, 5, 6)]
        tracker = ApiUsageTracker(
            db_path=str(db_path),
            daily_limits={"gemini-test": 1},
            date_provider=lambda: current_date[0],
        )

        tracker.reserve_call("gemini-test", "generateContent")
        tracker.record_call("gemini-test", "generateContent")
        current_date[0] = date(2026, 5, 7)
        tracker.reserve_call("gemini-test", "generateContent")
        tracker.record_call("gemini-test", "generateContent")
    finally:
        if db_path.exists():
            db_path.unlink()


def test_api_usage_tracker_reports_daily_usage(tmp_path):
    db_path = _usage_db_path(tmp_path, "api_usage_report")
    try:
        tracker = ApiUsageTracker(
            db_path=str(db_path),
            daily_limits={"gemini-test": 3},
            date_provider=lambda: date(2026, 5, 6),
        )

        tracker.reserve_call("gemini-test", "generateContent")
        tracker.record_call("gemini-test", "generateContent")

        assert tracker.get_daily_usage("gemini-test") == (1, 3)
        assert tracker.get_daily_usage("unknown-model") == (0, None)
    finally:
        if db_path.exists():
            db_path.unlink()


def test_api_usage_tracker_migrates_legacy_usage_table(tmp_path):
    db_path = _usage_db_path(tmp_path, "api_usage_legacy_schema")
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE api_usage (
                    usage_date TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    calls INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (usage_date, model_name, operation)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO api_usage (usage_date, model_name, operation, calls)
                VALUES (?, ?, ?, ?)
                """,
                ("2026-05-06", "gemini-test", "generateContent", 1),
            )
            conn.commit()

        tracker = ApiUsageTracker(
            db_path=str(db_path),
            daily_limits={"gemini-test": 3},
            date_provider=lambda: date(2026, 5, 6),
        )

        reservation_id = tracker.reserve_call("gemini-test", "generateContent")

        assert isinstance(reservation_id, str)
        assert tracker.get_daily_usage("gemini-test") == (1, 3)
        assert tracker.get_daily_usage("gemini-test", include_reserved=True) == (2, 3)
    finally:
        if db_path.exists():
            db_path.unlink()


def test_api_usage_tracker_reserves_in_flight_call_without_counting_success(tmp_path):
    db_path = _usage_db_path(tmp_path, "api_usage_reservation")
    try:
        tracker = ApiUsageTracker(
            db_path=str(db_path),
            daily_limits={"gemini-test": 1},
            date_provider=lambda: date(2026, 5, 6),
        )

        tracker.reserve_call("gemini-test", "generateContent")

        assert tracker.get_daily_usage("gemini-test") == (0, 1)
        with pytest.raises(ApiUsageLimitExceeded, match="gemini-test"):
            tracker.reserve_call("gemini-test", "generateContent")

        tracker.record_call("gemini-test", "generateContent")

        assert tracker.get_daily_usage("gemini-test") == (1, 1)
    finally:
        if db_path.exists():
            db_path.unlink()


def test_api_usage_tracker_expires_stale_reservations(tmp_path):
    db_path = _usage_db_path(tmp_path, "api_usage_stale_reservation")
    try:
        current_time = [100.0]
        tracker = ApiUsageTracker(
            db_path=str(db_path),
            daily_limits={"gemini-test": 1},
            date_provider=lambda: date(2026, 5, 6),
            time_provider=lambda: current_time[0],
            reservation_ttl_seconds=60,
        )

        tracker.reserve_call("gemini-test", "generateContent")

        assert tracker.get_daily_usage("gemini-test", include_reserved=True) == (1, 1)
        current_time[0] = 161.0
        tracker.check_call_available("gemini-test", "generateContent")

        assert tracker.get_daily_usage("gemini-test", include_reserved=True) == (0, 1)
        tracker.reserve_call("gemini-test", "generateContent")
    finally:
        if db_path.exists():
            db_path.unlink()


def test_api_usage_tracker_records_and_releases_specific_reservations(tmp_path):
    db_path = _usage_db_path(tmp_path, "api_usage_reservation_token")
    try:
        current_time = [100.0]
        tracker = ApiUsageTracker(
            db_path=str(db_path),
            daily_limits={"gemini-test": 3},
            date_provider=lambda: date(2026, 5, 6),
            time_provider=lambda: current_time[0],
            reservation_ttl_seconds=60,
        )

        first_reservation = tracker.reserve_call("gemini-test", "generateContent")
        second_reservation = tracker.reserve_call("gemini-test", "generateContent")

        tracker.record_call(
            "gemini-test",
            "generateContent",
            reservation_id=first_reservation,
        )

        assert tracker.get_daily_usage("gemini-test") == (1, 3)
        assert tracker.get_daily_usage("gemini-test", include_reserved=True) == (2, 3)

        tracker.release_call(
            "gemini-test",
            "generateContent",
            reservation_id=second_reservation,
        )

        assert tracker.get_daily_usage("gemini-test", include_reserved=True) == (1, 3)
    finally:
        if db_path.exists():
            db_path.unlink()


def test_api_usage_tracker_releases_failed_in_flight_call(tmp_path):
    db_path = _usage_db_path(tmp_path, "api_usage_release")
    try:
        tracker = ApiUsageTracker(
            db_path=str(db_path),
            daily_limits={"gemini-test": 1},
            date_provider=lambda: date(2026, 5, 6),
        )

        tracker.reserve_call("gemini-test", "generateContent")
        tracker.release_call("gemini-test", "generateContent")
        tracker.reserve_call("gemini-test", "generateContent")

        assert tracker.get_daily_usage("gemini-test") == (0, 1)
    finally:
        if db_path.exists():
            db_path.unlink()


def test_api_usage_tracker_check_does_not_increment_usage(tmp_path):
    db_path = _usage_db_path(tmp_path, "api_usage_check")
    try:
        tracker = ApiUsageTracker(
            db_path=str(db_path),
            daily_limits={"gemini-test": 1},
            date_provider=lambda: date(2026, 5, 6),
        )

        tracker.check_call_available("gemini-test", "generateContent")

        assert tracker.get_daily_usage("gemini-test") == (0, 1)
        tracker.record_call("gemini-test", "generateContent")
        with pytest.raises(ApiUsageLimitExceeded, match="gemini-test"):
            tracker.check_call_available("gemini-test", "generateContent")
    finally:
        if db_path.exists():
            db_path.unlink()


def test_should_track_api_usage_ignores_fake_test_keys():
    assert should_track_api_usage("AIza-real-looking-key")
    assert not should_track_api_usage("fake")
    assert not should_track_api_usage("test_api_key")
    assert not should_track_api_usage("")


def test_is_placeholder_api_key_detects_fake_test_keys():
    assert is_placeholder_api_key("fake")
    assert is_placeholder_api_key("test_api_key")
    assert not is_placeholder_api_key("AIza-real-looking-key")
    assert not is_placeholder_api_key("")
