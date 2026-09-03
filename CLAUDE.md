# Paid Ads Management Pipeline

## Tech Stack
Python 3.12, GitHub Actions, Google Ads API v25, Anthropic API, Slack Block Kit

## Source repo
This IS the source repo. Planning documents live in
`~/claude-hub/paid-ads-management-pipeline/`.

## Scope
Google Ads first. Named for paid ads generally because other platforms may follow.

## Architecture
Daily GitHub Actions run pulls the Google Ads search terms report, classifies job-seeker-intent
queries with Claude, and posts them to Slack with Approve & Execute / Reject buttons.

Button clicks are handled by the existing Slack Lambda in `thought-leadership-automation`
(`polymer-content-slack-handler`), which dispatches `workflow_dispatch` into this repo. Slack
requires an HTTP 200 within 3 seconds and GitHub Actions has no inbound endpoint, so the click
half cannot be Actions-only. Everything after the click is Actions.

## Conventions
Follow `thought-leadership-automation` — `scripts/` for entry points, `catalogue/` for committed
CSV state, `utils.py` for shared helpers, `.env` at root loaded via python-dotenv.

## Data policy
This repo is PUBLIC. Committed data is limited to search terms, keyword text, campaign and ad
group names, match types, spend and dates. Nothing else.

## Key files
- Spec: `~/claude-hub/paid-ads-management-pipeline/features/negative-keyword-pipeline-2026-09-03/spec.md`
- API shapes: `~/claude-hub/paid-ads-management-pipeline/investigations/google-ads-api-shapes-2026-09-03/`
- Committed state: `catalogue/`
