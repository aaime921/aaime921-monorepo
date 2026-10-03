# TrainIQ — Operational Readiness Review

**Scope, as specified:** not architecture, not security — operability. Can the system be run, recovered, and troubleshot, not just built and tested. Audit only. No code was changed to produce this document.

---

## The headline finding, stated before anything else: there is currently no operational tooling at all

`trainiq/app.py` — the only entry point that exists — boots the Foundation (logging, database, an empty Connector Framework) and exits. It registers zero connectors, exposes no UI, and offers no command for any of the operations below. **Every procedure in this document, as of today, requires someone comfortable with a SQLite client, Keychain Access.app, or a Python shell — not a supported product feature.** This is worth stating plainly rather than letting the sections below imply otherwise: this review describes what is *technically possible given the current architecture*, not what a non-technical user could actually do today. That gap is itself the primary finding of an operational readiness review, arguably more consequential than any single procedure's specifics.

This also means: everywhere below that says "the operator would," read that as "no supported way exists for the athlete themselves to do this yet" — a real, currently-true statement, not a hypothetical.

---

## 1. If SQLite corrupts, what does the user do?

**Today:** nothing built-in detects or repairs this. If `trainiq.db` is corrupted, the practical recovery is: delete the file, let TrainIQ recreate a fresh schema on next launch (`schema.migrate()` handles fresh-install cleanly), and let the Synchronization Engine perform a full historical backfill for every connector from scratch — since checkpoints live in the same file that just got deleted.

**One genuine positive, worth stating clearly:** credentials are NOT lost in this scenario. Keychain items are independent of the SQLite file — a corrupted database does not require re-authenticating with any provider, only re-syncing. This wasn't obviously true without checking; it's confirmed by the architecture (`CredentialStore` never touches SQLite for secret values) rather than assumed.

**Gap:** no corruption *detection* exists — a user would discover this only when sync starts behaving strangely or SQLite itself raises an error, with no proactive check (e.g. `PRAGMA integrity_check`) run anywhere in the codebase today.

## 2. How is a backup restored?

**Today:** `backup_database()` (Milestone 4 §5 / Epic 0) creates timestamped snapshots before every migration, keeping the last 5. **No corresponding `restore_database()` function exists.** Restoring today means manually copying a `.bak` file over the live `trainiq.db` — with no schema-version check, no confirmation step, and no code path that does this safely. A user restoring an old backup with an outdated schema version would trigger `migrate()` to run forward-migrations against it on next launch, which is expected to work (migrations are designed to be idempotent and additive) but has never been explicitly tested as a "restore an old backup, then migrate forward" scenario — only "migrate a live, current database" has direct test coverage.

## 3. How is new authentication forced?

**Today:** no explicit "force re-auth" trigger exists for any connector. Strava, Eufy, and Peloton's automated-login paths only re-authenticate when the cached token/session is actually expired (checked via `expires_at`). To force re-authentication before that expiry, an operator would need to manually delete the relevant Keychain item(s) via Keychain Access.app (searching for `trainiq.{provider}.{credential_type}`) so the connector finds nothing valid and attempts a fresh login on the next sync. Peloton's `submit_manual_recovery()` exists as a real, tested method (Epic 3) but has no UI or CLI hook — invoking it today requires writing a short Python script that imports `PelotonConnector` directly.

## 4. How is a checkpoint reset?

**Today:** requires directly editing the `sync_checkpoints` table (`DELETE FROM sync_checkpoints WHERE provider = ? AND strategy = ?`) via a SQLite client. No function, command, or safety confirmation exists in the codebase for this. Resetting a checkpoint without understanding the consequence would cause the next sync to re-fetch and re-normalize the provider's entire history — which, for Strava specifically, could take multiple days under single-player-mode rate limits (Milestone 1's own finding).

## 5. How is `normalized_activities` rebuilt from `raw_activities`?

**Technically possible, but no tool exists today.** This is exactly the scenario `raw_activities` was designed for (Milestone 4 §5: "kept so Normalization can be re-run against history without re-fetching") — and the underlying pieces (`build_canonical_record()`, each connector's `normalize()`, which is a pure function requiring no network call) are all individually capable of supporting this. But no script or command currently iterates `raw_activities` and replays normalization against it. Building one would be a small, well-scoped, low-risk utility — explicitly not built here, since this review's scope is audit only, but worth naming as a concrete, low-effort operational tool that would close a real gap between "the architecture supports this" and "an operator can actually do it."

## 6. What logs are needed for troubleshooting?

**This one is already in reasonably good shape, confirmed against the actual logging configuration:** `~/Library/Logs/TrainIQ/summary.log` (user-facing, concise) and `~/Library/Logs/TrainIQ/diagnostic.log` (verbose, includes every warning this review's Security companion discusses), both rotated weekly with 8-week retention. For any sync-related issue, `diagnostic.log` is the one that matters — it includes connector state transitions, retry/backoff decisions (ADR-037/038), and normalization warnings (unrecognized disciplines, malformed records).

**Gap worth naming directly: no documented guidance exists today telling a user (or a future support process) which log to look at for which symptom**, or how far back to check given the rotation window. This document is a first step toward that; a proper "how to read your own logs" guide is still future operator documentation, not something this review produces.

## 7. What should a bug report collect?

Proposed, not yet built into any tooling:

- TrainIQ version / commit hash.
- macOS version, Python version (if run from source rather than a packaged `.app`).
- `schema_version` (a single `SELECT` against the database).
- The relevant window of `diagnostic.log` — **with an explicit, mandatory reminder to check for anything resembling a token or credential before sharing it**, per this review's companion Security Architecture Review's still-open finding on exception-stringification logging. This is not a hypothetical caution — it's a direct consequence of a specific, named, unresolved question in that document, and a bug-report process built before that question is answered risks asking users to paste exactly the thing the Security Review flagged as unverified.
- `connector_state` table contents (non-secret — safe to share as-is).
- **Never** the contents of Keychain, and never the raw `athlete_profile` table without the user's explicit awareness that it contains personal physiological data (sex, DOB, HR, FTP) — not a security secret, but a privacy-relevant one worth the same "ask before sharing" courtesy.

---

## Summary — what actually needs to happen before this project is operationally mature, not just architecturally sound

1. **The headline finding stands as the priority: zero operational tooling exists today.** Every answer above describes a manual, technically-demanding workaround, not a supported feature — a direct tension with Epic 0's own founding goal of "almost zero technical knowledge from the final user," now visible in a part of the system that goal was never actually tested against (recovery/troubleshooting, as opposed to first-run sync).
2. **The one genuinely cheap, well-scoped tool worth building soon:** a `raw_activities` replay/rebuild utility (Section 5) — the architecture already supports it fully; only the utility itself is missing.
3. **The one cross-reference worth carrying into release engineering directly:** any future bug-report process or support documentation must account for the Security Review's still-open exception-logging question before asking users to share diagnostic logs.

**No code was changed in the production of this document.**
