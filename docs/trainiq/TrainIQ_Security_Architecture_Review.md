# TrainIQ — Security Architecture Review

**Scope, as specified:** security architecture, not cybersecurity in the penetration-testing sense — where tokens end up, what gets logged, credential lifecycle, permission model. Audit only. No code was changed to produce this document; every finding was verified against the actual repository and, where relevant, the installed third-party library source.

---

## Where tokens end up

**Confirmed correct:** every credential (Strava refresh/access tokens, Eufy email/password/access token, Peloton email/password/manual bearer token) flows through `CredentialStore`, which delegates exclusively to `keyring` — macOS Keychain. Verified by re-checking every `credential_store.set()`/`.rotate()`/`.get()` call site across all three connectors: none bypasses this path, none writes a secret to SQLite. `credentials_metadata` (SQLite) was specifically re-checked and contains only a `connected` boolean and a `last_refreshed_at` timestamp — confirmed by reading the actual `INSERT`/`UPDATE` statements in `trainiq/credentials/store.py`, not assumed from the schema alone.

## Logging of sensitive data — one confirmed-clean area, one genuine open question

**Confirmed clean:** no connector ever passes a credential variable directly to `diagnostic_logger()` or `summary_logger()`. Checked every logging call site in `strava.py`, `eufy.py`, and `peloton.py` individually. Login/authentication request bodies (which contain email/password) are never logged.

**Open question, not resolved here:** several exception-handling paths log the *stringified exception object* itself — for example, `strava.py`: `diagnostic_logger().warning(f"{PROVIDER}: refresh_access_token failed: {exc}")`, and similarly in `eufy.py`/`peloton.py`'s `_TransientHTTPCondition(str(exc))` wrapping raw `requests` exceptions. **Whether this can leak a credential depends entirely on what the underlying library puts into that string**, which this review checked as far as reasonably possible but could not fully rule out:
- `stravalib.exc.AuthError` is a bare `RuntimeError` subclass with no custom `__str__` (confirmed by reading the installed library source) — so its message content is whatever `stravalib`'s internal OAuth code passed in, which was not traced further in this review.
- `requests.exceptions.RequestException`'s default string representation can, in some versions and some failure modes, include the request URL. For a `GET` request this is unlikely to carry the credential (Eufy/Peloton send tokens via headers, not query strings, per their own connector code) — but this was not exhaustively verified against every `requests` failure mode.

**This is flagged as a genuine open item, not dismissed and not fixed.** Recommended verification (not performed here, per the audit-only scope): deliberately trigger an auth failure against a live account during the live-provider verification pass (already planned) and inspect the resulting diagnostic log file directly for anything resembling a token or password substring.

## SQLite

Confirmed (re-verified, not just cited from prior reviews): no table in the current schema (`raw_activities`, `normalized_activities`, `weigh_ins`, `connector_state`, `credentials_metadata`, `athlete_profile`, `sync_checkpoints`, `dedup_links`) contains a column intended to hold a secret. `athlete_profile` holds physiological facts (sex, DOB, resting/max HR, FTP) — personal but not a credential; worth noting as a privacy-adjacent (not security-adjacent) consideration if this database is ever backed up to a non-local location, which nothing in the current codebase does.

**No file-permission hardening found — classified as future hardening, not a pre-release concern, per Chief Architect review.** Grepped the entire codebase for `chmod`/`os.chmod`/explicit octal permissions — none exist. The SQLite database file, its backups, and the log files are all created with whatever the OS default umask produces, not explicitly restricted to the owning user. On a single-user Mac in a single-user account, the practical exposure is genuinely low — macOS's per-user home directory permissions already limit access, and there is no multi-tenant or shared-machine scenario in scope today. Worth revisiting explicitly if TrainIQ is ever used in a shared-machine or cloud-backup context, neither of which is planned; not a reason to delay Release Candidate.

## Keychain

Confirmed: one Keychain item per `(provider, credential_type)` pair — a Peloton auth failure can be resolved without touching Strava's or Eufy's stored credentials, and vice versa. This isolation is structurally correct, not just documented as intended.

**Cross-referenced, not re-litigated:** the code-signing-consistency dependency (R-ARCH-01, established during Epic 0) is a real security control, not just a UX-continuity concern — inconsistent signing across builds doesn't just cause re-prompts, it's the mechanism macOS uses to decide which application is allowed to retrieve a given Keychain item at all. Worth carrying this into release engineering's packaging checklist explicitly as a security requirement, not only a user-experience one.

## Recovery flow (Peloton manual bearer token)

Confirmed: `submit_manual_recovery()` stores the user-supplied token through the same `CredentialStore` path as every other credential — no separate, less-audited code path for the one credential a human manually copy-pastes. This matters specifically because a manually-entered value is exactly the kind of thing that might tempt a shortcut (e.g., holding it in a plain instance variable "just for this session") — confirmed that shortcut was not taken.

## Backup

`backup_database()` uses `shutil.copy2`, which preserves the source file's permission bits — meaning backups inherit whatever hardening (or lack thereof) the live database has, consistent either way, not a separate gap. Backup retention (keep last 5) was already verified functionally correct in the Epic 0 test suite; this review adds only the permission-inheritance observation above.

## Exceptions / stack traces

No exception type in the codebase currently captures or forwards a full HTTP response body — every error path uses `response.status_code` only, never `response.text` or `response.json()`, when constructing an error message. This was specifically checked because a provider's error response body could in principle echo back part of a request (a common API debugging pattern) and end up logged. **Confirmed this specific risk does not currently exist** — the one open question is the exception-stringification path already flagged above, which is different (library-internal message content, not a response body TrainIQ chose to log).

## Credential lifecycle

Confirmed: every token is treated as rotating (Constitution-level principle, ADR-derived) — `rotate()` and `set()` are functionally identical, and every successful auth/refresh path calls one of them with the newest value, never assuming a previously stored value remains valid. `delete()` correctly handles the "already absent" case without raising. No code path was found that reads a credential and later writes back a *stale* value (a classic rotation bug) — this was specifically checked against Strava's refresh flow, the one place Milestone 1 flagged this exact risk originally.

## Permission model

TrainIQ has no multi-user concept and no privilege separation — appropriate for its stated scope (single-user personal desktop tool) and not flagged as a gap, since building one would be scope the product was never meant to have. The relevant permission boundary is entirely the macOS user-account boundary itself, which is outside this codebase's control and inherited from the OS.

---

## Summary — ranked by what would actually matter if wrong

1. **Exception-stringification credential leakage into logs (Logging section) — genuinely unresolved, verify during live-provider testing.** This is the one finding in this review with a plausible path to real harm (a token sitting in a plaintext log file on disk) that this audit could not fully rule out from static inspection alone.
2. **File permission hardening (SQLite section) — reclassified as future hardening, not a pre-release concern.** Genuinely low practical risk on a single-user Mac today; revisit only if a shared-machine or cloud-backup scenario is ever actually planned.
3. Everything else in this review is either confirmed clean or a documented, low-severity, already-understood characteristic of a single-user personal tool.

**No code was changed in the production of this document.**
