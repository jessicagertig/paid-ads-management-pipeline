#!/usr/bin/env python3
"""Detach a shared set from every campaign, then remove the shared set itself.

Usage:
    python delete_shared_set.py <shared_set_id> [--execute]

Without --execute the mutates run with validate_only=True and change nothing.
"""

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


def report_failure(e: GoogleAdsException, label: str) -> None:
    print(f"  {label} FAILED — request_id {e.request_id}")
    for error in e.failure.errors:
        print(f"    error_code: {str(error.error_code).strip()}")
        print(f"    message:    {error.message}")


def main() -> None:
    shared_set_id = sys.argv[1]
    execute = "--execute" in sys.argv
    validate_only = not execute

    env = load_env()
    customer_id = env["GOOGLE_ADS_CUSTOMER_ID"]
    client = GoogleAdsClient.load_from_dict({
        "developer_token": env["GOOGLE_ADS_DEVELOPER_TOKEN"],
        "client_id": env["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": env["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": env["GOOGLE_ADS_REFRESH_TOKEN"],
        "use_proto_plus": True,
    }, version="v25")

    google_ads_service = client.get_service("GoogleAdsService")
    campaign_shared_set_service = client.get_service("CampaignSharedSetService")
    shared_set_service = client.get_service("SharedSetService")

    print(f"mode: {'EXECUTE' if execute else 'VALIDATE ONLY'}")

    attachments = list(google_ads_service.search(customer_id=customer_id, query=f"""
        SELECT campaign.id, campaign.name, campaign_shared_set.resource_name
        FROM campaign_shared_set
        WHERE shared_set.id = {shared_set_id}
          AND campaign_shared_set.status = ENABLED
    """))

    print(f"\ndetaching {len(attachments)} campaign attachment(s)")
    if attachments:
        operations = []
        for row in attachments:
            print(f"  campaign {row.campaign.id} {row.campaign.name!r}")
            operation = client.get_type("CampaignSharedSetOperation")
            operation.remove = row.campaign_shared_set.resource_name
            operations.append(operation)

        request = client.get_type("MutateCampaignSharedSetsRequest")
        request.customer_id = customer_id
        request.operations.extend(operations)
        request.validate_only = validate_only
        try:
            campaign_shared_set_service.mutate_campaign_shared_sets(request=request)
        except GoogleAdsException as e:
            report_failure(e, "detach")
            sys.exit(1)
        print("  detach OK")

    print(f"\nremoving shared set {shared_set_id}")
    operation = client.get_type("SharedSetOperation")
    operation.remove = shared_set_service.shared_set_path(customer_id, shared_set_id)

    request = client.get_type("MutateSharedSetsRequest")
    request.customer_id = customer_id
    request.operations.append(operation)
    request.validate_only = validate_only
    try:
        shared_set_service.mutate_shared_sets(request=request)
    except GoogleAdsException as e:
        report_failure(e, "remove shared set")
        sys.exit(1)
    print("  remove OK")


if __name__ == "__main__":
    main()
