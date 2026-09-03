#!/usr/bin/env python3
"""List the Google Ads accounts this credential can reach.

Uses CustomerService.ListAccessibleCustomers, which needs no customer id.
This is the cheapest possible probe of whether the developer token works
against production accounts at all.
"""

import os
import sys
from pathlib import Path

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

ENV_FILE = Path(__file__).parent.parent / ".env"


def load_env() -> dict:
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def main() -> None:
    env = load_env()
    config = {
        "developer_token": env["GOOGLE_ADS_DEVELOPER_TOKEN"],
        "client_id": env["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": env["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": env["GOOGLE_ADS_REFRESH_TOKEN"],
        "use_proto_plus": True,
    }

    client = GoogleAdsClient.load_from_dict(config, version="v25")
    customer_service = client.get_service("CustomerService")

    try:
        response = customer_service.list_accessible_customers()
    except GoogleAdsException as e:
        print(f"FAILED — request_id {e.request_id}")
        for error in e.failure.errors:
            print(f"  error_code: {error.error_code}")
            print(f"  message:    {error.message}")
        sys.exit(1)

    print(f"Accessible accounts ({len(response.resource_names)}):")
    for resource_name in response.resource_names:
        print(f"  {resource_name}")


if __name__ == "__main__":
    main()
