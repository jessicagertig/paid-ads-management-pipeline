#!/usr/bin/env python3
"""Count negative keywords by location, so totals are exact rather than eyeballed."""

from collections import Counter
from pathlib import Path

from google.ads.googleads.client import GoogleAdsClient

ENV_FILE = Path(__file__).parent.parent / ".env"

CAMPAIGN_NEGATIVES = """
    SELECT campaign.id, campaign.name, campaign.status,
           campaign_criterion.keyword.match_type
    FROM campaign_criterion
    WHERE campaign_criterion.type = KEYWORD
      AND campaign_criterion.negative = TRUE
      AND campaign_criterion.status != REMOVED
"""

AD_GROUP_NEGATIVES = """
    SELECT campaign.name, ad_group.id, ad_group.name,
           ad_group_criterion.keyword.match_type
    FROM ad_group_criterion
    WHERE ad_group_criterion.type = KEYWORD
      AND ad_group_criterion.negative = TRUE
      AND ad_group_criterion.status != REMOVED
"""

SHARED_MEMBERS = """
    SELECT shared_set.id, shared_set.name, shared_criterion.keyword.match_type
    FROM shared_criterion
    WHERE shared_criterion.type = KEYWORD
"""


def load_env() -> dict:
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def main() -> None:
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

    print("=== CAMPAIGN-LEVEL NEGATIVE KEYWORDS, by campaign ===")
    by_campaign = Counter()
    campaign_status = {}
    match_types = Counter()
    total = 0
    for row in service.search(customer_id=customer_id, query=CAMPAIGN_NEGATIVES):
        key = (row.campaign.id, row.campaign.name)
        by_campaign[key] += 1
        campaign_status[key] = row.campaign.status.name
        match_types[row.campaign_criterion.keyword.match_type.name] += 1
        total += 1
    for (cid, name), count in by_campaign.most_common():
        print(f"  {count:4}  {campaign_status[(cid, name)]:8}  {cid}  {name!r}")
    print(f"  ----\n  {total:4}  TOTAL campaign-level negatives")
    print(f"  by match type: {dict(match_types)}")

    print("\n=== AD-GROUP-LEVEL NEGATIVE KEYWORDS ===")
    by_ad_group = Counter()
    ag_total = 0
    for row in service.search(customer_id=customer_id, query=AD_GROUP_NEGATIVES):
        by_ad_group[(row.ad_group.id, row.ad_group.name, row.campaign.name)] += 1
        ag_total += 1
    for (agid, agname, cname), count in by_ad_group.most_common():
        print(f"  {count:4}  ad_group {agid} {agname!r}  in {cname!r}")
    if not by_ad_group:
        print("  (none)")
    print(f"  ----\n  {ag_total:4}  TOTAL ad-group-level negatives")

    print("\n=== SHARED LIST MEMBERS ===")
    by_list = Counter()
    list_match_types = {}
    sl_total = 0
    for row in service.search(customer_id=customer_id, query=SHARED_MEMBERS):
        key = (row.shared_set.id, row.shared_set.name)
        by_list[key] += 1
        list_match_types.setdefault(key, Counter())[row.shared_criterion.keyword.match_type.name] += 1
        sl_total += 1
    for key, count in by_list.most_common():
        print(f"  {count:4}  list {key[0]} {key[1]!r}  {dict(list_match_types[key])}")
    print(f"  ----\n  {sl_total:4}  TOTAL shared list members")

    print(f"\n=== GRAND TOTAL negative keywords in account: {total + ag_total + sl_total} ===")


if __name__ == "__main__":
    main()
