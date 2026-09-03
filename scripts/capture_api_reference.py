#!/usr/bin/env python3
"""Record every Google Ads API request this pipeline makes, with its raw response.

Each call is written as two files under <out_dir>/responses/:
    <name>.request.json   the exact request that was sent
    <name>.response.json  the raw response, unabridged

Mutates run with validate_only=True unless the call is marked read-only, so
running this changes nothing in the account.

Usage:
    python scripts/capture_api_reference.py <out_dir>
"""

import json
import sys
from pathlib import Path

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.protobuf.json_format import MessageToDict

ENV_FILE = Path(__file__).parent.parent / ".env"

READ_QUERIES = {
    "search_terms_last_30_days": """
        SELECT
          search_term_view.search_term,
          search_term_view.status,
          campaign.id,
          campaign.name,
          ad_group.id,
          ad_group.name,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions
        FROM search_term_view
        WHERE segments.date DURING LAST_30_DAYS
        ORDER BY metrics.cost_micros DESC
    """,
    "search_terms_with_matched_keyword": """
        SELECT
          search_term_view.search_term,
          campaign.id,
          campaign.name,
          ad_group.id,
          ad_group.name,
          segments.keyword.info.text,
          segments.keyword.info.match_type,
          segments.search_term_match_type,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros
        FROM search_term_view
        WHERE segments.date DURING LAST_30_DAYS
        ORDER BY metrics.cost_micros DESC
    """,
    "shared_sets": """
        SELECT shared_set.id, shared_set.name, shared_set.type,
               shared_set.status, shared_set.member_count, shared_set.reference_count
        FROM shared_set
        WHERE shared_set.status = ENABLED
    """,
    "shared_set_members": """
        SELECT shared_set.id, shared_set.name, shared_criterion.criterion_id,
               shared_criterion.resource_name, shared_criterion.keyword.text,
               shared_criterion.keyword.match_type
        FROM shared_criterion
        WHERE shared_criterion.type = KEYWORD
    """,
    "campaign_shared_set_attachments": """
        SELECT campaign.id, campaign.name, shared_set.id, shared_set.name,
               campaign_shared_set.resource_name, campaign_shared_set.status
        FROM campaign_shared_set
        WHERE campaign_shared_set.status = ENABLED
    """,
    "campaign_negative_keywords": """
        SELECT campaign.id, campaign.name, campaign_criterion.criterion_id,
               campaign_criterion.resource_name, campaign_criterion.keyword.text,
               campaign_criterion.keyword.match_type
        FROM campaign_criterion
        WHERE campaign_criterion.type = KEYWORD
          AND campaign_criterion.negative = TRUE
          AND campaign_criterion.status != REMOVED
    """,
    "ad_group_negative_keywords": """
        SELECT campaign.id, ad_group.id, ad_group.name,
               ad_group_criterion.criterion_id, ad_group_criterion.resource_name,
               ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type
        FROM ad_group_criterion
        WHERE ad_group_criterion.type = KEYWORD
          AND ad_group_criterion.negative = TRUE
          AND ad_group_criterion.status != REMOVED
    """,
    "campaigns": """
        SELECT campaign.id, campaign.name, campaign.status,
               campaign.advertising_channel_type
        FROM campaign
        WHERE campaign.status != REMOVED
    """,
    "ad_groups": """
        SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, ad_group.status
        FROM ad_group
        WHERE ad_group.status != REMOVED
    """,
    "customer_negative_criteria": """
        SELECT customer_negative_criterion.id, customer_negative_criterion.type,
               customer_negative_criterion.negative_keyword_list.shared_set
        FROM customer_negative_criterion
    """,
}


def load_env() -> dict:
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def write(out_dir: Path, name: str, suffix: str, payload) -> None:
    path = out_dir / f"{name}.{suffix}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"  wrote {path.name}")


def capture_read(service, customer_id, out_dir, name, query):
    print(f"\n[{name}]")
    write(out_dir, name, "request", {
        "service": "GoogleAdsService",
        "method": "Search",
        "customer_id": customer_id,
        "query": query.strip(),
    })
    try:
        rows = [MessageToDict(row._pb, preserving_proto_field_name=True)
                for row in service.search(customer_id=customer_id, query=query)]
    except GoogleAdsException as e:
        write(out_dir, name, "response", {
            "error": True,
            "request_id": e.request_id,
            "errors": [{"error_code": str(err.error_code).strip(), "message": err.message}
                       for err in e.failure.errors],
        })
        print(f"  FAILED — {[str(err.error_code).strip() for err in e.failure.errors]}")
        return []
    write(out_dir, name, "response", {"row_count": len(rows), "rows": rows})
    print(f"  {len(rows)} rows")
    return rows


def capture_duplicate_probe(client, customer_id, out_dir, existing):
    """Add a keyword that already exists in the list, validate_only, to learn the error."""
    name = "add_duplicate_negative_validate_only"
    print(f"\n[{name}]")
    shared_set_resource_name = existing["shared_set"]
    text = existing["text"]
    match_type = existing["match_type"]

    operation = client.get_type("SharedCriterionOperation")
    shared_criterion = operation.create
    shared_criterion.shared_set = shared_set_resource_name
    shared_criterion.keyword.text = text
    shared_criterion.keyword.match_type = client.enums.KeywordMatchTypeEnum[match_type]

    request = client.get_type("MutateSharedCriteriaRequest")
    request.customer_id = customer_id
    request.operations.append(operation)
    request.validate_only = True

    write(out_dir, name, "request", {
        "service": "SharedCriterionService",
        "method": "MutateSharedCriteria",
        "customer_id": customer_id,
        "validate_only": True,
        "operations": [{
            "create": {
                "shared_set": shared_set_resource_name,
                "keyword": {"text": text, "match_type": match_type},
            }
        }],
        "note": f"{text!r} already exists in this list as {match_type}",
    })

    try:
        response = client.get_service("SharedCriterionService").mutate_shared_criteria(request=request)
    except GoogleAdsException as e:
        payload = {
            "error": True,
            "request_id": e.request_id,
            "errors": [{"error_code": str(err.error_code).strip(), "message": err.message}
                       for err in e.failure.errors],
        }
        write(out_dir, name, "response", payload)
        for err in e.failure.errors:
            print(f"  {str(err.error_code).strip()} — {err.message}")
        return
    write(out_dir, name, "response", MessageToDict(response._pb, preserving_proto_field_name=True))
    print("  ACCEPTED — duplicate raised no error")


def capture_add_probe(client, customer_id, out_dir, shared_set_resource_name):
    """Shape of a genuine add, validate_only, exact + phrase as the pipeline will send it."""
    name = "add_negative_exact_and_phrase_validate_only"
    print(f"\n[{name}]")
    text = "zzz shape probe never added"

    operations = []
    for match_type in ("EXACT", "PHRASE"):
        operation = client.get_type("SharedCriterionOperation")
        shared_criterion = operation.create
        shared_criterion.shared_set = shared_set_resource_name
        shared_criterion.keyword.text = text
        shared_criterion.keyword.match_type = client.enums.KeywordMatchTypeEnum[match_type]
        operations.append(operation)

    request = client.get_type("MutateSharedCriteriaRequest")
    request.customer_id = customer_id
    request.operations.extend(operations)
    request.validate_only = True

    write(out_dir, name, "request", {
        "service": "SharedCriterionService",
        "method": "MutateSharedCriteria",
        "customer_id": customer_id,
        "validate_only": True,
        "operations": [
            {"create": {"shared_set": shared_set_resource_name,
                        "keyword": {"text": text, "match_type": mt}}}
            for mt in ("EXACT", "PHRASE")
        ],
    })

    try:
        response = client.get_service("SharedCriterionService").mutate_shared_criteria(request=request)
    except GoogleAdsException as e:
        write(out_dir, name, "response", {
            "error": True,
            "request_id": e.request_id,
            "errors": [{"error_code": str(err.error_code).strip(), "message": err.message}
                       for err in e.failure.errors],
        })
        print(f"  FAILED — {[str(err.error_code).strip() for err in e.failure.errors]}")
        return
    write(out_dir, name, "response", MessageToDict(response._pb, preserving_proto_field_name=True))
    print("  validate_only accepted")


def main() -> None:
    out_dir = Path(sys.argv[1]) / "responses"
    out_dir.mkdir(parents=True, exist_ok=True)

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

    members = []
    for name, query in READ_QUERIES.items():
        rows = capture_read(service, customer_id, out_dir, name, query)
        if name == "shared_set_members":
            members = rows

    candidate_list = None
    existing = None
    for row in members:
        shared_set = row.get("shared_set", {})
        criterion = row.get("shared_criterion", {})
        if shared_set.get("name") == "Candidate Traffic - Exclude":
            candidate_list = criterion.get("resource_name", "").rsplit("/", 1)[0].replace(
                "sharedCriteria", "sharedSets")
            if existing is None:
                existing = {
                    "shared_set": f"customers/{customer_id}/sharedSets/{shared_set['id']}",
                    "text": criterion["keyword"]["text"],
                    "match_type": criterion["keyword"]["match_type"],
                }
            break

    if existing:
        capture_duplicate_probe(client, customer_id, out_dir, existing)
        capture_add_probe(client, customer_id, out_dir, existing["shared_set"])
    else:
        print("\nSkipped mutate probes — could not find a member of 'Candidate Traffic - Exclude'")


if __name__ == "__main__":
    main()
