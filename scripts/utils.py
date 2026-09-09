#!/usr/bin/env python3
"""
utils.py - Shared utilities for the paid ads management pipeline.
All scripts import paths, clients, and helpers from here.

Environment variables are loaded from .env at project root on import.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load .env from project root (parent of this scripts/ directory).
# Only load if file exists - in CI, env vars are set directly.
_ENV_FILE = Path(__file__).parent.parent / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE, override=True)

# ---------------------------------------------------------------------------
# Base paths
# ---------------------------------------------------------------------------
SCRIPTS_DIR   = Path(__file__).parent
ROOT_DIR      = SCRIPTS_DIR.parent
CATALOGUE_DIR = ROOT_DIR / "catalogue"
OUTPUT_DIR    = ROOT_DIR / "output"
RUNS_DIR      = OUTPUT_DIR / "runs"

# Committed state files
ADDED_NEGATIVES  = CATALOGUE_DIR / "added_negatives.csv"
REJECTED_TERMS   = CATALOGUE_DIR / "rejected_terms.csv"
CLASSIFIED_TERMS = CATALOGUE_DIR / "classified_terms.csv"

# ---------------------------------------------------------------------------
# Google Ads configuration
# ---------------------------------------------------------------------------
GOOGLE_ADS_API_VERSION = "v25"

# Negative keyword lists that approved terms are written to. Resolved by NAME at
# runtime so a rename in the Google Ads UI surfaces as a clear error rather than
# silently writing nowhere.
NEGATIVE_LIST_NAMES = (
    "Candidate Traffic - Exclude",
    "account-level negative keywords list",
)

# Both match types are added for every approved term.
NEGATIVE_MATCH_TYPES = ("EXACT", "PHRASE")

# KeywordInfo.text limits, enforced by the API.
KEYWORD_TEXT_MAX_CHARS = 80
KEYWORD_TEXT_MAX_WORDS = 10

# Google Ads account limit; the daily summary warns as a list approaches it.
SHARED_SET_MAX_MEMBERS = 5000
SHARED_SET_WARN_RATIO  = 0.9

# ---------------------------------------------------------------------------
# Priority bands
# ---------------------------------------------------------------------------
# Spend only. The classifier has no input into priority.
CRITICAL_DAY_SPEND = 15.00   # one term consuming the entire daily budget
HIGH_WEEK_SPEND    = 5.00
HIGH_DAY_SPEND     = 2.00

PRIORITY_EMOJI = {
    "CRITICAL": "\U0001f6a8",   # rotating light
    "HIGH":     "\U0001f534",   # red circle
    "MEDIUM":   "\U0001f7e1",   # yellow circle
    "LOW":      "⚪",       # white circle
}

# ---------------------------------------------------------------------------
# Keyword concentration thresholds, as a share of daily budget
# ---------------------------------------------------------------------------
CONCENTRATION_WARNING   = 0.50
CONCENTRATION_RED_ALERT = 0.75

# ---------------------------------------------------------------------------
# Slack message colours. Slack exposes no background fill and no full border;
# the attachment colour renders a bar down the left edge of the message.
# ---------------------------------------------------------------------------
COLOR_SUMMARY   = "#3b82f6"   # blue
COLOR_WARNING   = "#f59e0b"   # amber
COLOR_RED_ALERT = "#ef4444"   # red
COLOR_TERM      = "#9ca3af"   # grey

# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
# Config, not a literal, so a model bake-off can swap it without code changes.
CLASSIFIER_MODEL = os.environ.get("CLASSIFIER_MODEL", "claude-opus-5")


# ---------------------------------------------------------------------------
# Google Ads client
# ---------------------------------------------------------------------------

def google_ads_client():
    """Build a GoogleAdsClient from environment variables."""
    from google.ads.googleads.client import GoogleAdsClient

    required = [
        "GOOGLE_ADS_DEVELOPER_TOKEN",
        "GOOGLE_ADS_CLIENT_ID",
        "GOOGLE_ADS_CLIENT_SECRET",
        "GOOGLE_ADS_REFRESH_TOKEN",
    ]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    config = {
        "developer_token": os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"],
        "client_id":       os.environ["GOOGLE_ADS_CLIENT_ID"],
        "client_secret":   os.environ["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token":   os.environ["GOOGLE_ADS_REFRESH_TOKEN"],
        "use_proto_plus":  True,
    }
    login_customer_id = os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID")
    if login_customer_id:
        config["login_customer_id"] = login_customer_id
    return GoogleAdsClient.load_from_dict(config, version=GOOGLE_ADS_API_VERSION)


def customer_id() -> str:
    value = os.environ.get("GOOGLE_ADS_CUSTOMER_ID", "")
    if not value:
        print("ERROR: GOOGLE_ADS_CUSTOMER_ID not set", file=sys.stderr)
        sys.exit(1)
    return value.replace("-", "")


def micros_to_currency(micros) -> float:
    """cost_micros is an int64; the JSON form is a string. Both handled."""
    return int(micros or 0) / 1_000_000


# ---------------------------------------------------------------------------
# Keyword text validity
# ---------------------------------------------------------------------------

def keyword_text_problem(text: str) -> str | None:
    """Return why this text cannot be a keyword, or None if it is fine.

    KeywordInfo.text is capped at 80 characters and 10 words; exceeding either
    raises CriterionError.KEYWORD_TEXT_TOO_LONG or KEYWORD_HAS_TOO_MANY_WORDS.
    """
    if not text or not text.strip():
        return "empty"
    if len(text) > KEYWORD_TEXT_MAX_CHARS:
        return f"{len(text)} characters, limit is {KEYWORD_TEXT_MAX_CHARS}"
    word_count = len(text.split())
    if word_count > KEYWORD_TEXT_MAX_WORDS:
        return f"{word_count} words, limit is {KEYWORD_TEXT_MAX_WORDS}"
    return None


def phrase_covers(negative_text: str, search_term: str) -> bool:
    """True if a PHRASE-match negative would block this search term.

    Negative phrase match blocks any query containing the negative's words in
    the same order, with any words before or after.
    """
    negative_words = negative_text.split()
    term_words = search_term.split()
    if not negative_words or len(negative_words) > len(term_words):
        return False
    for start in range(len(term_words) - len(negative_words) + 1):
        if term_words[start:start + len(negative_words)] == negative_words:
            return True
    return False


def already_covered(search_term: str, existing_negatives: list[dict]) -> dict | None:
    """Return the negative that already blocks this term, or None.

    ``existing_negatives`` entries need ``text``, ``match_type`` and ``location``.
    Negative match types nest: broad blocks the most, then phrase, then exact.
    """
    term = search_term.strip().lower()
    for negative in existing_negatives:
        text = negative["text"].strip().lower()
        match_type = negative["match_type"]
        if match_type == "EXACT" and text == term:
            return negative
        if match_type == "PHRASE" and phrase_covers(text, term):
            return negative
        if match_type == "BROAD" and all(word in term.split() for word in text.split()):
            return negative
    return None


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------

def priority_for(day_spend: float, week_spend: float) -> str:
    """Spend-only priority band. No classifier input."""
    if day_spend >= CRITICAL_DAY_SPEND:
        return "CRITICAL"
    if week_spend >= HIGH_WEEK_SPEND or day_spend >= HIGH_DAY_SPEND:
        return "HIGH"
    if week_spend > 0 or day_spend > 0:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

def slack_channel_id() -> str:
    value = os.environ.get("SLACK_PAID_ADS_CHANNEL_ID", "")
    if not value:
        print("ERROR: SLACK_PAID_ADS_CHANNEL_ID not set", file=sys.stderr)
        sys.exit(1)
    return value


def post_to_slack(blocks: list, color: str, fallback_text: str,
                  channel: str = None) -> str | None:
    """Post a Block Kit message wrapped in a coloured attachment.

    Slack has no background colour for messages. The attachment ``color`` renders
    a bar down the left edge, which is the only colour affordance available.

    Returns the message ts on success, None on failure.
    """
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print("ERROR: SLACK_BOT_TOKEN not set", file=sys.stderr)
        return None

    response = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "channel": channel or slack_channel_id(),
            "text": fallback_text,
            "attachments": [{"color": color, "blocks": blocks}],
        },
        timeout=15,
    )
    data = response.json() if response.content else {}
    if not data.get("ok"):
        print(f"ERROR: Slack post failed: {data.get('error', response.status_code)}",
              file=sys.stderr)
        return None
    return data.get("ts")


def fetch_message_blocks(channel: str, message_ts: str):
    """Read one message's own blocks, colour and fallback text back from Slack.

    Editing a card in place means keeping what it already says, so the blocks
    come from the message itself rather than being rebuilt from today's figures,
    which have moved on since it was posted.

    Returns (blocks, color, fallback_text), or None when the message cannot be
    read. None means leave the card alone: overwriting it with a rebuilt one is
    the behaviour this replaced.
    """
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print("ERROR: SLACK_BOT_TOKEN not set", file=sys.stderr)
        return None

    response = requests.get(
        "https://slack.com/api/conversations.history",
        headers={"Authorization": f"Bearer {token}"},
        params={"channel": channel, "latest": message_ts,
                "inclusive": "true", "limit": 1},
        timeout=15,
    )
    data = response.json() if response.content else {}
    messages = data.get("messages") or []
    if not data.get("ok") or not messages:
        print(f"ERROR: could not read message {message_ts}: "
              f"{data.get('error', response.status_code)}", file=sys.stderr)
        return None

    message = messages[0]
    if message.get("ts") != message_ts:
        print(f"ERROR: asked for message {message_ts}, got {message.get('ts')}",
              file=sys.stderr)
        return None

    # Term cards are posted as a single coloured attachment, so the blocks live
    # there rather than on the message.
    attachment = (message.get("attachments") or [{}])[0]
    blocks = attachment.get("blocks") or message.get("blocks") or []
    if not blocks:
        print(f"ERROR: message {message_ts} has no blocks to keep", file=sys.stderr)
        return None

    return blocks, attachment.get("color", ""), message.get("text", "")


def update_slack_message(channel: str, message_ts: str, blocks: list,
                         color: str, fallback_text: str) -> bool:
    """Replace an existing message in place. Used to retire the buttons."""
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print("ERROR: SLACK_BOT_TOKEN not set", file=sys.stderr)
        return False

    response = requests.post(
        "https://slack.com/api/chat.update",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "channel": channel,
            "ts": message_ts,
            "text": fallback_text,
            "attachments": [{"color": color, "blocks": blocks}],
        },
        timeout=15,
    )
    data = response.json() if response.content else {}
    if not data.get("ok"):
        print(f"ERROR: Slack update failed: {data.get('error', response.status_code)}",
              file=sys.stderr)
        return False
    return True


def post_error_to_slack(text: str) -> bool:
    """Post a plain error message. Cannot recursively error-post if Slack broke."""
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_PAID_ADS_CHANNEL_ID")
    if not token or not channel:
        print(f"WARN: Slack not configured; error dropped: {text}", file=sys.stderr)
        return False
    response = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}"},
        json={"channel": channel, "text": text},
        timeout=10,
    )
    data = response.json() if response.content else {}
    if not data.get("ok"):
        print(f"WARN: Slack error post failed: {data.get('error')}", file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

def call_anthropic(client, model, messages, system=None, max_tokens=8192,
                   max_retries=5, initial_delay=60.0, label=None):
    """Call the Anthropic API with retry on overload and rate limit errors.

    ``system`` may be a list of content blocks, which is how a cache_control
    breakpoint is placed on the stable prefix.
    """
    import anthropic as anth

    tag = f"[{label}] " if label else ""
    print(f"  {tag}API call: model={model}")
    delay = initial_delay

    for attempt in range(max_retries):
        try:
            kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages}
            if system:
                kwargs["system"] = system

            if max_tokens > 16_384:
                with client.messages.stream(**kwargs) as stream:
                    response = stream.get_final_message()
            else:
                response = client.messages.create(**kwargs)

            usage = response.usage
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
            print(f"  {tag}Usage: {usage.input_tokens:,} in / {usage.output_tokens:,} out"
                  f"  cache_write={cache_write:,} cache_read={cache_read:,}")
            return response

        except anth.APIStatusError as exc:
            if exc.status_code in (429, 529):
                print(f"  {tag}API error {exc.status_code}")
                if attempt < max_retries - 1:
                    print(f"  {tag}Waiting {delay:.0f}s before retry "
                          f"({attempt + 1}/{max_retries}) ...")
                    time.sleep(delay)
                    delay *= 2
                else:
                    print(f"  {tag}FAILED after {max_retries} attempts.")
                    raise
            else:
                raise


def extract_text_from_response(response) -> str:
    """Concatenate every text block in an Anthropic response."""
    return "".join(block.text for block in response.content
                   if getattr(block, "type", None) == "text")


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

def commit_and_push(paths: list[str], message: str, reapply=None,
                    attempts: int = 5) -> None:
    """Stage, commit and push. No-op when nothing changed.

    Paths that do not exist are skipped rather than passed to git, which exits
    128 on a missing pathspec. A state file only appears once its first row is
    written, so on early runs some of these legitimately do not exist yet.

    Button clicks arrive in bursts, so two runs routinely race on the same CSV.
    The loser resets to the pushed state and calls ``reapply`` to write its rows
    onto the winner's file, then commits again. Rebasing instead conflicts,
    because both runs append to the same end of the file, and a union merge
    would corrupt rejected_terms.csv, which record_rejection and
    accrue_rejected_spend rewrite in place rather than append to.

    ``reapply`` must re-perform this run's state writes against whatever is on
    disk. Without it a lost race has nothing to retry with and raises, rather
    than dropping the rows silently.
    """
    present = [path for path in paths if Path(path).exists()]
    if not present:
        print("No state files to commit.")
        return

    subprocess.run(["git", "config", "--local", "user.email", "action@github.com"], check=True)
    subprocess.run(["git", "config", "--local", "user.name", "GitHub Action"], check=True)
    branch = current_branch()

    for attempt in range(1, attempts + 1):
        subprocess.run(["git", "add"] + present, check=True)
        if subprocess.run(["git", "diff", "--staged", "--quiet"]).returncode == 0:
            print("Nothing to commit.")
            return
        subprocess.run(["git", "commit", "-m", message], check=True)
        subprocess.run(["git", "fetch", "origin", branch], check=True)

        rebased = subprocess.run(["git", "rebase", f"origin/{branch}"]).returncode == 0
        if rebased and subprocess.run(["git", "push"]).returncode == 0:
            return
        if not rebased:
            subprocess.run(["git", "rebase", "--abort"], check=False)

        if reapply is None:
            raise RuntimeError(f"lost a push race on {', '.join(present)} "
                               f"and has no reapply callback to retry with")

        print(f"attempt {attempt}/{attempts}: another run pushed first; "
              f"re-applying onto {branch}", file=sys.stderr)
        subprocess.run(["git", "reset", "--hard", f"origin/{branch}"], check=True)
        reapply()

    raise RuntimeError(f"could not push {', '.join(present)} after {attempts} attempts")


def current_branch() -> str:
    result = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Run files
# ---------------------------------------------------------------------------

def run_dir_for(run_date: str) -> Path:
    """Per-run output directory. Not committed; used to look up term records."""
    path = RUNS_DIR / run_date
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_run_file(run_date: str, name: str, payload) -> Path:
    path = run_dir_for(run_date) / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def read_run_file(run_date: str, name: str):
    path = RUNS_DIR / run_date / name
    if not path.exists():
        return None
    return json.loads(path.read_text())
