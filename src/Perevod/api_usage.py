import os
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import date

from Perevod.config import PROJECT_ROOT
from Perevod.model_registry import default_daily_limits

DEFAULT_USAGE_DB_PATH = os.path.join("_project_files", "api_usage.sqlite3")
DEFAULT_RESERVATION_TTL_SECONDS = 60 * 60

DEFAULT_DAILY_LIMITS = default_daily_limits()
TEST_API_KEY_PREFIXES = ("fake", "test")


class ApiUsageLimitExceeded(RuntimeError):
    """Raised before an API call when the configured daily budget is exhausted."""


def is_placeholder_api_key(api_key: str) -> bool:
    normalized = (api_key or "").strip().lower()
    return bool(normalized) and normalized.startswith(TEST_API_KEY_PREFIXES)


def should_track_api_usage(api_key: str) -> bool:
    normalized = (api_key or "").strip()
    return bool(normalized) and not is_placeholder_api_key(normalized)


class ApiUsageTracker:
    def __init__(
        self,
        db_path: str | None = None,
        daily_limits: dict[str, int] | None = None,
        date_provider=date.today,
        time_provider=time.time,
        reservation_ttl_seconds: int = DEFAULT_RESERVATION_TTL_SECONDS,
    ):
        self.db_path = db_path or os.path.join(PROJECT_ROOT, DEFAULT_USAGE_DB_PATH)
        self.daily_limits = daily_limits or DEFAULT_DAILY_LIMITS
        self.date_provider = date_provider
        self.time_provider = time_provider
        self.reservation_ttl_seconds = reservation_ttl_seconds
        self._last_cleanup_time = 0.0

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_table(self) -> None:
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_usage (
                    usage_date TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    calls INTEGER NOT NULL DEFAULT 0,
                    reserved INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (usage_date, model_name, operation)
                )
                """
            )
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(api_usage)").fetchall()
            }
            if "reserved" not in columns:
                conn.execute(
                    "ALTER TABLE api_usage ADD COLUMN reserved INTEGER NOT NULL DEFAULT 0"
                )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_usage_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    usage_date TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_api_usage_reservations_stale
                ON api_usage_reservations(created_at)
                """
            )
            conn.commit()

    def _sync_reserved_counts(self, conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE api_usage SET reserved = 0 WHERE reserved != 0")
        reservations = conn.execute(
            """
            SELECT usage_date, model_name, operation, COUNT(*)
            FROM api_usage_reservations
            GROUP BY usage_date, model_name, operation
            """
        ).fetchall()
        for usage_date, model_name, operation, reservation_count in reservations:
            conn.execute(
                """
                INSERT INTO api_usage
                    (usage_date, model_name, operation, calls, reserved)
                VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(usage_date, model_name, operation)
                DO UPDATE SET reserved = excluded.reserved
                """,
                (usage_date, model_name, operation, reservation_count),
            )

    def _cleanup_expired_reservations(self, conn: sqlite3.Connection) -> None:
        current_time = float(self.time_provider())
        if current_time - self._last_cleanup_time < 60.0:
            return
        self._last_cleanup_time = current_time

        expires_before = current_time - self.reservation_ttl_seconds
        conn.execute(
            "DELETE FROM api_usage_reservations WHERE created_at <= ?",
            (expires_before,),
        )
        self._sync_reserved_counts(conn)

    @staticmethod
    def _decrement_reserved(
        conn: sqlite3.Connection,
        usage_date: str,
        model_name: str,
        operation: str,
    ) -> None:
        conn.execute(
            """
            UPDATE api_usage
            SET reserved = CASE
                WHEN reserved > 0 THEN reserved - 1
                ELSE 0
            END
            WHERE usage_date = ?
              AND model_name = ?
              AND operation = ?
            """,
            (usage_date, model_name, operation),
        )

    def _consume_reservation(
        self,
        conn: sqlite3.Connection,
        reservation_id: str | None,
        usage_date: str,
        model_name: str,
        operation: str,
    ) -> bool:
        if reservation_id:
            row = conn.execute(
                """
                SELECT usage_date, reservation_id
                FROM api_usage_reservations
                WHERE reservation_id = ?
                  AND model_name = ?
                  AND operation = ?
                """,
                (reservation_id, model_name, operation),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT usage_date, reservation_id
                FROM api_usage_reservations
                WHERE usage_date = ?
                  AND model_name = ?
                  AND operation = ?
                ORDER BY created_at
                LIMIT 1
                """,
                (usage_date, model_name, operation),
            ).fetchone()
        if not row:
            return False

        reservation_date, consumed_reservation_id = row
        conn.execute(
            "DELETE FROM api_usage_reservations WHERE reservation_id = ?",
            (consumed_reservation_id,),
        )
        self._decrement_reserved(conn, reservation_date, model_name, operation)
        return True

    def reserve_call(self, model_name: str, operation: str) -> str | None:
        normalized_model = (model_name or "").strip()
        daily_limit = self.daily_limits.get(normalized_model)
        if not daily_limit:
            return None

        usage_date = self.date_provider().isoformat()
        reservation_id = uuid.uuid4().hex
        self._ensure_table()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._cleanup_expired_reservations(conn)
            used_calls = conn.execute(
                """
                SELECT COALESCE(SUM(calls + reserved), 0)
                FROM api_usage
                WHERE usage_date = ? AND model_name = ?
                """,
                (usage_date, normalized_model),
            ).fetchone()[0]

            if used_calls >= daily_limit:
                conn.rollback()
                raise ApiUsageLimitExceeded(
                    f"Daily Gemini API limit exhausted for {normalized_model}: "
                    f"{used_calls}/{daily_limit} calls used or reserved on {usage_date}."
                )

            conn.execute(
                """
                INSERT INTO api_usage
                    (usage_date, model_name, operation, calls, reserved)
                VALUES (?, ?, ?, 0, 1)
                ON CONFLICT(usage_date, model_name, operation)
                DO UPDATE SET reserved = reserved + 1
                """,
                (usage_date, normalized_model, operation),
            )
            conn.execute(
                """
                INSERT INTO api_usage_reservations
                    (reservation_id, usage_date, model_name, operation, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    reservation_id,
                    usage_date,
                    normalized_model,
                    operation,
                    float(self.time_provider()),
                ),
            )
            conn.commit()
        return reservation_id

    def check_call_available(self, model_name: str, operation: str) -> None:
        normalized_model = (model_name or "").strip()
        daily_limit = self.daily_limits.get(normalized_model)
        if not daily_limit:
            return

        usage_date = self.date_provider().isoformat()
        self._ensure_table()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._cleanup_expired_reservations(conn)
            used_calls = conn.execute(
                """
                SELECT COALESCE(SUM(calls + reserved), 0)
                FROM api_usage
                WHERE usage_date = ? AND model_name = ?
                """,
                (usage_date, normalized_model),
            ).fetchone()[0]

            if used_calls >= daily_limit:
                conn.commit()
                raise ApiUsageLimitExceeded(
                    f"Daily Gemini API limit exhausted for {normalized_model}: "
                    f"{used_calls}/{daily_limit} calls used or reserved on {usage_date}."
                )
            conn.commit()

    def record_call(
        self,
        model_name: str,
        operation: str,
        reservation_id: str | None = None,
    ) -> None:
        normalized_model = (model_name or "").strip()
        daily_limit = self.daily_limits.get(normalized_model)
        if not daily_limit:
            return

        usage_date = self.date_provider().isoformat()
        self._ensure_table()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._cleanup_expired_reservations(conn)
            reservation_consumed = self._consume_reservation(
                conn,
                reservation_id,
                usage_date,
                normalized_model,
                operation,
            )
            conn.execute(
                """
                INSERT INTO api_usage
                    (usage_date, model_name, operation, calls, reserved)
                VALUES (?, ?, ?, 1, 0)
                ON CONFLICT(usage_date, model_name, operation)
                DO UPDATE SET calls = calls + 1
                """,
                (usage_date, normalized_model, operation),
            )
            if reservation_id is None and not reservation_consumed:
                self._decrement_reserved(conn, usage_date, normalized_model, operation)
            conn.commit()

    def release_call(
        self,
        model_name: str,
        operation: str,
        reservation_id: str | None = None,
    ) -> None:
        normalized_model = (model_name or "").strip()
        daily_limit = self.daily_limits.get(normalized_model)
        if not daily_limit:
            return

        usage_date = self.date_provider().isoformat()
        self._ensure_table()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._cleanup_expired_reservations(conn)
            reservation_consumed = self._consume_reservation(
                conn,
                reservation_id,
                usage_date,
                normalized_model,
                operation,
            )
            if reservation_id is None and not reservation_consumed:
                self._decrement_reserved(conn, usage_date, normalized_model, operation)
            conn.commit()

    def get_daily_usage(
        self, model_name: str, *, include_reserved: bool = False
    ) -> tuple[int, int | None]:
        normalized_model = (model_name or "").strip()
        daily_limit = self.daily_limits.get(normalized_model)
        if not daily_limit:
            return 0, None

        usage_date = self.date_provider().isoformat()
        self._ensure_table()
        usage_expression = "calls + reserved" if include_reserved else "calls"
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._cleanup_expired_reservations(conn)
            used_calls = conn.execute(
                f"""
                SELECT COALESCE(SUM({usage_expression}), 0)
                FROM api_usage
                WHERE usage_date = ? AND model_name = ?
                """,
                (usage_date, normalized_model),
            ).fetchone()[0]
            conn.commit()
        return int(used_calls), daily_limit
