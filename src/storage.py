import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


DEFAULT_DB_PATH = "output/garmingo.sqlite3"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_sqlite_path(profile_data: Optional[dict] = None) -> Path:
    profile_data = profile_data or {}
    raw_path = (
        profile_data.get("sqlite_path")
        or os.environ.get("GARMINGO_DB_PATH")
        or DEFAULT_DB_PATH
    )
    return Path(raw_path).expanduser()


def json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def json_loads(value: str) -> Any:
    return json.loads(value)


def endpoint_cache_key(endpoint: str, args: list[Any], kwargs: dict[str, Any]) -> tuple[str, str, str]:
    return endpoint, json_dumps(args), json_dumps(kwargs)


def activity_date(activity: dict[str, Any], fallback_date: str) -> str:
    for key in ("calendarDate", "startTimeLocal", "startTimeGMT", "beginTimestamp", "date"):
        value = activity.get(key)
        if not value:
            continue
        value = str(value)
        return value.split("T", 1)[0].split(" ", 1)[0]
    return fallback_date


def activity_id(activity: dict[str, Any]) -> str:
    for key in ("activityId", "activity_id", "id"):
        value = activity.get(key)
        if value is not None:
            return str(value)
    return json_dumps(activity)


class GarminHistoryStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_metrics (
                    profile TEXT NOT NULL,
                    metric_date TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    output_row_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (profile, metric_date)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS activity_ranges (
                    profile TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    activities_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (profile, start_date, end_date)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS activities (
                    profile TEXT NOT NULL,
                    activity_id TEXT NOT NULL,
                    activity_date TEXT NOT NULL,
                    activity_type TEXT,
                    activity_name TEXT,
                    activity_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (profile, activity_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS endpoint_cache (
                    profile TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    args_json TEXT NOT NULL,
                    kwargs_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (profile, endpoint, args_json, kwargs_json)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_metrics_date "
                "ON daily_metrics(profile, metric_date)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_activities_date "
                "ON activities(profile, activity_date)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_endpoint_cache_endpoint "
                "ON endpoint_cache(profile, endpoint)"
            )

    def save_daily_metric(
        self,
        profile: str,
        metric_date: str,
        metrics: dict[str, Any],
        output_row: dict[str, Any],
    ) -> str:
        fetched_at = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_metrics (
                    profile, metric_date, metrics_json, output_row_json, fetched_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(profile, metric_date) DO UPDATE SET
                    metrics_json = excluded.metrics_json,
                    output_row_json = excluded.output_row_json,
                    fetched_at = excluded.fetched_at
                """,
                (profile, metric_date, json_dumps(metrics), json_dumps(output_row), fetched_at),
            )
        return fetched_at

    def get_daily_metric(self, profile: str, metric_date: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT metrics_json, output_row_json, fetched_at
                FROM daily_metrics
                WHERE profile = ? AND metric_date = ?
                """,
                (profile, metric_date),
            ).fetchone()
        if row is None:
            return None
        return {
            "profile": profile,
            "date": metric_date,
            "metrics": json_loads(row["metrics_json"]),
            "output_row": json_loads(row["output_row_json"]),
            "source": "history",
            "fetched_at": row["fetched_at"],
        }

    def list_daily_metrics(self, profile: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT metric_date, metrics_json, output_row_json, fetched_at
                FROM daily_metrics
                WHERE profile = ? AND metric_date BETWEEN ? AND ?
                ORDER BY metric_date
                """,
                (profile, start_date, end_date),
            ).fetchall()
        return [
            {
                "profile": profile,
                "date": row["metric_date"],
                "metrics": json_loads(row["metrics_json"]),
                "output_row": json_loads(row["output_row_json"]),
                "source": "history",
                "fetched_at": row["fetched_at"],
            }
            for row in rows
        ]

    def save_activity_range(
        self,
        profile: str,
        start_date: str,
        end_date: str,
        activities: list[dict[str, Any]],
    ) -> str:
        fetched_at = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO activity_ranges (
                    profile, start_date, end_date, activities_json, fetched_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(profile, start_date, end_date) DO UPDATE SET
                    activities_json = excluded.activities_json,
                    fetched_at = excluded.fetched_at
                """,
                (profile, start_date, end_date, json_dumps(activities), fetched_at),
            )
            for activity in activities:
                activity_type = activity.get("activityType") or {}
                conn.execute(
                    """
                    INSERT INTO activities (
                        profile, activity_id, activity_date, activity_type,
                        activity_name, activity_json, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(profile, activity_id) DO UPDATE SET
                        activity_date = excluded.activity_date,
                        activity_type = excluded.activity_type,
                        activity_name = excluded.activity_name,
                        activity_json = excluded.activity_json,
                        fetched_at = excluded.fetched_at
                    """,
                    (
                        profile,
                        activity_id(activity),
                        activity_date(activity, start_date),
                        activity_type.get("typeKey"),
                        activity.get("activityName"),
                        json_dumps(activity),
                        fetched_at,
                    ),
                )
        return fetched_at

    def get_activity_range(
        self,
        profile: str,
        start_date: str,
        end_date: str,
    ) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT activities_json, fetched_at
                FROM activity_ranges
                WHERE profile = ? AND start_date = ? AND end_date = ?
                """,
                (profile, start_date, end_date),
            ).fetchone()
        if row is None:
            return None
        activities = json_loads(row["activities_json"])
        return {
            "profile": profile,
            "start_date": start_date,
            "end_date": end_date,
            "count": len(activities or []),
            "activities": activities or [],
            "source": "history",
            "fetched_at": row["fetched_at"],
        }

    def save_endpoint_response(
        self,
        profile: str,
        endpoint: str,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Any,
    ) -> str:
        fetched_at = utc_now_iso()
        _, args_json, kwargs_json = endpoint_cache_key(endpoint, args, kwargs)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO endpoint_cache (
                    profile, endpoint, args_json, kwargs_json, response_json, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile, endpoint, args_json, kwargs_json) DO UPDATE SET
                    response_json = excluded.response_json,
                    fetched_at = excluded.fetched_at
                """,
                (profile, endpoint, args_json, kwargs_json, json_dumps(response), fetched_at),
            )
        return fetched_at

    def get_endpoint_response(
        self,
        profile: str,
        endpoint: str,
        args: list[Any],
        kwargs: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        _, args_json, kwargs_json = endpoint_cache_key(endpoint, args, kwargs)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT response_json, fetched_at
                FROM endpoint_cache
                WHERE profile = ? AND endpoint = ? AND args_json = ? AND kwargs_json = ?
                """,
                (profile, endpoint, args_json, kwargs_json),
            ).fetchone()
        if row is None:
            return None
        return {
            "profile": profile,
            "endpoint": endpoint,
            "args": args,
            "kwargs": kwargs,
            "response": json_loads(row["response_json"]),
            "source": "history",
            "fetched_at": row["fetched_at"],
        }

    def status(self, profile: str) -> dict[str, Any]:
        with self._connect() as conn:
            daily = conn.execute(
                """
                SELECT COUNT(*) AS count, MIN(metric_date) AS min_date, MAX(metric_date) AS max_date
                FROM daily_metrics
                WHERE profile = ?
                """,
                (profile,),
            ).fetchone()
            activities = conn.execute(
                """
                SELECT COUNT(*) AS count, MIN(activity_date) AS min_date, MAX(activity_date) AS max_date
                FROM activities
                WHERE profile = ?
                """,
                (profile,),
            ).fetchone()
            endpoint_cache = conn.execute(
                """
                SELECT COUNT(*) AS count, COUNT(DISTINCT endpoint) AS endpoint_count
                FROM endpoint_cache
                WHERE profile = ?
                """,
                (profile,),
            ).fetchone()
        return {
            "db_path": str(self.db_path),
            "profile": profile,
            "daily_metrics": dict(daily),
            "activities": dict(activities),
            "endpoint_cache": dict(endpoint_cache),
        }
