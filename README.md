# Paid Ads Management Pipeline

Daily review of Google Ads search terms. Flags job-seeker-intent queries wasting spend on ads
meant for employers, posts them to Slack for approval, and writes approved terms into negative
keyword lists.

## Setup

    cp .env.example .env       # fill in credentials
    python3.12 -m venv .venv
    .venv/bin/pip install -r scripts/requirements.txt

Mint a Google Ads refresh token once:

    .venv/bin/python scripts/generate_refresh_token.py

## Scripts

| Script | Does |
|---|---|
| `generate_refresh_token.py` | one-time OAuth flow, writes the refresh token to `.env` |
| `list_accounts.py` | accounts this credential can reach |
| `describe_accounts.py` | names each account, flags managers and test accounts |
| `discover.py` | negative keyword lists, campaigns, ad groups, existing negatives |
| `count_negatives.py` | exact negative keyword counts by location |
| `show_list.py` | members and campaign attachments of one shared set |
| `capture_api_reference.py` | records every request and response as documentation fixtures |
