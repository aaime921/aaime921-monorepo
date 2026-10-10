# #73 Cloud sync on trainiq-data: QA verification

Re-verification of PR #87 (head 31b3156; supersedes #85). Verdict: **FAIL** (AC2/AC6 docs, per BO's AC6 approval comment). Fixtures only; live Actions run is the BO's.

Tests: `pytest tests/test_deploy_trainiq_data.py` 35 passed. Full `pytest tests/`: 433 passed, 2 failed (`test_peloton_csv_import.py`, local-only CSV path), 411 errors from the sandbox-only keyring fixture (no D-Bus); all pre-existing/unrelated, none in the deploy tests.

| # | Criterion | Result | Reason |
|---|---|---|---|
| 1 | sync.yml valid, no secrets in repo | PASS | Unchanged from first pass |
| 2 | Install docs, read-only token, all secrets named | **FAIL** | README lacks the three items the BO required with the AC6 approval (see below) |
| 3 | Triggers + refresh filter + concurrency | PASS | Unchanged |
| 4 | Restore, headless (+streams), export, persist | PASS | Unchanged |
| 5 | DB not in git history | PASS | Release asset, `coach/` on main |
| 6 | Rotated creds persisted, even on partial/fail | **FAIL** (docs only) | Mechanism approved (option A), but README still says "needs BO approval" and misstates passphrase-loss behaviour |
| 7 | status.md written on every run incl. failures | PASS | Fixed: `--exit-code ""` now rc 0 and writes "failed / crashed before status.json"; 2 new tests |
| 8 | Partial run commits, BO notified | PASS | Unchanged |
| 9 | refresh issue: comment+close / leave open | PASS | Unchanged |
| 10 | No secret in logs/status/commits | PASS | Unchanged |
| 11 | Free-tier estimate | PASS | ~250 of 2,000 min/month |
| 12 | Script tests (fixtures) | PASS | 35 tests |

## What is missing (BO comment, 2026-10-10 21:03Z: "make sure the install README covers")
1. How to create `TRAINIQ_STATE_PASSPHRASE`: long random string generated on the Mac and set with `gh secret set` without printing it. README only says "e.g. `openssl rand -base64 32`", no `gh secret set` instructions.
2. If the passphrase is lost: re-run `scripts/push_secrets_to_github.py` and delete the `creds.enc` release asset. README says the next run "falls back to whatever's in the secrets", with no recovery steps.
3. Once cloud sync is live the Mac must not run `trainiq` against Peloton concurrently (refresh-token rotation locks one side out). `grep -i mac` in README: no match.
4. Remove or update the "⚠️ needs BO approval" wording (README "Credential state" heading and section 5): approved 2026-10-10, option A.

Live-account verification (runner, token scopes, notifications) not done here.
