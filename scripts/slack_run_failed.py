#!/usr/bin/env python3
"""
slack_run_failed.py - Post a failure notice with a link to the run.

Called from workflow `if: failure()` steps, mirroring slack_highlight_done.py
in thought-leadership-automation.

Usage:
    python scripts/slack_run_failed.py <workflow name>
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import post_error_to_slack


def main() -> None:
    workflow = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")

    text = f":x: `{workflow}` failed."
    if repository and run_id:
        text += f" <https://github.com/{repository}/actions/runs/{run_id}|View the run>"
    post_error_to_slack(text)


if __name__ == "__main__":
    main()
