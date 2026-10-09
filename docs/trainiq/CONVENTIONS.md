# TrainIQ conventions (shared by BA, Architect, Developer, QA)

Project invariants, stated once. Role files add only what is specific to the role.

**Connector layers.** Every data source is Authenticate → Download → Normalize → Checkpoint.
- Authenticate: OAuth, bearer token, session cookie or API key, via `CredentialStore`
  (`.get/.set/.rotate/.exists`, credential-type keys kept distinct). Every auth path needs a recovery path.
- Download: pagination loop; rate limits become `TransientError(retry_after_s=...)` from `Retry-After`; incremental via the `after` checkpoint.
- Normalize: map raw to canonical fields (`duration_s`, `distance_m`, ...). **Never fabricate:** a field the API doesn't return stays `None`. Derivable math (`duration_s = end - start`) is fine; estimates are not.
- Checkpoint: the cursor is stored as a **string**. `extract_resume_cursor()` must return `str(...)` even if the API gives an int (issue #12: int vs str crash on the second sync).

**Lifecycle (ADR-038).** Connected → Degraded (after `DEGRADED_THRESHOLD` consecutive failures) →
RecoveryRequired (after `DEGRADED_ESCALATION_THRESHOLD_DAYS` in Degraded). Recovery logic lives in the
connector, not the Sync Engine. Auth 401/403 escalates to RecoveryRequired and clears invalid credentials; 5xx backs off without escalating.

**Evidence-based.** Live-captured payloads in `docs/trainiq/verification/` are ground truth. "The API
docs say X" without live proof is a gap to flag, not something to build on. Exact record shapes the BO
supplies go straight into acceptance criteria and fixtures.

**CI has no live accounts.** Everything must be testable with fixtures and mocked clients (no real
Peloton/Eufy/Strava calls). Live-account testing is the BO's job; the BO may commit captures to
`docs/trainiq/verification/`.

**Never** store credentials in code or commit history; never script a login form.
