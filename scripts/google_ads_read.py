#!/usr/bin/env python3
"""
google_ads_read.py - Every read this pipeline makes against the Google Ads API.

Query shapes are pinned against captured responses in
~/claude-hub/paid-ads-management-pipeline/investigations/google-ads-api-shapes-2026-09-03/

Run directly to print an inventory:
    python scripts/google_ads_read.py
"""

import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    NEGATIVE_LIST_NAMES,
    customer_id,
    google_ads_client,
    micros_to_currency,
)

# Every field the pipeline needs arrives in one row. No join is required.
SEARCH_TERMS_QUERY = """
    SELECT
      search_term_view.search_term,
      search_term_view.status,
      campaign.id,
      campaign.name,
      ad_group.id,
      ad_group.name,
      segments.date,
      segments.keyword.info.text,
      segments.keyword.info.match_type,
      segments.keyword.ad_group_criterion,
      metrics.impressions,
      metrics.clicks,
      metrics.cost_micros
    FROM search_term_view
    WHERE segments.date DURING LAST_14_DAYS
"""

SHARED_SETS_QUERY = """
    SELECT shared_set.id, shared_set.name, shared_set.type,
           shared_set.status, shared_set.reference_count
    FROM shared_set
    WHERE shared_set.status = ENABLED
"""

SHARED_CRITERIA_QUERY = """
    SELECT shared_set.id, shared_set.name,
           shared_criterion.criterion_id, shared_criterion.resource_name,
           shared_criterion.keyword.text, shared_criterion.keyword.match_type
    FROM shared_criterion
    WHERE shared_criterion.type = KEYWORD
      AND shared_set.status = ENABLED
"""

CAMPAIGN_NEGATIVES_QUERY = """
    SELECT campaign.id, campaign.name,
           campaign_criterion.keyword.text, campaign_criterion.keyword.match_type
    FROM campaign_criterion
    WHERE campaign_criterion.type = KEYWORD
      AND campaign_criterion.negative = TRUE
      AND campaign_criterion.status != REMOVED
"""

AD_GROUP_NEGATIVES_QUERY = """
    SELECT campaign.id, ad_group.id, ad_group.name,
           ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type
    FROM ad_group_criterion
    WHERE ad_group_criterion.type = KEYWORD
      AND ad_group_criterion.negative = TRUE
      AND ad_group_criterion.status != REMOVED
"""

CAMPAIGN_BUDGETS_QUERY = """
    SELECT campaign.id, campaign.name, campaign.status,
           campaign_budget.id, campaign_budget.amount_micros
    FROM campaign
    WHERE campaign.status = ENABLED
"""


def _search(client, query):
    service = client.get_service("GoogleAdsService")
    return list(service.search(customer_id=customer_id(), query=query))


def fetch_search_terms(client) -> list[dict]:
    """Search terms over the last 14 days, one record per term per day per keyword."""
    records = []
    for row in _search(client, SEARCH_TERMS_QUERY):
        keyword = row.segments.keyword
        records.append({
            "search_term":   row.search_term_view.search_term,
            "status":        row.search_term_view.status.name,
            "date":          row.segments.date,
            "campaign_id":   str(row.campaign.id),
            "campaign_name": row.campaign.name,
            "ad_group_id":   str(row.ad_group.id),
            "ad_group_name": row.ad_group.name,
            "keyword_text":       keyword.info.text,
            "keyword_match_type": keyword.info.match_type.name,
            "keyword_criterion":  keyword.ad_group_criterion,
            "impressions": int(row.metrics.impressions),
            "clicks":      int(row.metrics.clicks),
            "cost":        micros_to_currency(row.metrics.cost_micros),
        })
    return records


def fetch_negative_lists(client) -> dict[str, dict]:
    """The shared sets approved terms are written to, resolved by name.

    member_count is deliberately not read - it reported 0 for a list holding 46
    keywords. Members are counted from shared_criterion instead.
    """
    by_name = {}
    for row in _search(client, SHARED_SETS_QUERY):
        by_name[row.shared_set.name] = {
            "id":              str(row.shared_set.id),
            "name":            row.shared_set.name,
            "type":            row.shared_set.type_.name,
            "reference_count": int(row.shared_set.reference_count),
            "resource_name":   f"customers/{customer_id()}/sharedSets/{row.shared_set.id}",
        }

    missing = [name for name in NEGATIVE_LIST_NAMES if name not in by_name]
    if missing:
        raise RuntimeError(
            "Negative keyword list(s) not found in the account: "
            + ", ".join(repr(name) for name in missing)
            + ". Present: " + ", ".join(repr(name) for name in sorted(by_name))
        )
    return {name: by_name[name] for name in NEGATIVE_LIST_NAMES}


def fetch_existing_negatives(client) -> list[dict]:
    """Every negative keyword in the account, from all three locations."""
    negatives = []

    for row in _search(client, SHARED_CRITERIA_QUERY):
        negatives.append({
            "text":       row.shared_criterion.keyword.text,
            "match_type": row.shared_criterion.keyword.match_type.name,
            "location":   f"list:{row.shared_set.name}",
        })

    for row in _search(client, CAMPAIGN_NEGATIVES_QUERY):
        negatives.append({
            "text":       row.campaign_criterion.keyword.text,
            "match_type": row.campaign_criterion.keyword.match_type.name,
            "location":   f"campaign:{row.campaign.name}",
        })

    for row in _search(client, AD_GROUP_NEGATIVES_QUERY):
        negatives.append({
            "text":       row.ad_group_criterion.keyword.text,
            "match_type": row.ad_group_criterion.keyword.match_type.name,
            "location":   f"ad_group:{row.ad_group.name}",
        })

    return negatives


def fetch_list_member_counts(client) -> dict[str, int]:
    """Members per shared set, counted rather than read from member_count."""
    counts = defaultdict(int)
    for row in _search(client, SHARED_CRITERIA_QUERY):
        counts[row.shared_set.name] += 1
    return dict(counts)


def fetch_daily_budget(client) -> float:
    """Summed daily budget across enabled campaigns.

    Budgets are set per campaign, so this is the denominator for both the ad
    group percentages and the keyword concentration thresholds.
    """
    seen_budget_ids = set()
    total = 0.0
    for row in _search(client, CAMPAIGN_BUDGETS_QUERY):
        budget_id = str(row.campaign_budget.id)
        if budget_id in seen_budget_ids:
            continue          # shared budgets must not be counted twice
        seen_budget_ids.add(budget_id)
        total += micros_to_currency(row.campaign_budget.amount_micros)
    return total


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def window_dates(records: list[dict]) -> tuple[str, str]:
    """Yesterday, and the newest date actually present in the data.

    Search terms data lags, so the newest row may predate yesterday.
    """
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    newest = max((r["date"] for r in records), default=yesterday)
    return yesterday, newest


def aggregate_by_term(records: list[dict], newest_date: str) -> dict[str, dict]:
    """Collapse per-day rows into one record per search term.

    day_cost is the newest available day, not literally yesterday, because the
    search terms report lags behind live spend.
    """
    week_start = (date.fromisoformat(newest_date) - timedelta(days=6)).isoformat()

    by_term = {}
    for record in records:
        term = record["search_term"]
        entry = by_term.setdefault(term, {
            "search_term":   term,
            "campaign_id":   record["campaign_id"],
            "campaign_name": record["campaign_name"],
            "ad_group_id":   record["ad_group_id"],
            "ad_group_name": record["ad_group_name"],
            "keyword_text":       record["keyword_text"],
            "keyword_match_type": record["keyword_match_type"],
            "keyword_criterion":  record["keyword_criterion"],
            "day_cost": 0.0, "week_cost": 0.0, "total_cost": 0.0,
            "day_clicks": 0, "week_clicks": 0, "total_clicks": 0,
            "total_impressions": 0,
            "days": {},
        })
        entry["total_cost"] += record["cost"]
        entry["total_clicks"] += record["clicks"]
        entry["total_impressions"] += record["impressions"]
        entry["days"][record["date"]] = entry["days"].get(record["date"], 0.0) + record["cost"]

        if record["date"] == newest_date:
            entry["day_cost"] += record["cost"]
            entry["day_clicks"] += record["clicks"]
        if record["date"] >= week_start:
            entry["week_cost"] += record["cost"]
            entry["week_clicks"] += record["clicks"]

        # Attribute the term to whichever keyword spent the most on it.
        if record["cost"] > 0 and record["cost"] >= entry.get("_top_keyword_cost", 0):
            entry["_top_keyword_cost"] = record["cost"]
            entry["keyword_text"] = record["keyword_text"]
            entry["keyword_match_type"] = record["keyword_match_type"]
            entry["keyword_criterion"] = record["keyword_criterion"]

    for entry in by_term.values():
        entry.pop("_top_keyword_cost", None)
    return by_term


def aggregate_by_keyword(records: list[dict], newest_date: str) -> dict[str, dict]:
    """Spend per keyword criterion on the newest day, with the terms it matched.

    Grouping by ad_group_criterion separates 'ats software' PHRASE from
    'ats software' EXACT, which are distinct criteria with distinct ids.
    """
    by_keyword = {}
    for record in records:
        if record["date"] != newest_date:
            continue
        criterion = record["keyword_criterion"]
        if not criterion:
            continue          # no attributable keyword on this row
        entry = by_keyword.setdefault(criterion, {
            "keyword_criterion":  criterion,
            "keyword_text":       record["keyword_text"],
            "keyword_match_type": record["keyword_match_type"],
            "ad_group_name":      record["ad_group_name"],
            "campaign_name":      record["campaign_name"],
            "cost": 0.0, "clicks": 0,
            "terms": [],
        })
        entry["cost"] += record["cost"]
        entry["clicks"] += record["clicks"]
        entry["terms"].append({
            "search_term": record["search_term"],
            "cost":        record["cost"],
            "clicks":      record["clicks"],
        })

    for entry in by_keyword.values():
        entry["terms"].sort(key=lambda t: t["cost"], reverse=True)
    return by_keyword


def aggregate_by_ad_group(records: list[dict], newest_date: str) -> dict[str, dict]:
    """Spend per ad group on the newest day."""
    by_ad_group = {}
    for record in records:
        if record["date"] != newest_date:
            continue
        entry = by_ad_group.setdefault(record["ad_group_id"], {
            "ad_group_id":   record["ad_group_id"],
            "ad_group_name": record["ad_group_name"],
            "campaign_name": record["campaign_name"],
            "cost": 0.0, "clicks": 0,
        })
        entry["cost"] += record["cost"]
        entry["clicks"] += record["clicks"]
    return by_ad_group


def window_totals(records: list[dict], newest_date: str) -> dict:
    """Spend and clicks for the day, the trailing week, the prior week, and 14 days."""
    newest = date.fromisoformat(newest_date)
    week_start = (newest - timedelta(days=6)).isoformat()
    prior_week_start = (newest - timedelta(days=13)).isoformat()
    prior_week_end = (newest - timedelta(days=7)).isoformat()

    totals = {
        "day":        {"cost": 0.0, "clicks": 0, "impressions": 0},
        "week":       {"cost": 0.0, "clicks": 0, "impressions": 0},
        "prior_week": {"cost": 0.0, "clicks": 0, "impressions": 0},
        "fortnight":  {"cost": 0.0, "clicks": 0, "impressions": 0},
    }
    for record in records:
        buckets = ["fortnight"]
        if record["date"] == newest_date:
            buckets.append("day")
        if record["date"] >= week_start:
            buckets.append("week")
        elif prior_week_start <= record["date"] <= prior_week_end:
            buckets.append("prior_week")
        for bucket in buckets:
            totals[bucket]["cost"] += record["cost"]
            totals[bucket]["clicks"] += record["clicks"]
            totals[bucket]["impressions"] += record["impressions"]
    return totals


def main() -> None:
    client = google_ads_client()
    records = fetch_search_terms(client)
    yesterday, newest = window_dates(records)
    print(f"{len(records)} search term rows; newest date {newest} (yesterday is {yesterday})")

    by_term = aggregate_by_term(records, newest)
    print(f"{len(by_term)} distinct search terms")

    print(f"daily budget: ${fetch_daily_budget(client):.2f}")
    print(f"existing negatives: {len(fetch_existing_negatives(client))}")

    print("negative keyword lists:")
    for name, info in fetch_negative_lists(client).items():
        print(f"  {info['id']}  {info['type']:32}  {name!r}")

    print("members per list:")
    for name, count in sorted(fetch_list_member_counts(client).items()):
        print(f"  {count:5}  {name!r}")

    totals = window_totals(records, newest)
    for label, values in totals.items():
        print(f"  {label:11} ${values['cost']:8.2f}  {values['clicks']:4} clicks")


if __name__ == "__main__":
    main()
