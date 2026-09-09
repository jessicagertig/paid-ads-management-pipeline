#!/usr/bin/env python3
"""
handle_action.py - What happens when a Slack button is clicked.

Dispatched by workflow_dispatch from the Slack Lambda. Runs in a fresh checkout,
so the term's figures are re-derived from the Google Ads API rather than read
from a run file that is not in the repo.

Usage:
    python scripts/handle_action.py --action approve|reject|hard-reject \
        --search-term "..." --channel-id C... --message-ts 1756... [--user-name ...]
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from google_ads_read import aggregate_by_term, fetch_search_terms, window_dates
from google_ads_write import add_negative_keyword
from slack_messages import approved_note, rejected_note, retire_buttons
from state import (
    HARD_REJECT,
    SOFT_REJECT,
    record_added_negatives,
    record_rejection,
)
from utils import (
    ADDED_NEGATIVES,
    NEGATIVE_LIST_NAMES,
    REJECTED_TERMS,
    commit_and_push,
    fetch_message_blocks,
    google_ads_client,
    post_error_to_slack,
    update_slack_message,
)


def record_decision(channel_id: str, message_ts: str, note: str) -> bool:
    """Take the buttons off the clicked card and write the decision on it.

    The card is edited, never rebuilt: its blocks are read back from Slack so
    everything it was posted with survives. A message that cannot be read is
    left untouched, which keeps its buttons and lets the click be repeated.
    """
    if not (channel_id and message_ts):
        print("WARN: no channel or message ts on the button; card left as it is",
              file=sys.stderr)
        return False

    existing = fetch_message_blocks(channel_id, message_ts)
    if existing is None:
        print(f"WARN: message {message_ts} left as it is", file=sys.stderr)
        return False

    blocks, color, fallback = existing
    return update_slack_message(channel_id, message_ts,
                                retire_buttons(blocks, note), color, fallback)


def term_record(client, search_term: str) -> dict:
    """Re-derive this term's figures from the API.

    Falls back to a shell record if the term has aged out of the 14-day window,
    so a late click still records the decision.
    """
    records = fetch_search_terms(client)
    _, newest_date = window_dates(records)
    by_term = aggregate_by_term(records, newest_date)
    if search_term in by_term:
        return by_term[search_term]

    print(f"WARN: {search_term!r} not in the current 14-day window; using a shell record",
          file=sys.stderr)
    return {
        "search_term": search_term,
        "campaign_name": "(not in the last 14 days)",
        "ad_group_name": "(not in the last 14 days)",
        "keyword_text": "", "keyword_match_type": "",
        "day_cost": 0.0, "week_cost": 0.0, "total_cost": 0.0,
        "day_clicks": 0, "week_clicks": 0, "total_clicks": 0,
        "total_impressions": 0, "days": {},
    }


def do_approve(client, term, channel_id, message_ts, user_name) -> int:
    result = add_negative_keyword(client, term["search_term"], validate_only=False)

    if not result["ok"]:
        message = (f"Failed to add negative keyword `{term['search_term']}`.\n"
                   f"```{result['error']}```")
        post_error_to_slack(message)
        print(f"ERROR: {result['error']}", file=sys.stderr)
        return 1

    if not result["written"] and result["skipped"]:
        print("Already present everywhere; nothing written.")

    added_on = date.today().isoformat()
    added_rows = [{
        "search_term":    term["search_term"],
        "match_type":     entry["match_type"],
        "shared_set_name": entry["list"],
        "campaign_name":  term["campaign_name"],
        "ad_group_name":  term["ad_group_name"],
        "matched_keyword": term["keyword_text"],
        "matched_keyword_match_type": term["keyword_match_type"],
        "added_on":       added_on,
    } for entry in result["written"]]
    record_added_negatives(added_rows)

    lists_written = sorted({entry["list"] for entry in result["written"]}) or list(NEGATIVE_LIST_NAMES)
    record_decision(channel_id, message_ts, approved_note(added_on, lists_written))

    for entry in result["written"]:
        print(f"wrote {entry['match_type']:6} -> {entry['list']}")
    for note in result["skipped"]:
        print(f"skipped: {note}")

    commit_and_push([str(ADDED_NEGATIVES)],
                    f"Add negative keyword: {term['search_term']}",
                    reapply=lambda: record_added_negatives(added_rows))
    return 0


def do_reject(term, channel_id, message_ts, user_name, reject_type) -> int:
    rejected_on = date.today().isoformat()
    last_counted = max(term["days"], default=rejected_on)

    def write_rejection() -> None:
        record_rejection(
            term["search_term"],
            spend_at_rejection=term["total_cost"],
            last_counted_date=last_counted,
            reject_type=reject_type,
            rejected_on=rejected_on,
        )

    write_rejection()

    record_decision(channel_id, message_ts,
                    rejected_note(rejected_on, reject_type=reject_type))

    label = "hard" if reject_type == HARD_REJECT else "soft"
    print(f"recorded {label} rejection of {term['search_term']!r} "
          f"at ${term['total_cost']:.2f}")

    commit_and_push([str(REJECTED_TERMS)],
                    f"{label.capitalize()} reject: {term['search_term']}",
                    reapply=write_rejection)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", required=True,
                        choices=["approve", "reject", "hard-reject"])
    parser.add_argument("--search-term", required=True)
    parser.add_argument("--channel-id", default="")
    parser.add_argument("--message-ts", default="")
    parser.add_argument("--user-name", default="")
    args = parser.parse_args()

    search_term = args.search_term.strip()
    if not search_term:
        print("ERROR: empty search term", file=sys.stderr)
        sys.exit(1)

    client = google_ads_client()
    term = term_record(client, search_term)

    if args.action == "approve":
        sys.exit(do_approve(client, term, args.channel_id, args.message_ts, args.user_name))
    reject_type = HARD_REJECT if args.action == "hard-reject" else SOFT_REJECT
    sys.exit(do_reject(term, args.channel_id, args.message_ts, args.user_name, reject_type))


if __name__ == "__main__":
    main()
