#!/usr/bin/env python3
"""Mint a Google Ads API refresh token via the installed-app OAuth flow.

Opens a browser once, captures the grant on http://127.0.0.1:8080, and writes
GOOGLE_ADS_REFRESH_TOKEN back into .env alongside this script.

Usage:
    python generate_refresh_token.py
"""

import os
import re
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPE = "https://www.googleapis.com/auth/adwords"
ENV_FILE = Path(__file__).parent.parent / ".env"


def read_env() -> dict:
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def write_refresh_token(refresh_token: str) -> None:
    text = ENV_FILE.read_text()
    text = re.sub(
        r"^GOOGLE_ADS_REFRESH_TOKEN=.*$",
        f"GOOGLE_ADS_REFRESH_TOKEN={refresh_token}",
        text,
        flags=re.MULTILINE,
    )
    ENV_FILE.write_text(text)


def main() -> None:
    env = read_env()
    client_id = env.get("GOOGLE_ADS_CLIENT_ID", "")
    client_secret = env.get("GOOGLE_ADS_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("ERROR: GOOGLE_ADS_CLIENT_ID / GOOGLE_ADS_CLIENT_SECRET not set in .env")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://127.0.0.1:8080"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=[SCOPE])
    credentials = flow.run_local_server(
        host="127.0.0.1",
        port=8080,
        access_type="offline",
        prompt="consent",
        authorization_prompt_message="Opening a browser to authorize. Sign in as jessica@polymer.co.",
        success_message="Authorized. You can close this tab and return to the terminal.",
    )

    if not credentials.refresh_token:
        print("ERROR: no refresh token returned. Re-run; access_type=offline and prompt=consent are required.")
        sys.exit(1)

    write_refresh_token(credentials.refresh_token)
    print(f"Refresh token written to {ENV_FILE}")


if __name__ == "__main__":
    main()
