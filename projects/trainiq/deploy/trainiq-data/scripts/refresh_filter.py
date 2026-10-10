"""
refresh_filter.py — issue #73

`should_run()` decides whether an `issues: opened` webhook on
`aaime921/trainiq-data` should trigger the TrainIQ sync job: Grok's GitHub
connector opens a `refresh` issue (title or label `refresh`) from an
account with write access, and that's the only kind of issue this workflow
reacts to — any other issue starts no job.

`sync.yml`'s job-level `if:` duplicates these exact same conditions in
GitHub Actions expression syntax, because a workflow's `if:` can't import
this module. `tests/test_deploy_trainiq_data.py` asserts both agree on the
same fixture payloads, so a future edit to one without the other is caught
there instead of live on GitHub.

This CLI exists only so the two can be exercised the same way locally
(`python refresh_filter.py --event-name issues --payload-json fixture.json`);
the workflow itself never shells out to it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping

ALWAYS_RUN_EVENTS = {"schedule", "workflow_dispatch"}
ALLOWED_AUTHOR_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
REFRESH_TITLE = "refresh"
REFRESH_LABEL = "refresh"


def should_run(event_name: str, payload: Mapping) -> bool:
    if event_name in ALWAYS_RUN_EVENTS:
        return True
    if event_name != "issues":
        return False

    issue = payload.get("issue") or {}
    title = issue.get("title") or ""
    labels = issue.get("labels") or []
    label_names = {label.get("name") for label in labels if isinstance(label, Mapping)}
    is_refresh = title == REFRESH_TITLE or REFRESH_LABEL in label_names
    if not is_refresh:
        return False

    return issue.get("author_association") in ALLOWED_AUTHOR_ASSOCIATIONS


def _parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--payload-json", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = json.loads(args.payload_json.read_text())
    result = should_run(args.event_name, payload)
    print("true" if result else "false")
    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())
