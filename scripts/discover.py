#!/usr/bin/env python3
"""Read-only inventory of the Google Ads account the pipeline will manage.

Prints shared negative keyword lists, which campaigns each is attached to,
account-level negative criteria, campaigns, and ad groups.
"""

import sys
from pathlib import Path

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

ENV_FILE = Path(__file__).parent.parent / ".env"

SHARED_SETS = """
    SELECT shared_set.id, shared_set.name, shared_set.type,
           shared_set.status, shared_set.member_count, shared_set.reference_count
    FROM shared_set
    WHERE shared_set.status = ENABLED
"""

CAMPAIGN_SHARED_SETS = """
    SELECT campaign.id, campaign.name, shared_set.id, shared_set.name,
           campaign_shared_set.status
    FROM campaign_shared_set
    WHERE campaign_shared_set.status = ENABLED
"""

CAMPAIGNS = """
    SELECT campaign.id, campaign.name, campaign.status,
           campaign.advertising_channel_type
    FROM campaign
    WHERE campaign.status != REMOVED
"""

AD_GROUPS = """
    SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, ad_group.status
    FROM ad_group
    WHERE ad_group.status != REMOVED
"""

CUSTOMER_NEGATIVES = """
    SELECT customer_negative_criterion.id, customer_negative_criterion.type,
           customer_negative_criterion.negative_keyword_list.shared_set
    FROM customer_negative_criterion
"""

CAMPAIGN_NEGATIVES = """
    SELECT campaign.id, campaign.name, campaign_criterion.keyword.text,
           campaign_criterion.keyword.match_type
    FROM campaign_criterion
    WHERE campaign_criterion.type = KEYWORD
      AND campaign_criterion.negative = TRUE
      AND campaign_criterion.status != REMOVED
"""


def load_env() -> dict:
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def run(google_ads_service, customer_id, label, query, formatter):
    print(f"\n=== {label} ===")
    try:
        rows = list(google_ads_service.search(customer_id=customer_id, query=query))
    except GoogleAdsException as e:
        codes = "; ".join(str(err.error_code).strip() for err in e.failure.errors)
        print(f"  FAILED  {codes}")
        for err in e.failure.errors:
            print(f"    {err.message}")
        return []
    if not rows:
        print("  (none)")
    for row in rows:
        print("  " + formatter(row))
    return rows


def main() -> None:
    env = load_env()
    customer_id = env["GOOGLE_ADS_CUSTOMER_ID"]
    config = {
        "developer_token": env["GOOGLE_ADS_DEVELOPER_TOKEN"],
        "client_id": env["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": env["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": env["GOOGLE_ADS_REFRESH_TOKEN"],
        "use_proto_plus": True,
    }
    client = GoogleAdsClient.load_from_dict(config, version="v25")
    google_ads_service = client.get_service("GoogleAdsService")

    run(google_ads_service, customer_id, "SHARED SETS (all types)", SHARED_SETS,
        lambda r: f"id={r.shared_set.id}  type={r.shared_set.type_.name}  members={r.shared_set.member_count}  campaigns={r.shared_set.reference_count}  {r.shared_set.name!r}")

    run(google_ads_service, customer_id, "SHARED SET -> CAMPAIGN ATTACHMENTS", CAMPAIGN_SHARED_SETS,
        lambda r: f"list {r.shared_set.id} {r.shared_set.name!r}  ->  campaign {r.campaign.id} {r.campaign.name!r}")

    run(google_ads_service, customer_id, "ACCOUNT-LEVEL NEGATIVE CRITERIA", CUSTOMER_NEGATIVES,
        lambda r: f"id={r.customer_negative_criterion.id}  type={r.customer_negative_criterion.type_.name}  shared_set={r.customer_negative_criterion.negative_keyword_list.shared_set}")

    run(google_ads_service, customer_id, "CAMPAIGNS", CAMPAIGNS,
        lambda r: f"id={r.campaign.id}  {r.campaign.status.name:8}  {r.campaign.advertising_channel_type.name:12}  {r.campaign.name!r}")

    run(google_ads_service, customer_id, "AD GROUPS", AD_GROUPS,
        lambda r: f"campaign {r.campaign.id} {r.campaign.name!r}  ->  ad_group {r.ad_group.id} {r.ad_group.name!r}  {r.ad_group.status.name}")

    run(google_ads_service, customer_id, "CAMPAIGN-LEVEL NEGATIVE KEYWORDS", CAMPAIGN_NEGATIVES,
        lambda r: f"campaign {r.campaign.id} {r.campaign.name!r}  {r.campaign_criterion.keyword.match_type.name:6}  {r.campaign_criterion.keyword.text!r}")


if __name__ == "__main__":
    main()
