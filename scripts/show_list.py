#!/usr/bin/env python3
"""Print the members of one shared set, plus its campaign attachments."""

import sys
from pathlib import Path

from google.ads.googleads.client import GoogleAdsClient

ENV_FILE = Path(__file__).parent.parent / ".env"


def load_env() -> dict:
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def main() -> None:
    shared_set_id = sys.argv[1]
    env = load_env()
    customer_id = env["GOOGLE_ADS_CUSTOMER_ID"]
    client = GoogleAdsClient.load_from_dict({
        "developer_token": env["GOOGLE_ADS_DEVELOPER_TOKEN"],
        "client_id": env["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": env["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": env["GOOGLE_ADS_REFRESH_TOKEN"],
        "use_proto_plus": True,
    }, version="v25")
    service = client.get_service("GoogleAdsService")

    print(f"--- members of shared set {shared_set_id} ---")
    for row in service.search(customer_id=customer_id, query=f"""
        SELECT shared_set.name, shared_criterion.keyword.text,
               shared_criterion.keyword.match_type, shared_criterion.resource_name
        FROM shared_criterion
        WHERE shared_set.id = {shared_set_id}
    """):
        print(f"  {row.shared_criterion.keyword.match_type.name:6}  {row.shared_criterion.keyword.text!r}")

    print(f"--- campaign attachments of shared set {shared_set_id} ---")
    for row in service.search(customer_id=customer_id, query=f"""
        SELECT campaign.id, campaign.name, campaign.status,
               campaign_shared_set.resource_name, campaign_shared_set.status
        FROM campaign_shared_set
        WHERE shared_set.id = {shared_set_id}
    """):
        print(f"  {row.campaign_shared_set.status.name:8}  campaign {row.campaign.id} [{row.campaign.status.name}] {row.campaign.name!r}")


if __name__ == "__main__":
    main()
