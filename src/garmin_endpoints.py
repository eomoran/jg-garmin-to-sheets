import inspect
from typing import Any, Optional

import garminconnect


DOC_LINKS = {
    "python_garminconnect": "https://github.com/cyberjunky/python-garminconnect",
    "garmin_connect_developer_program": "https://developer.garmin.com/gc-developer-program/",
    "garmin_activity_api": "https://developer.garmin.com/gc-developer-program/activity-api/",
    "garmin_health_api": "https://developer.garmin.com/gc-developer-program/health-api/",
    "garmin_training_api": "https://developer.garmin.com/gc-developer-program/training-api/",
}


def endpoint_category(name: str) -> str:
    if name.startswith("get_activity") or name in {
        "get_activities",
        "get_activities_by_date",
        "get_activities_fordate",
        "get_last_activity",
        "get_progress_summary_between_dates",
    }:
        return "activities"
    if any(token in name for token in ("training", "vo2", "race", "endurance", "hill", "fitnessage")):
        return "training"
    if any(
        token in name
        for token in (
            "sleep",
            "hrv",
            "heart",
            "stress",
            "body_battery",
            "spo2",
            "respiration",
            "rhr",
            "max_metrics",
        )
    ):
        return "health"
    if any(token in name for token in ("weight", "weigh", "body_composition", "blood_pressure")):
        return "body"
    if any(token in name for token in ("steps", "floors", "intensity", "hydration", "summary", "stats")):
        return "daily"
    if any(token in name for token in ("device", "unit_system")):
        return "device"
    if any(token in name for token in ("gear", "workout")):
        return "gear_workouts"
    if any(token in name for token in ("badge", "challenge", "goal", "record")):
        return "goals"
    if any(token in name for token in ("menstrual", "pregnancy")):
        return "women_health"
    if "user" in name or "profile" in name or name == "get_full_name":
        return "profile"
    return "other"


def _parameter_schema(parameter: inspect.Parameter) -> dict[str, Any]:
    return {
        "name": parameter.name,
        "required": parameter.default is inspect._empty,
        "default": None if parameter.default is inspect._empty else repr(parameter.default),
        "kind": str(parameter.kind),
    }


def list_read_endpoints(
    query: Optional[str] = None,
    category: Optional[str] = None,
) -> list[dict[str, Any]]:
    query_value = query.lower().strip() if query else None
    category_value = category.lower().strip() if category else None
    endpoints = []

    for name, value in inspect.getmembers(garminconnect.Garmin, predicate=inspect.isfunction):
        if not name.startswith("get_"):
            continue

        endpoint = describe_endpoint(name, value)
        haystack = " ".join(
            [
                endpoint["name"],
                endpoint["category"],
                endpoint.get("doc") or "",
                " ".join(param["name"] for param in endpoint["parameters"]),
            ]
        ).lower()

        if query_value and query_value not in haystack:
            continue
        if category_value and endpoint["category"] != category_value:
            continue

        endpoints.append(endpoint)

    return sorted(endpoints, key=lambda item: item["name"])


def describe_endpoint(name: str, value: Any = None) -> dict[str, Any]:
    if value is None:
        value = getattr(garminconnect.Garmin, name, None)
    if value is None or not callable(value) or not name.startswith("get_"):
        raise ValueError(f"Unsupported Garmin read endpoint: {name}")

    signature = inspect.signature(value)
    parameters = [
        _parameter_schema(parameter)
        for parameter in signature.parameters.values()
        if parameter.name != "self"
    ]
    return {
        "name": name,
        "category": endpoint_category(name),
        "signature": str(signature).replace("(self, ", "(").replace("(self)", "()"),
        "parameters": parameters,
        "doc": (inspect.getdoc(value) or "").split("\n")[0],
    }


def validate_read_endpoint(name: str) -> None:
    value = getattr(garminconnect.Garmin, name, None)
    if value is None or not callable(value) or not name.startswith("get_"):
        raise ValueError(f"Unsupported Garmin read endpoint: {name}")


def validate_endpoint_arguments(name: str, args: list[Any], kwargs: dict[str, Any]) -> None:
    value = getattr(garminconnect.Garmin, name)
    signature = inspect.signature(value)
    parameters = [p for p in signature.parameters.values() if p.name != "self"]
    callable_signature = signature.replace(parameters=parameters)
    callable_signature.bind(*args, **kwargs)
