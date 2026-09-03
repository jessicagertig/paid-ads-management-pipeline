#!/usr/bin/env python3
"""
google_ads_write.py - Adding negative keywords.

Approved terms go to BOTH lists as BOTH exact and phrase - four operations per
term. Both targets are SharedSets (one NEGATIVE_KEYWORDS, one
ACCOUNT_LEVEL_NEGATIVE_KEYWORDS), so both take the same
SharedCriterionService.MutateSharedCriteria path.

validate_only is NOT a duplicate check. Re-adding an existing keyword returned
an empty success response when probed against the live account, so duplicates
are prevented by reading and diffing, never by relying on the API to refuse.
"""

import sys
from pathlib import Path

from google.ads.googleads.errors import GoogleAdsException

sys.path.insert(0, str(Path(__file__).parent))
from google_ads_read import fetch_existing_negatives, fetch_negative_lists
from utils import (
    NEGATIVE_MATCH_TYPES,
    customer_id,
    google_ads_client,
    keyword_text_problem,
)


def _already_present(search_term, match_type, list_name, existing_negatives) -> bool:
    """Exact text-and-match-type match within one list. Not fuzzy coverage.

    Coverage logic belongs in the filter step; here the only question is whether
    this precise row already exists, so the mutate does not create a duplicate.
    """
    term = search_term.strip().lower()
    for negative in existing_negatives:
        if (negative["text"].strip().lower() == term
                and negative["match_type"] == match_type
                and negative["location"] == f"list:{list_name}"):
            return True
    return False


def add_negative_keyword(client, search_term, validate_only=False) -> dict:
    """Add one term to both lists as exact and phrase.

    Returns {"ok", "written", "skipped", "error"}.
    """
    problem = keyword_text_problem(search_term)
    if problem:
        return {"ok": False, "written": [], "skipped": [],
                "error": f"not a valid keyword: {problem}"}

    lists = fetch_negative_lists(client)
    existing = fetch_existing_negatives(client)

    operations = []
    planned = []
    skipped = []

    for list_name, list_info in lists.items():
        for match_type in NEGATIVE_MATCH_TYPES:
            if _already_present(search_term, match_type, list_name, existing):
                skipped.append(f"{match_type} in {list_name} (already present)")
                continue
            operation = client.get_type("SharedCriterionOperation")
            shared_criterion = operation.create
            shared_criterion.shared_set = list_info["resource_name"]
            shared_criterion.keyword.text = search_term
            shared_criterion.keyword.match_type = client.enums.KeywordMatchTypeEnum[match_type]
            operations.append(operation)
            planned.append({"list": list_name, "match_type": match_type})

    if not operations:
        return {"ok": True, "written": [], "skipped": skipped, "error": None}

    request = client.get_type("MutateSharedCriteriaRequest")
    request.customer_id = customer_id()
    request.operations.extend(operations)
    request.validate_only = validate_only

    try:
        client.get_service("SharedCriterionService").mutate_shared_criteria(request=request)
    except GoogleAdsException as exc:
        detail = "; ".join(
            f"{str(error.error_code).strip()}: {error.message}"
            for error in exc.failure.errors
        )
        return {"ok": False, "written": [], "skipped": skipped, "error": detail}

    return {"ok": True, "written": planned, "skipped": skipped, "error": None}


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: google_ads_write.py <search term> [--execute]")
        sys.exit(1)
    search_term = sys.argv[1]
    validate_only = "--execute" not in sys.argv

    client = google_ads_client()
    result = add_negative_keyword(client, search_term, validate_only=validate_only)

    print(f"mode: {'VALIDATE ONLY' if validate_only else 'EXECUTE'}")
    print(f"term: {search_term!r}")
    for entry in result["written"]:
        print(f"  would write: {entry['match_type']:6} -> {entry['list']}"
              if validate_only else
              f"  wrote: {entry['match_type']:6} -> {entry['list']}")
    for note in result["skipped"]:
        print(f"  skipped: {note}")
    if result["error"]:
        print(f"  ERROR: {result['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
