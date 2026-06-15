import asyncio
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

from src.config import HEADERS, HEADER_TO_ATTRIBUTE_MAP
from src.exceptions import MFARequiredException
from src.garmin_endpoints import DOC_LINKS, list_read_endpoints, validate_endpoint_arguments, validate_read_endpoint
from src.garmin_client import GarminClient
from src.profiles import load_env_files, load_user_profiles, resolve_profile_password
from src.storage import GarminHistoryStore, resolve_sqlite_path

MAX_METRIC_RANGE_DAYS = 31
MAX_ACTIVITY_RANGE_DAYS = 90

mcp = FastMCP(
    "GarminGo",
    instructions=(
        "Read-only access to Garmin Connect health metrics and activities. "
        "Tools fetch data through the local GarminGo profile configuration and "
        "never write back to Garmin, CSV files, or Google Sheets."
    ),
)


def _load_env() -> None:
    load_env_files()


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}'. Expected YYYY-MM-DD.") from exc


def _validate_date_range(start_date: str, end_date: str, max_days: int) -> tuple[date, date]:
    parsed_start = _parse_date(start_date)
    parsed_end = _parse_date(end_date)
    if parsed_end < parsed_start:
        raise ValueError("end_date cannot be before start_date.")

    day_count = (parsed_end - parsed_start).days + 1
    if day_count > max_days:
        raise ValueError(f"Date range is {day_count} days; maximum is {max_days} days.")

    return parsed_start, parsed_end


def _metric_to_dict(metric) -> Dict[str, Any]:
    data = asdict(metric)
    if isinstance(data.get("date"), date):
        data["date"] = data["date"].isoformat()
    return data


def _metric_to_output_row(metric) -> Dict[str, Any]:
    row = {}
    for header in HEADERS:
        attribute_name = HEADER_TO_ATTRIBUTE_MAP.get(header)
        value = getattr(metric, attribute_name, None) if attribute_name else None
        if isinstance(value, date):
            value = value.isoformat()
        row[header] = value
    return row


def _profile_data(profile: str) -> dict:
    _load_env()
    profiles = load_user_profiles()
    profile_data = profiles.get(profile)
    if not profile_data:
        raise ValueError(f"Profile '{profile}' not found.")
    return profile_data


def _history_store(profile_data: dict) -> GarminHistoryStore:
    return GarminHistoryStore(resolve_sqlite_path(profile_data))


async def _authenticated_client(profile: str) -> GarminClient:
    profile_data = _profile_data(profile)

    email = profile_data.get("email")
    password = resolve_profile_password(profile, profile_data)
    if not email or not password:
        raise ValueError(
            f"Profile '{profile}' is missing GARMIN_EMAIL and/or password configuration."
        )

    client = GarminClient(email, password)
    try:
        await client.authenticate()
    except MFARequiredException as exc:
        raise RuntimeError(
            "Garmin MFA is required. Run the normal GarminGo CLI interactively once "
            "to refresh saved tokens before using the MCP server."
        ) from exc

    return client


async def _daily_metrics_payload(
    target_date: date,
    profile: str,
    refresh: bool,
    save_to_history: bool,
) -> Dict[str, Any]:
    profile_data = _profile_data(profile)
    store = _history_store(profile_data)
    target_date_iso = target_date.isoformat()

    if not refresh:
        cached = store.get_daily_metric(profile, target_date_iso)
        if cached is not None:
            return cached

    client = await _authenticated_client(profile)
    metric = await client.get_metrics(target_date)
    payload = {
        "profile": profile,
        "date": target_date_iso,
        "metrics": _metric_to_dict(metric),
        "output_row": _metric_to_output_row(metric),
        "source": "garmin",
    }
    if save_to_history:
        payload["fetched_at"] = store.save_daily_metric(
            profile,
            target_date_iso,
            payload["metrics"],
            payload["output_row"],
        )
    return payload


@mcp.resource("garmin://metrics-schema")
def metrics_schema() -> Dict[str, Any]:
    """Return the GarminGo output headers and their metric attribute names."""
    return {
        "headers": HEADERS,
        "header_to_attribute": HEADER_TO_ATTRIBUTE_MAP,
        "max_metric_range_days": MAX_METRIC_RANGE_DAYS,
        "max_activity_range_days": MAX_ACTIVITY_RANGE_DAYS,
    }


@mcp.resource("garmin://garmin-endpoints")
def garmin_endpoints_resource() -> Dict[str, Any]:
    """Return live read-only Garmin endpoint metadata discovered from garminconnect."""
    endpoints = list_read_endpoints()
    return {
        "docs": DOC_LINKS,
        "source": "garminconnect.Garmin get_* methods discovered at runtime",
        "read_only_policy": "Only get_* methods are exposed through call_garmin_endpoint.",
        "count": len(endpoints),
        "endpoints": endpoints,
    }


@mcp.tool()
def list_profiles() -> Dict[str, Any]:
    """List configured GarminGo profiles without exposing secrets."""
    _load_env()
    profiles = load_user_profiles()
    profile_items = []
    for name, data in sorted(profiles.items()):
        profile_items.append(
            {
                "profile": name,
                "email": data.get("email"),
                "has_plaintext_password": bool(data.get("password")),
                "uses_keyring_password": bool(data.get("password_keyring")),
                "csv_path": data.get("csv_path"),
                "sqlite_path": str(resolve_sqlite_path(data)),
                "sheet_name": data.get("sheet_name"),
                "has_sheet_id": bool(data.get("sheet_id")),
            }
        )

    return {"count": len(profile_items), "profiles": profile_items}


@mcp.tool()
async def get_daily_metrics(
    target_date: str,
    profile: str = "USER1",
    refresh: bool = False,
) -> Dict[str, Any]:
    """Fetch one day of Garmin health metrics, using SQLite history unless refresh is true."""
    parsed_date = _parse_date(target_date)
    return await _daily_metrics_payload(
        parsed_date,
        profile=profile,
        refresh=refresh,
        save_to_history=True,
    )


@mcp.tool()
async def get_metrics_range(
    start_date: str,
    end_date: str,
    profile: str = "USER1",
    refresh: bool = False,
) -> Dict[str, Any]:
    """Fetch Garmin health metrics for a date range up to 31 days, using history first."""
    parsed_start, parsed_end = _validate_date_range(
        start_date,
        end_date,
        MAX_METRIC_RANGE_DAYS,
    )

    metrics = []
    current_date = parsed_start
    while current_date <= parsed_end:
        metrics.append(
            await _daily_metrics_payload(
                current_date,
                profile=profile,
                refresh=refresh,
                save_to_history=True,
            )
        )
        current_date += timedelta(days=1)

    return {
        "profile": profile,
        "start_date": parsed_start.isoformat(),
        "end_date": parsed_end.isoformat(),
        "days": len(metrics),
        "results": metrics,
    }


@mcp.tool()
async def get_activities(
    start_date: str,
    end_date: str,
    profile: str = "USER1",
    refresh: bool = False,
) -> Dict[str, Any]:
    """Fetch raw Garmin activity records for a date range up to 90 days, using history first."""
    parsed_start, parsed_end = _validate_date_range(
        start_date,
        end_date,
        MAX_ACTIVITY_RANGE_DAYS,
    )
    profile_data = _profile_data(profile)
    store = _history_store(profile_data)
    start_date_iso = parsed_start.isoformat()
    end_date_iso = parsed_end.isoformat()

    if not refresh:
        cached = store.get_activity_range(profile, start_date_iso, end_date_iso)
        if cached is not None:
            return cached

    client = await _authenticated_client(profile)
    activities = await asyncio.get_event_loop().run_in_executor(
        None,
        client.client.get_activities_by_date,
        start_date_iso,
        end_date_iso,
    )
    activities = activities or []
    fetched_at = store.save_activity_range(profile, start_date_iso, end_date_iso, activities)

    return {
        "profile": profile,
        "start_date": start_date_iso,
        "end_date": end_date_iso,
        "count": len(activities),
        "activities": activities,
        "source": "garmin",
        "fetched_at": fetched_at,
    }


@mcp.tool()
def get_history_status(profile: str = "USER1") -> Dict[str, Any]:
    """Return local SQLite history coverage for a configured profile."""
    profile_data = _profile_data(profile)
    return _history_store(profile_data).status(profile)


@mcp.tool()
async def sync_metrics_to_history(
    start_date: str,
    end_date: str,
    profile: str = "USER1",
    refresh: bool = False,
) -> Dict[str, Any]:
    """Populate SQLite history with daily metrics for a date range up to 31 days."""
    result = await get_metrics_range(
        start_date=start_date,
        end_date=end_date,
        profile=profile,
        refresh=refresh,
    )
    profile_data = _profile_data(profile)
    return {
        "profile": profile,
        "start_date": result["start_date"],
        "end_date": result["end_date"],
        "days": result["days"],
        "sources": {
            "history": sum(1 for item in result["results"] if item.get("source") == "history"),
            "garmin": sum(1 for item in result["results"] if item.get("source") == "garmin"),
        },
        "history": _history_store(profile_data).status(profile),
    }


@mcp.tool()
def list_garmin_endpoints(
    query: Optional[str] = None,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    """List read-only garminconnect get_* endpoints available to the generic endpoint bridge."""
    endpoints = list_read_endpoints(query=query, category=category)
    return {
        "count": len(endpoints),
        "query": query,
        "category": category,
        "docs": DOC_LINKS,
        "endpoints": endpoints,
    }


@mcp.tool()
async def call_garmin_endpoint(
    endpoint: str,
    args: Optional[list[Any]] = None,
    kwargs: Optional[dict[str, Any]] = None,
    profile: str = "USER1",
    refresh: bool = False,
) -> Dict[str, Any]:
    """Call a read-only garminconnect get_* endpoint, cached by exact arguments."""
    endpoint_args = args or []
    endpoint_kwargs = kwargs or {}
    if not isinstance(endpoint_args, list):
        raise ValueError("args must be a list.")
    if not isinstance(endpoint_kwargs, dict):
        raise ValueError("kwargs must be an object.")

    validate_read_endpoint(endpoint)
    validate_endpoint_arguments(endpoint, endpoint_args, endpoint_kwargs)

    profile_data = _profile_data(profile)
    store = _history_store(profile_data)
    if not refresh:
        cached = store.get_endpoint_response(profile, endpoint, endpoint_args, endpoint_kwargs)
        if cached is not None:
            return cached

    client = await _authenticated_client(profile)
    method = getattr(client.client, endpoint)
    response = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: method(*endpoint_args, **endpoint_kwargs),
    )
    fetched_at = store.save_endpoint_response(
        profile,
        endpoint,
        endpoint_args,
        endpoint_kwargs,
        response,
    )
    return {
        "profile": profile,
        "endpoint": endpoint,
        "args": endpoint_args,
        "kwargs": endpoint_kwargs,
        "response": response,
        "source": "garmin",
        "fetched_at": fetched_at,
    }


def main() -> None:
    """Run the GarminGo MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
