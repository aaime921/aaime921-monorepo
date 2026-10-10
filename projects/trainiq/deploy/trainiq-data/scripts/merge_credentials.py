"""
merge_credentials.py — issue #73

Resolves, per credential secret, whether `sync.yml` should export the
value currently sitting in the GitHub Actions secret or the value TrainIQ
rotated and stored at the end of a previous run (encrypted, `creds.enc` —
see the architecture doc's "Credentials" decision). The BO can change a
GitHub secret at any time (e.g. re-running `scripts/push_secrets_to_github.py`
after reconnecting a provider by hand); that edit must always win over a
now-stale rotated value.

Rule: `stored[name]["seed_sha256"]` records the sha256 of the secret value
that was live when that rotated value was produced (`build_stored()`,
called at the end of a run). If the secret still hashes to that value, the
BO hasn't touched it since that rotation, so the newer rotated value wins.
If the hash differs, the BO changed the secret after that rotation, so the
current secret wins — a changed secret always beats a stale rotation.

`CREDENTIAL_SECRET_NAMES` is the exact, explicit set of `TRAINIQ_*` secrets
this merges — deliberately not "every `TRAINIQ_`-prefixed env var", which
would also sweep in non-credential settings like `TRAINIQ_HEADLESS` or
`TRAINIQ_DB_PATH` and mask their (non-secret, short, collision-prone)
values for the rest of the job's logs. This same list is what `README.md`
(AC2) documents as the per-provider secrets the BO must create.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Mapping, Optional

CREDENTIAL_SECRET_NAMES: frozenset[str] = frozenset({
    # Strava (OAuth)
    "TRAINIQ_STRAVA_REFRESH_TOKEN",
    "TRAINIQ_STRAVA_ACCESS_TOKEN",
    "TRAINIQ_STRAVA_EXPIRES_AT",
    # Strava (unofficial, session-cookie)
    "TRAINIQ_STRAVA_UNOFFICIAL_SESSION_COOKIE",
    "TRAINIQ_STRAVA_UNOFFICIAL_SESSION_OBTAINED_AT",
    "TRAINIQ_STRAVA_UNOFFICIAL_SESSION_EXPIRES_AT",
    # Peloton
    "TRAINIQ_PELOTON_EMAIL",
    "TRAINIQ_PELOTON_PASSWORD",
    "TRAINIQ_PELOTON_SESSION_ID",
    "TRAINIQ_PELOTON_SESSION_EXPIRES_AT",
    "TRAINIQ_PELOTON_MANUAL_BEARER_TOKEN",
    "TRAINIQ_PELOTON_OAUTH_ACCESS_TOKEN",
    "TRAINIQ_PELOTON_OAUTH_REFRESH_TOKEN",
    "TRAINIQ_PELOTON_OAUTH_EXPIRES_AT",
    # Eufy
    "TRAINIQ_EUFY_EMAIL",
    "TRAINIQ_EUFY_PASSWORD",
    "TRAINIQ_EUFY_ACCESS_TOKEN",
    "TRAINIQ_EUFY_REFRESH_TOKEN",
    "TRAINIQ_EUFY_EXPIRES_AT",
})


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def merge(env: Mapping[str, str], stored: Optional[Mapping[str, Mapping[str, str]]]) -> dict[str, str]:
    """`env`: this run's secret values (name -> value), already filtered to
    `CREDENTIAL_SECRET_NAMES`. `stored`: last run's rotated values plus
    their seed hash (name -> {"value", "seed_sha256"}), or None on a first
    run / no prior `creds.enc`. Returns the merged name -> value mapping to
    export for this run."""
    stored = stored or {}
    merged: dict[str, str] = {}
    for name, value in env.items():
        entry = stored.get(name)
        if entry is not None and _sha256(value) == entry.get("seed_sha256"):
            merged[name] = entry["value"]
        else:
            merged[name] = value
    # A secret the BO deleted but that was still rotated last run (e.g. a
    # provider temporarily unset) stays available until the next rotation.
    for name, entry in stored.items():
        if name not in merged:
            merged[name] = entry["value"]
    return merged


def build_stored(env: Mapping[str, str], rotated: Mapping[str, str]) -> dict[str, dict[str, str]]:
    """Builds the next run's `stored` structure (what gets encrypted into
    `creds.enc`) from this run's rotated values (`--credentials-out`,
    trainiq's env backend) and the secret values this run started with —
    the seed each rotated value is checked against next time."""
    return {
        name: {"value": value, "seed_sha256": _sha256(env.get(name, ""))}
        for name, value in rotated.items()
    }


def _parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["merge", "seal"], default="merge")
    parser.add_argument(
        "--stored", type=Path, default=None,
        help="merge mode: decrypted creds.enc contents from the prior run (JSON), if any.",
    )
    parser.add_argument(
        "--github-env", type=Path, default=None,
        help="merge mode: defaults to $GITHUB_ENV, as set by GitHub Actions for every step.",
    )
    parser.add_argument(
        "--rotated", type=Path, default=None,
        help="seal mode: this run's --credentials-out file (trainiq's env backend), if any.",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="seal mode: where to write the next run's creds.enc contents (JSON, pre-encryption).",
    )
    return parser.parse_args(argv)


def _env_secrets() -> dict[str, str]:
    return {name: os.environ[name] for name in CREDENTIAL_SECRET_NAMES if os.environ.get(name)}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.mode == "seal":
        rotated = {}
        if args.rotated is not None and args.rotated.exists():
            rotated = json.loads(args.rotated.read_text()).get("credentials") or {}
        stored = build_stored(_env_secrets(), rotated)
        payload = json.dumps({"version": 1, "credentials": stored}, indent=2)
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(payload)
        else:
            print(payload)
        return 0

    stored = None
    if args.stored is not None and args.stored.exists():
        stored = json.loads(args.stored.read_text()).get("credentials")

    merged = merge(_env_secrets(), stored)

    github_env = args.github_env
    if github_env is None and os.environ.get("GITHUB_ENV"):
        github_env = Path(os.environ["GITHUB_ENV"])

    for value in merged.values():
        print("::add-mask::" + value)

    if github_env is not None:
        with github_env.open("a") as f:
            for name, value in merged.items():
                f.write(f"{name}={value}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
