#!/usr/bin/env python3
"""
daily_review.py - The scheduled run.

Pulls 14 days of search terms and posts three kinds of Slack message: one
summary, any keyword concentration alerts, and one message per term matched on
the newest day whose text is not its keyword's own text.

Usage:
    python scripts/daily_review.py [--dry-run] [--limit N] [--no-commit]
"""

import argparse
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from classify import classify_terms
from google_ads_read import (
    aggregate_by_ad_group,
    aggregate_by_keyword,
    aggregate_by_term,
    fetch_daily_budget,
    fetch_existing_negatives,
    fetch_list_member_counts,
    fetch_search_terms,
    window_dates,
    window_totals,
)
from slack_messages import (
    build_concentration_blocks,
    build_summary_blocks,
    build_term_blocks,
    button_value,
    concentration_level,
)
from state import (
    added_negative_terms,
    accrue_rejected_spend,
    hard_rejected_terms,
    record_classifications,
    rejected_term_map,
)
from utils import (
    CLASSIFIED_TERMS,
    REJECTED_TERMS,
    already_covered,
    commit_and_push,
    google_ads_client,
    post_to_slack,
    priority_for,
    slack_channel_id,
    update_slack_message,
    write_run_file,
)


def daily_spend_by_term(records) -> dict[str, dict[str, float]]:
    """{search term: {date: cost}}, for accruing spend on rejected terms."""
    per_term = {}
    for record in records:
        per_day = per_term.setdefault(record["search_term"], {})
        per_day[record["date"]] = per_day.get(record["date"], 0.0) + record["cost"]
    return per_term


def is_keyword_text(term: str, keyword_text: str) -> bool:
    """True when the search term is the matched keyword itself, ignoring case and spacing."""
    return " ".join(term.lower().split()) == " ".join(keyword_text.lower().split())


def select_terms_to_post(by_term, newest_day_terms, existing_negatives, added,
                         hard_rejected, soft_rejected):
    """Every term matched on the newest day that is not its keyword's own text.

    The classifier has no say in what is posted. A soft-rejected term is offered
    again whenever it matches; a hard-rejected or added term never is.
    Returns (terms to post, count of terms an existing negative already blocks).
    """
    to_post = []
    covered_count = 0
    for term, entry in by_term.items():
        if term in hard_rejected or term in added:
            continue
        if already_covered(term, existing_negatives):
            covered_count += 1
            continue
        if term not in newest_day_terms:
            continue
        if is_keyword_text(term, entry["keyword_text"]):
            continue

        record = dict(entry)
        record["priority"] = priority_for(entry["day_cost"], entry["week_cost"])

        if term in soft_rejected:
            record["resurfaced"] = True
            record["rejected_on"] = soft_rejected[term]["rejected_on"]
            record["spend_since_rejection"] = soft_rejected[term]["spend_since_rejection"]

        to_post.append(record)

    priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    to_post.sort(key=lambda r: (priority_order[r["priority"]], -r["week_cost"]))
    return to_post, covered_count


def post_term_message(term, channel) -> str | None:
    """Post, then rewrite the buttons with the message ts they now know.

    Slack only reveals ts after the post, and the approve workflow needs it to
    edit this exact message later, so the value is filled in on a second pass.
    """
    blocks, color, fallback = build_term_blocks(term, channel_id=channel)
    message_ts = post_to_slack(blocks, color, fallback, channel=channel)
    if not message_ts:
        return None

    value = button_value(term["search_term"], channel, message_ts)
    # Locate the actions block by type. It is not the last block - the cases and
    # the context line sit below it so the buttons stay above Slack's fold - and
    # a context block also has an "elements" list, which would silently accept a
    # value key and then be rejected as invalid_attachments.
    for block in blocks:
        if block.get("type") == "actions":
            for element in block["elements"]:
                element["value"] = value
            break
    else:
        print("WARN: no actions block found; buttons will not carry the message ts",
              file=sys.stderr)
    update_slack_message(channel, message_ts, blocks, color, fallback)
    return message_ts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="classify and print, post nothing to Slack")
    parser.add_argument("--limit", type=int,
                        help="post at most N terms (for a cheap first pass)")
    parser.add_argument("--no-commit", action="store_true",
                        help="write the CSVs but do not commit")
    args = parser.parse_args()

    run_date = date.today().isoformat()
    client = google_ads_client()

    records = fetch_search_terms(client)
    _, newest_date = window_dates(records)
    by_term = aggregate_by_term(records, newest_date)
    print(f"{len(records)} rows, {len(by_term)} distinct terms, newest date {newest_date}")

    # Campaign and ad group negatives apply only to their own campaign or ad group,
    # and many belong to paused campaigns, so only the shared lists count as blocking.
    existing_negatives = [negative for negative in fetch_existing_negatives(client)
                          if negative["location"].startswith("list:")]
    daily_budget = fetch_daily_budget(client)
    print(f"{len(existing_negatives)} existing negatives, ${daily_budget:.2f} daily budget")

    added = added_negative_terms()
    rejected = rejected_term_map()
    hard_rejected = hard_rejected_terms()
    soft_rejected = {term: row for term, row in rejected.items()
                     if row.get("reject_type") != "hard"}
    newest_day_terms = {record["search_term"] for record in records
                        if record["date"] == newest_date}

    to_post, covered_count = select_terms_to_post(
        by_term, newest_day_terms, existing_negatives, added, hard_rejected, soft_rejected)
    if args.limit:
        to_post = to_post[:args.limit]
        print(f"limiting to {len(to_post)} terms")
    print(f"{covered_count} already covered by a negative, {len(to_post)} to post")

    # The classifier writes the card's case for and against; it no longer decides
    # what is posted. Cases are not stored, so every posted term is classified.
    new_verdicts = classify_terms([term["search_term"] for term in to_post]) if to_post else {}
    for term in to_post:
        verdict = new_verdicts.get(term["search_term"], {})
        term["case_for"] = verdict.get("case_for", "")
        term["case_against"] = verdict.get("case_against", "")
    print(f"{len(to_post)} terms flagged for posting")

    by_keyword = aggregate_by_keyword(records, newest_date)
    alerts = []
    for entry in by_keyword.values():
        level = concentration_level(entry["cost"], daily_budget)
        if level:
            alerts.append((entry, level))
    alerts.sort(key=lambda pair: pair[0]["cost"], reverse=True)
    print(f"{len(alerts)} keyword concentration alert(s)")

    write_run_file(run_date, "flagged_terms.json", to_post)
    write_run_file(run_date, "alerts.json", [
        {"level": level, **{k: v for k, v in entry.items()}} for entry, level in alerts])

    if args.dry_run:
        print("\n--- DRY RUN, nothing posted ---")
        for term in to_post:
            print(f"  {term['priority']:8} ${term['week_cost']:6.2f}  {term['search_term']!r}")
        for entry, level in alerts:
            print(f"  {level:9} ${entry['cost']:6.2f}  {entry['keyword_text']!r} "
                  f"({entry['keyword_match_type']})")
        return

    channel = slack_channel_id()

    totals = window_totals(records, newest_date)
    by_ad_group = aggregate_by_ad_group(records, newest_date)
    counts = {
        "terms_seen":       len(by_term),
        "already_covered":  covered_count,
        "newly_classified": len(new_verdicts),
        "flagged":          len(to_post),
        "awaiting":         len(to_post),
    }
    blocks, color, fallback = build_summary_blocks(
        run_date, newest_date, totals, by_ad_group, daily_budget,
        counts, fetch_list_member_counts(client))
    post_to_slack(blocks, color, fallback, channel=channel)

    for entry, level in alerts:
        blocks, color, fallback = build_concentration_blocks(
            entry, daily_budget, level, newest_date)
        post_to_slack(blocks, color, fallback, channel=channel)

    posted = 0
    for term in to_post:
        if post_term_message(term, channel):
            posted += 1
        # chat.postMessage allows about 1 message per second per channel.
        time.sleep(1)
    print(f"posted {posted} term message(s)")

    def write_state() -> None:
        record_classifications({term: verdict["is_job_seeker"]
                                for term, verdict in new_verdicts.items()}, run_date)
        accrue_rejected_spend(daily_spend_by_term(records))

    write_state()

    if not args.no_commit:
        commit_and_push(
            [str(CLASSIFIED_TERMS), str(REJECTED_TERMS)],
            f"Daily review {run_date}: {len(new_verdicts)} classified, {posted} flagged",
            reapply=write_state,
        )


if __name__ == "__main__":
    main()
