#!/usr/bin/env python3
"""
state.py - The three committed CSVs under catalogue/.

This repo is public. These files carry search terms, keyword text, campaign and
ad group names, match types, spend and dates. Nothing else.
"""

import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import ADDED_NEGATIVES, CLASSIFIED_TERMS, REJECTED_TERMS

ADDED_COLUMNS = [
    "search_term", "match_type", "shared_set_name", "campaign_name",
    "ad_group_name", "matched_keyword", "matched_keyword_match_type", "added_on",
]

REJECTED_COLUMNS = [
    "search_term", "reject_type", "rejected_on", "spend_at_rejection",
    "spend_since_rejection", "last_counted_date",
]

# A soft reject is "not now" - the term keeps accruing spend and resurfaces if
# it crosses the HIGH threshold. A hard reject is "never again" - the term is
# dropped before classification and never resurfaces at any spend.
SOFT_REJECT = "soft"
HARD_REJECT = "hard"

CLASSIFIED_COLUMNS = ["search_term", "is_job_seeker", "classified_on"]


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _append(path: Path, columns: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def _rewrite(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# added_negatives.csv
# ---------------------------------------------------------------------------

def read_added_negatives() -> list[dict]:
    return _read(ADDED_NEGATIVES)


def added_negative_terms() -> set[str]:
    return {row["search_term"] for row in read_added_negatives()}


def record_added_negatives(rows: list[dict]) -> None:
    _append(ADDED_NEGATIVES, ADDED_COLUMNS, rows)


# ---------------------------------------------------------------------------
# rejected_terms.csv
# ---------------------------------------------------------------------------

def read_rejected_terms() -> list[dict]:
    return _read(REJECTED_TERMS)


def rejected_term_map() -> dict[str, dict]:
    return {row["search_term"]: row for row in read_rejected_terms()}


def hard_rejected_terms() -> set[str]:
    """Terms never to surface again, at any spend."""
    return {row["search_term"] for row in read_rejected_terms()
            if row.get("reject_type") == HARD_REJECT}


def record_rejection(search_term: str, spend_at_rejection: float,
                     last_counted_date: str, reject_type: str = SOFT_REJECT,
                     rejected_on: str = None) -> None:
    """Append a rejection.

    spend_at_rejection is frozen at the observed figure and may legitimately be
    zero, since LOW-priority terms surface on classification rather than spend.
    spend_since_rejection is a separate column so the baseline survives for
    comparison; incrementing the baseline itself would destroy it.

    A soft reject that is later hard rejected is upgraded in place, so a term
    the user has finally had enough of stops resurfacing.
    """
    rows = read_rejected_terms()
    by_term = {row["search_term"]: row for row in rows}

    if search_term in by_term:
        existing = by_term[search_term]
        if reject_type == HARD_REJECT and existing.get("reject_type") != HARD_REJECT:
            existing["reject_type"] = HARD_REJECT
            existing["rejected_on"] = rejected_on or date.today().isoformat()
            _rewrite(REJECTED_TERMS, REJECTED_COLUMNS, rows)
        return

    _append(REJECTED_TERMS, REJECTED_COLUMNS, [{
        "search_term":           search_term,
        "reject_type":           reject_type,
        "rejected_on":           rejected_on or date.today().isoformat(),
        "spend_at_rejection":    f"{spend_at_rejection:.2f}",
        "spend_since_rejection": "0.00",
        "last_counted_date":     last_counted_date,
    }])


def accrue_rejected_spend(daily_spend_by_term: dict[str, dict[str, float]]) -> list[dict]:
    """Add each new day's spend to every rejected term's running total.

    ``daily_spend_by_term`` maps search term to {date: cost}. Only dates newer
    than last_counted_date are added, so days still inside the 14-day pull
    window are never counted twice.

    Returns the rows whose running total changed.
    """
    rows = read_rejected_terms()
    if not rows:
        return []

    changed = []
    for row in rows:
        if row.get("reject_type") == HARD_REJECT:
            continue          # never resurfaces, so accrual would be noise
        per_day = daily_spend_by_term.get(row["search_term"], {})
        last_counted = row.get("last_counted_date") or row["rejected_on"]
        new_days = {d: cost for d, cost in per_day.items() if d > last_counted}
        if not new_days:
            continue
        added = sum(new_days.values())
        row["spend_since_rejection"] = f"{float(row['spend_since_rejection']) + added:.2f}"
        row["last_counted_date"] = max(new_days)
        changed.append(row)

    if changed:
        _rewrite(REJECTED_TERMS, REJECTED_COLUMNS, rows)
    return changed


def rejected_terms_to_resurface(threshold: float) -> list[dict]:
    """Soft-rejected terms whose accrued spend has crossed the HIGH threshold.

    Hard rejects never resurface, whatever they cost.
    """
    return [row for row in read_rejected_terms()
            if row.get("reject_type") != HARD_REJECT
            and float(row["spend_since_rejection"]) >= threshold]


# ---------------------------------------------------------------------------
# classified_terms.csv
# ---------------------------------------------------------------------------

def read_classified_terms() -> list[dict]:
    return _read(CLASSIFIED_TERMS)


def classified_term_map() -> dict[str, bool]:
    return {row["search_term"]: row["is_job_seeker"] == "true"
            for row in read_classified_terms()}


def record_classifications(verdicts: dict[str, bool], classified_on: str = None) -> None:
    known = set(classified_term_map())
    stamp = classified_on or date.today().isoformat()
    _append(CLASSIFIED_TERMS, CLASSIFIED_COLUMNS, [
        {"search_term": term, "is_job_seeker": "true" if is_job_seeker else "false",
         "classified_on": stamp}
        for term, is_job_seeker in verdicts.items() if term not in known
    ])
