#!/usr/bin/env python3
"""
slack_messages.py - Block Kit builders for the three message kinds.

Slack exposes no background colour and no full border for messages. The
attachment ``color`` field renders a bar down the left edge, which together
with a header block and an emoji is the whole visual vocabulary available.
"""

import hashlib
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    COLOR_RED_ALERT,
    COLOR_SUMMARY,
    COLOR_TERM,
    COLOR_WARNING,
    CONCENTRATION_RED_ALERT,
    CONCENTRATION_WARNING,
    NEGATIVE_LIST_NAMES,
    PRIORITY_EMOJI,
    SHARED_SET_MAX_MEMBERS,
    SHARED_SET_WARN_RATIO,
)

# Slack truncates a section's text at 3000 characters and a header at 150.
SECTION_TEXT_LIMIT = 2900
HEADER_TEXT_LIMIT = 150


def _header(text: str) -> dict:
    return {"type": "header",
            "text": {"type": "plain_text", "text": text[:HEADER_TEXT_LIMIT], "emoji": True}}


def _section(text: str) -> dict:
    return {"type": "section",
            "text": {"type": "mrkdwn", "text": text[:SECTION_TEXT_LIMIT]}}


def _context(text: str) -> dict:
    return {"type": "context",
            "elements": [{"type": "mrkdwn", "text": text[:SECTION_TEXT_LIMIT]}]}


def _pct(part: float, whole: float) -> str:
    return f"{(part / whole * 100):.0f}%" if whole else "n/a"


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------

def build_summary_blocks(run_date, newest_date, totals, by_ad_group, daily_budget,
                         counts, list_member_counts) -> tuple[list, str, str]:
    """Counts and arithmetic only. No classifier involvement."""
    day = totals["day"]
    week = totals["week"]
    prior = totals["prior_week"]
    fortnight = totals["fortnight"]

    delta = week["cost"] - prior["cost"]
    if prior["cost"] > 0:
        direction = "up" if delta > 0 else "down"
        change = f"{direction} ${abs(delta):.2f} ({abs(delta) / prior['cost'] * 100:.0f}%)"
    else:
        change = "no spend in the prior week to compare"

    blocks = [
        _header(f"\U0001f4ca Paid Ads Daily Summary — {run_date}"),
        _section(
            f"*Spend*\n"
            f"• Latest day ({newest_date}): *${day['cost']:.2f}* "
            f"({_pct(day['cost'], daily_budget)} of ${daily_budget:.2f} daily budget), "
            f"{day['clicks']} clicks\n"
            f"• Last 7 days: *${week['cost']:.2f}*, {week['clicks']} clicks\n"
            f"• Prior 7 days: ${prior['cost']:.2f}, {prior['clicks']} clicks\n"
            f"• Last 14 days: ${fortnight['cost']:.2f}, {fortnight['clicks']} clicks\n"
            f"• Week over week: {change}"
        ),
    ]

    if by_ad_group:
        lines = [
            f"• {entry['ad_group_name']} — ${entry['cost']:.2f} "
            f"({_pct(entry['cost'], daily_budget)} of daily budget), {entry['clicks']} clicks"
            for entry in sorted(by_ad_group.values(), key=lambda e: e["cost"], reverse=True)
        ]
        blocks.append(_section(f"*Spend by ad group ({newest_date})*\n" + "\n".join(lines)))
    else:
        blocks.append(_section(f"*Spend by ad group ({newest_date})*\nNo ad group spend."))

    blocks.append(_section(
        f"*Terms*\n"
        f"• {counts['terms_seen']} distinct search terms in the last 14 days\n"
        f"• {counts['already_covered']} already covered by an existing negative\n"
        f"• {counts['newly_classified']} newly classified this run\n"
        f"• {counts['flagged']} flagged as job-seeker intent\n"
        f"• {counts['awaiting']} awaiting a click"
    ))

    warnings = []
    for name in NEGATIVE_LIST_NAMES:
        members = list_member_counts.get(name, 0)
        if members >= SHARED_SET_MAX_MEMBERS * SHARED_SET_WARN_RATIO:
            warnings.append(f"• `{name}` holds {members:,} of {SHARED_SET_MAX_MEMBERS:,} keywords")
    if warnings:
        blocks.append(_section("*Negative keyword list capacity*\n" + "\n".join(warnings)))

    blocks.append(_context(
        f"Search terms data lags roughly a day; latest available date is {newest_date}."
    ))

    fallback = f"Paid Ads Daily Summary {run_date} — ${day['cost']:.2f} on {newest_date}"
    return blocks, COLOR_SUMMARY, fallback


# ---------------------------------------------------------------------------
# Keyword concentration alert
# ---------------------------------------------------------------------------

def concentration_level(cost: float, daily_budget: float) -> str | None:
    """WARNING, RED ALERT, or None. Spend arithmetic only."""
    if not daily_budget:
        return None
    share = cost / daily_budget
    if share >= CONCENTRATION_RED_ALERT:
        return "RED ALERT"
    if share >= CONCENTRATION_WARNING:
        return "WARNING"
    return None


def build_concentration_blocks(keyword_entry, daily_budget, level,
                               newest_date) -> tuple[list, str, str]:
    """One keyword absorbing an outsized share of the day's budget.

    States facts. No recommended action - the remedy is sometimes disabling the
    phrase variant rather than adding negatives, and that judgement is the user's.
    """
    emoji = "\U0001f6a8" if level == "RED ALERT" else "⚠️"
    color = COLOR_RED_ALERT if level == "RED ALERT" else COLOR_WARNING
    share = _pct(keyword_entry["cost"], daily_budget)

    blocks = [
        _header(f"{emoji} {level}: keyword taking {share} of daily budget"),
        _section(
            f"*Keyword:* `{keyword_entry['keyword_text']}`  "
            f"({keyword_entry['keyword_match_type']} match)\n"
            f"*Ad group:* {keyword_entry['ad_group_name']}\n"
            f"*Campaign:* {keyword_entry['campaign_name']}\n"
            f"*Spend on {newest_date}:* ${keyword_entry['cost']:.2f} of "
            f"${daily_budget:.2f} budget ({share}), {keyword_entry['clicks']} clicks"
        ),
    ]

    term_lines = [
        f"• `{term['search_term']}` — ${term['cost']:.2f}, {term['clicks']} clicks"
        for term in keyword_entry["terms"]
    ]
    blocks.append(_section(
        f"*Search terms this keyword matched ({len(keyword_entry['terms'])})*\n"
        + "\n".join(term_lines)
    ))
    blocks.append(_context(
        "Reported on spend alone. The classifier has no input into this alert."
    ))

    fallback = (f"{level}: '{keyword_entry['keyword_text']}' "
                f"({keyword_entry['keyword_match_type']}) took {share} of daily budget")
    return blocks, color, fallback


# ---------------------------------------------------------------------------
# Term suggestion
# ---------------------------------------------------------------------------

def term_id_for(search_term: str) -> str:
    """Stable short id for a term, so a reposted term keeps the same action_id."""
    return hashlib.sha256(search_term.encode("utf-8")).hexdigest()[:12]


def button_value(search_term: str, channel_id: str, message_ts: str) -> str:
    """Pack the state the approve/reject workflows need into one button value.

    The search term is percent-encoded so the pipe delimiter is unambiguous even
    for terms containing one. The term travels in the value rather than an id
    into local state, because the dispatched workflow runs in a fresh checkout
    and cannot see this run's files.
    """
    return "|".join([
        urllib.parse.quote(search_term, safe=""),
        channel_id or "",
        message_ts or "",
    ])


def build_term_blocks(term, channel_id="", message_ts="") -> tuple[list, str, str]:
    """One flagged term with Approve & Execute, Reject and Never Show Again."""
    term_id = term_id_for(term["search_term"])
    value = button_value(term["search_term"], channel_id, message_ts)
    priority = term["priority"]
    emoji = PRIORITY_EMOJI.get(priority, "⚪")

    blocks = [
        _header(f"{emoji} {priority} — {term['search_term']}"),
        _section(
            f"*Search term:* `{term['search_term']}`\n"
            f"*Campaign:* {term['campaign_name']}\n"
            f"*Ad group:* {term['ad_group_name']}\n"
            f"*Matched keyword:* `{term['keyword_text']}` "
            f"({term['keyword_match_type']} match)"
        ),
        _section(
            f"*Spend*\n"
            f"• Latest day: ${term['day_cost']:.2f}, {term['day_clicks']} clicks\n"
            f"• Last 7 days: ${term['week_cost']:.2f}, {term['week_clicks']} clicks\n"
            f"• Last 14 days: ${term['total_cost']:.2f}, {term['total_clicks']} clicks, "
            f"{term['total_impressions']} impressions"
        ),
        _section(
            f"*Case for adding*\n{term['case_for']}\n\n"
            f"*Case against*\n{term['case_against']}"
        ),
        _section(
            "*Proposed negative keyword*\n"
            f"`{term['search_term']}` as *exact* and *phrase*, added to both "
            + " and ".join(f"`{name}`" for name in NEGATIVE_LIST_NAMES)
        ),
    ]

    if term.get("resurfaced"):
        blocks.append(_context(
            f"Previously rejected on {term['rejected_on']}. Has cost "
            f"${term['spend_since_rejection']} since."
        ))

    blocks.append({
        "type": "actions",
        "elements": [
            {"type": "button",
             "text": {"type": "plain_text", "text": "Approve & Execute", "emoji": True},
             "style": "primary",
             "action_id": f"gads_approve_{term_id}",
             "value": value},
            {"type": "button",
             "text": {"type": "plain_text", "text": "Reject", "emoji": True},
             "action_id": f"gads_reject_{term_id}",
             "value": value},
            {"type": "button",
             "text": {"type": "plain_text", "text": "Never Show Again", "emoji": True},
             "style": "danger",
             "action_id": f"gads_hardreject_{term_id}",
             "value": value},
        ],
    })

    fallback = f"{priority}: {term['search_term']} — ${term['week_cost']:.2f} over 7 days"
    return blocks, COLOR_TERM, fallback


def build_executed_blocks(term, lists_written, added_on, user_name=None) -> tuple[list, str, str]:
    """Replaces a term message after a successful write. Buttons retired."""
    who = f" by {user_name}" if user_name else ""
    blocks = [
        _header(f"✅ Added — {term['search_term']}"),
        _section(
            f"*Search term:* `{term['search_term']}`\n"
            f"*Added as:* exact and phrase\n"
            f"*Lists:* " + ", ".join(f"`{name}`" for name in lists_written) + "\n"
            f"*Campaign:* {term['campaign_name']}  |  *Ad group:* {term['ad_group_name']}"
        ),
        _context(f"Approved and executed{who} on {added_on}."),
    ]
    return blocks, COLOR_TERM, f"Added negative keyword: {term['search_term']}"


def build_rejected_blocks(term, rejected_on, reject_type="soft",
                          user_name=None) -> tuple[list, str, str]:
    """Replaces a term message after rejection. Buttons retired."""
    who = f" by {user_name}" if user_name else ""
    is_hard = reject_type == "hard"

    heading = ("\U0001f515 Never showing again" if is_hard else "\U0001f6ab Rejected")
    note = ("It will not be surfaced again, whatever it spends."
            if is_hard else
            "It will resurface if its spend grows.")

    blocks = [
        _header(f"{heading} — {term['search_term']}"),
        _section(
            f"*Search term:* `{term['search_term']}`\n"
            f"*Spend at rejection:* ${term['total_cost']:.2f} over 14 days\n"
            f"*Campaign:* {term['campaign_name']}  |  *Ad group:* {term['ad_group_name']}"
        ),
        _context(f"Rejected{who} on {rejected_on}. {note}"),
    ]
    label = "Never showing again" if is_hard else "Rejected"
    return blocks, COLOR_TERM, f"{label}: {term['search_term']}"
