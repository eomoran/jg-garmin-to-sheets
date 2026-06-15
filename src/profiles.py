import logging
import os
import re
from typing import Optional

import keyring

logger = logging.getLogger(__name__)


def resolve_profile_password(profile_name: str, profile_data: dict) -> Optional[str]:
    """Return the Garmin password from plaintext config or a keyring reference."""
    if profile_data.get("password"):
        return profile_data["password"]

    keyring_ref = profile_data.get("password_keyring")
    if not keyring_ref:
        return None

    try:
        service, username = keyring_ref.split(":", 1)
    except ValueError:
        logger.error(
            f"Invalid keyring reference for profile '{profile_name}'. "
            "Expected format: service:username"
        )
        return None

    password = keyring.get_password(service, username)
    if not password:
        logger.error(
            f"No password found in keyring for profile '{profile_name}' "
            f"using service '{service}' and username '{username}'."
        )
    return password


def load_user_profiles():
    """Parse environment variables into Garmin user profiles."""
    profiles = {}
    profile_pattern = re.compile(
        r"^(USER\d+)_(GARMIN_EMAIL|GARMIN_PASSWORD|GARMIN_PASSWORD_KEYRING|"
        r"SHEET_ID|SHEET_NAME|SPREADSHEET_NAME|CSV_PATH|SQLITE_PATH)$"
    )

    for key, value in os.environ.items():
        match = profile_pattern.match(key)
        if match:
            profile_name, var_type = match.groups()
            if profile_name not in profiles:
                profiles[profile_name] = {}

            key_map = {
                "GARMIN_EMAIL": "email",
                "GARMIN_PASSWORD": "password",
                "GARMIN_PASSWORD_KEYRING": "password_keyring",
                "SHEET_ID": "sheet_id",
                "SHEET_NAME": "sheet_name",
                "SPREADSHEET_NAME": "spreadsheet_name",
                "CSV_PATH": "csv_path",
                "SQLITE_PATH": "sqlite_path",
            }
            profiles[profile_name][key_map[var_type]] = value
    return profiles
