# TrainIQ — Live Provider Verification Protocol

**Purpose:** a formal validation record for BL-001 (Strava), BL-006 (Eufy), BL-008 (Peloton) — not a "did it basically work" check. Each provider gets its own copy of this template, filled in against a real account. A completed copy is the evidence that closes its backlog item; an incomplete or failed section is itself valuable evidence of a real discrepancy between what the mocks assumed and what the provider actually does.

**Why this exists as a document, not just a mental checklist:** every mock in this test suite encodes an assumption about provider behavior — an endpoint shape, a status code, a header name — made without a live account to check against (see each connector module's own "HONEST LIMITATION" docstring). This protocol exists to make each of those assumptions individually falsifiable, not just "sync ran without crashing."

---

## How to use this

**Not all of this fits in one sitting, and that's expected, not a flaw.** Sections 1's token-refresh check (Strava, >6 hours), Section 5's checkpoint-resume check, and Section 6's ADR-038 backoff-cadence check (waiting out the 1-day retry interval) verify behavior that genuinely spans hours or days. Treat those as long-running verification tracked over multiple sessions, not items to rush through or skip because a single sitting wasn't long enough. Leave them unchecked with a timestamp/note on where you left off, rather than marking them done prematurely or leaving them silently blank.

1. Copy this file to `docs/verification/{provider}-{date}.md` (e.g. `docs/verification/strava-2026-07-10.md`).
2. Fill in every section — an unchecked box or a "not observed" is real information, not a gap to skip past.
3. Where observed behavior differs from what the code assumes, write it down under "Discrepancies," even if it didn't cause a visible failure — a difference that happens not to matter today may matter after a provider-side change.
4. When complete, update `BACKLOG.md`: close the corresponding item, referencing this file. If discrepancies were found, open new backlog items for them rather than silently patching the code — same governance model as every other finding in this project.

---

## 0. Environment

| Field | Value |
|---|---|
| Date | |
| macOS version | |
| Python version | |
| TrainIQ version / commit | |
| Provider account type (e.g. free/paid tier, if relevant) | |
| Network conditions (notable, e.g. VPN, corporate proxy) | |

## 1. Authentication Flow

- [ ] Initial connection (first-time auth) succeeds end to end.
- [ ] Credential is actually stored in macOS Keychain (verify via Keychain Access.app, not just "no error thrown").
- [ ] `credentials_metadata.connected` is `1` after a successful connection (query the SQLite file directly).
- [ ] **Strava only:** access token expires and is silently refreshed within a session lasting >6 hours, without user action. Verify the refresh token in Keychain actually changed (Milestone 1's rotation finding — the specific bug this project is most worried about recurring).
- [ ] **Eufy only:** confirm whether a login response includes a `refresh_token` field distinct from the access token, and if so, whether a separate refresh endpoint exists (Milestone 3's Open Question 1 — this was never confirmed against a live account).
- [ ] **Peloton only:** confirm current behavior of `POST /auth/login` for automated credentials — does it still return the documented 403 ("Access forbidden. Endpoint no longer accepting requests") for some or all attempts, or has this changed since Milestone 2's research? This is the single most important thing to verify for Peloton specifically.

**Observed auth flow (free text — describe exactly what happened, including anything that felt slow, unclear, or different from expected):**

## 2. First Sync (Historical Backfill)

- [ ] A full historical sync completes (or, for a large account, makes visible checkpointed progress across multiple runs — Strava specifically may take multiple days per Milestone 1's rate-limit finding).
- [ ] Record count in `raw_activities` for this provider matches what the provider's own app/website reports (spot-check at least 10 activities).
- [ ] `normalized_activities` (or `weigh_ins` for Eufy) is populated with a plausible `discipline` value for every activity — flag any that landed in `other` unexpectedly (a taxonomy gap, not necessarily a bug — see BACKLOG.md's taxonomy note).
- [ ] `source_confidence` values look plausible (spot-check: an activity you know had a paired HR monitor should score higher than one you know didn't).

**Observed backfill duration and behavior:**

## 3. Incremental Sync

- [ ] After the historical backfill, perform a new activity/weigh-in on the provider's own app.
- [ ] Run a TrainIQ sync. Confirm the new record appears without re-fetching the entire history (check `sync_checkpoints.last_cursor` advanced, and that the sync duration was short, not proportional to full history size).
- [ ] Confirm no duplicate row was created for any previously-synced activity.

## 4. Duplicate Handling (Strava + Peloton only, if the same workout was posted to both)

- [ ] If a Peloton ride was auto-posted to Strava, confirm TrainIQ's behavior — note that automated deduplication logic (BL-002-adjacent) may not exist yet at the time of this verification; if so, simply record whether both copies appear as expected, not as a failure.

## 5. Resume / Checkpoint Behavior

- [ ] Force-quit TrainIQ (or kill the process) mid-sync during a large backfill.
- [ ] Restart and re-sync. Confirm it resumes rather than restarting from scratch (compare elapsed time and the checkpoint value before/after).
- [ ] Confirm no duplicate or missing records resulted from the interruption.

## 6. Graceful Degradation / Lifecycle (primarily relevant to Peloton, per ADR-038)

- [ ] If authentication fails (e.g. deliberately supply a wrong password once), confirm the connector transitions to `Degraded` in `connector_state`, and that other providers still sync successfully in the same run.
- [ ] If feasible within the verification window: confirm a `Degraded` connector is *not* re-attempted on every subsequent run, but is re-attempted after the ADR-038 backoff interval (1 day) — this may require leaving the credential broken for 24+ hours to observe directly.

## 7. Provider-Specific Anomalies

Record anything not covered above: unexpected response fields, rate-limit headers actually observed (compare against Milestone 1/2/3's documented figures), any status code not currently handled by the connector's code, unicode/encoding oddities in activity names, timezone handling surprises, etc.

## 8. Comparison Against Implementation Assumptions

For each connector, the code's own docstrings state specific assumptions made without live verification. Explicitly confirm or refute each:

**Strava:**
- [ ] Base URL is still `www.strava.com/api/v3` (BL-001 — check whether `stravalib` has updated ahead of the Jan 2027 migration).
- [ ] `calories` is genuinely absent from the activity-list endpoint response (Milestone 1/Epic 1's documented limitation) — confirm this is still true, not fixed by a provider-side change.

**Eufy:**
- [ ] The `/v1/device/{id}/data` endpoint path and response shape match what `EufyConnector.download()` assumes for a REAL historical sync, especially pagination/date-range behavior on a large history (BL-006, narrowed by RC1-HF-003 — login and device discovery are now verified; the full sync payload shape is not).
- [x] ~~Confirm whether a "list devices" endpoint actually exists~~ — **Resolved by RC1-HF-003**: the login response itself returns the account's devices; no separate endpoint needed. `device_id` is now auto-discovered, not manually configured. If this protocol is being run and device discovery unexpectedly fails or returns something unexpected, treat that as a new discrepancy to record below, not as re-opening this specific question.

**Peloton:**
- [ ] `GET /api/me/workouts` is still the correct endpoint and response shape (BL-008 — never confirmed live).
- [ ] Confirm current status of the documented Oct 2025–Jan 2026 auth breakage — fully resolved, partially resolved, or unchanged.

## 9. Discrepancies Found

*(List every difference between observed and assumed behavior, even minor ones. For each: does it require a code change, a new backlog item, or just a documentation update?)*

## 10. Verdict

- [ ] BL-XXX can be closed as verified.
- [ ] BL-XXX remains open — see discrepancies above.
- [ ] New backlog item(s) opened: _____________
