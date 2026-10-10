# Architecture: Correct ADR-007 and BACKLOG.md BL-001's Strava hostname claim

Issue: #29
Requirements: [`docs/trainiq/requirements/29-adr-007-correct-strava-host.md`](../requirements/29-adr-007-correct-strava-host.md)
Related: #26 (closed, connector host fix), PR #28 (merged, `a2c0662`), #30/#31 (separate, unrelated follow-on work on the unofficial connector's endpoints)

## Who implements a docs-only correction (open question 2, settled here)

The requirements doc flags, as a non-blocking open question, that on #26 the
Architect, Developer, and QA each declined to touch `docs/trainiq/adr/*` /
`projects/trainiq/BACKLOG.md`, citing their role's mandated work location,
and asks who should own ADR edits going forward.

No pipeline/role-configuration change is needed to resolve this, and this
routine has no authority to make one regardless (role work locations are
pipeline configuration, same category as the locked routine settings — see
`CLAUDE.md`). The declines on #26 were correct **for #26**: that issue's own
scope was a one-constant code fix in `trainiq/connectors/strava_unofficial.py`,
so editing the ADR there really would have been scope creep beyond that
issue's acceptance criteria, for every role. It doesn't follow that ADR/
BACKLOG edits are permanently off-limits to the Developer (or any role) in
general — a role's mandated work location is the set of files the *current
issue's* requirements/architecture doc scopes it to, not a fixed, issue-
independent allowlist. **This issue's entire scope, per its requirements
doc, is `docs/trainiq/adr/ADR-007-no-hardcoded-endpoints.md` and
`projects/trainiq/BACKLOG.md`** — so implementing #29 *is* editing exactly
those two files, for whichever role implements it. No separate approval is
needed for the Developer to do so here; declining again on this issue, for
the same reason that correctly applied to #26, would leave this issue
permanently unfixable, which is exactly the outcome #26's QA opened this
issue to prevent.

This design doc stays inside this routine's own mandated work location
(`docs/trainiq/architecture/*` only, per this routine's instructions) and
does not make the edits itself — it specifies them exactly, for the
Developer to apply.

## Independent verification performed before designing

Per this role's Evidence-based principle, I checked both halves of the
original claim independently rather than designing against the issue body
alone:

**"Current hostname" half — already settled by #26, re-confirmed here, not
re-litigated:** `api.strava.com` has no DNS record (per #26's independent
`dig`/`getent hosts` findings from the BO and the prior Architect, on
unrestricted networks). `https://www.strava.com/api/v3` is Strava's real,
reachable current API base (confirmed by `curl` returning 401, not a
DNS/connection failure).

**"Migration" half — the actual open question from the requirements doc,
investigated here:** this sandbox's egress policy blocks direct requests to
`developers.strava.com`, `communityhub.strava.com`, and
`stravalib.readthedocs.io` (confirmed directly — each attempt returned
`EGRESS_BLOCKED` from this environment's proxy, the same category of
restriction already documented in `strava.py`/`strava_unofficial.py`'s
module comments, not a new finding). Web search (which is not subject to
this sandbox's own egress block, since it runs server-side) returned
consistent, repeated results across two independent queries:

- Strava's own developer changelog (`developers.strava.com/docs/changelog/`)
  and community-hub post "An update to our developer program" state the API
  base URL is changing from `https://www.strava.com/api/v3` — **the current,
  correct host, not `api.strava.com`** — to `https://api-v3.strava.com`,
  with the new host available starting **2027-01-04** and a final cutover
  deadline of **2027-06-01**.
- `stravalib`'s own `protocol.ApiV3` class hardcodes `server = "www.strava.com"`
  and `api_base = "/api/v3"` — independently corroborating that
  `www.strava.com/api/v3`, not `api.strava.com`, is the host `stravalib`
  itself targets today.

**Conclusion:** the migration claim is real and has a genuine, citable
source — ADR-007's error was in naming the wrong *current* host as the
migration's starting point, not in inventing the migration itself. AC2 is
satisfiable by correcting the "from" host and citing the source, not by
removing the claim. Per this role's Evidence-based principle, I'm flagging
plainly that this was verified via web search, not a direct fetch of the
primary source (blocked by this sandbox, same as #26's HTTP checks) — the
BO should spot-check the two URLs below next time they have unrestricted
network access, the same residual caveat #26 left for its own findings.

Sources found (for the BO to spot-check, not fetched directly from this
sandbox):
- `https://developers.strava.com/docs/changelog/`
- `https://communityhub.strava.com/insider-journal-9/an-update-to-our-developer-program-13428`

## Approach

Correct both documents' factual claim about which hostname is Strava's
current, valid one, while preserving the parts of each that remain true:
ADR-007's actual architectural decision (no hardcoded endpoints) and BL-001's
actual action item (watch `stravalib` ahead of the real migration) are both
unaffected by the hostname correction and are not touched beyond restating
them against the corrected fact.

I'm specifying the exact replacement text below rather than describing the
change in prose, consistent with this role's job of leaving the Developer no
significant undocumented decisions — this is a factual correction, not a
design choice, so there's nothing for the Developer to decide, only text to
apply verbatim.

## Affected components/files

- **Changed:** `docs/trainiq/adr/ADR-007-no-hardcoded-endpoints.md` — the
  `Origin` line and `Context` section only. `Decision` and `Consequences`
  are unchanged (verified below that neither depends on the wrong claim).
- **Changed:** `projects/trainiq/BACKLOG.md` — the `BL-001` entry only. No
  other entry in this file mentions `api.strava.com` (verified by grep
  across the whole file).
- **Not changed, verified by direct grep of this repo's current `main`
  (not GitHub's code-search index, which can lag on recent pushes) for
  `api.strava.com` project-wide:**
  - `projects/trainiq/trainiq/connectors/strava_unofficial.py` (lines 122,
    365) and `projects/trainiq/trainiq/connectors/strava.py` (lines 51,
    105): all four hits are the existing "this sandbox cannot reach
    api.strava.com" / "api.strava.com is not in this environment's allowed
    egress list" statements. These describe this pipeline sandbox's own
    network-egress restriction, not a claim about which hostname is
    Strava's real one — explicitly out of scope per the requirements doc's
    "Out of scope" section, and still accurate regardless of this
    correction (the sandbox's egress policy doesn't care which hostname is
    correct).
  - `projects/trainiq/tests/test_strava_connector.py` (line 5): same
    pattern, references the module docstring above.
  - **No other file under `projects/trainiq/` contains `api.strava.com`.**
    AC4 is already satisfied with zero code/test/comment changes — this
    item exists in the requirements doc "to guard against one being missed,
    not because a known instance has been found," and none was found.
  - Historical docs listed in the requirements doc's "Out of scope"
    (`docs/trainiq/requirements/18-*`, `20-*`, `26-*`,
    `docs/trainiq/discovery/18-*`, `20-*`,
    `docs/trainiq/architecture/18-*`, `22-*`, `26-*`): left untouched, per
    requirements doc.

## Interfaces/contracts

Not applicable — no code, schema, or function signature changes. The
"interface" here is exact replacement text for two Markdown files.

### `docs/trainiq/adr/ADR-007-no-hardcoded-endpoints.md`

Replace the header block (`Status`/`Origin` lines) and the `## Context`
section with:

```markdown
**Status:** Accepted (Phase 0, Milestone 1 — Strava Engineering Feasibility)
**Origin:** Strava's announced API base-URL migration — `https://www.strava.com/api/v3` → `https://api-v3.strava.com`, new host available 2027-01-04, final cutover deadline 2027-06-01 (Strava developer changelog / community-hub "An update to our developer program"; corrected 2026-10, see #26/#29).

## Context

A confirmed future breaking change to Strava's base URL provided concrete evidence that remote endpoint values change over the life of a long-running project, not just hypothetically.

**Correction (2026-10, #29):** this ADR originally named `api.strava.com` as Strava's current, valid API hostname, migrating to `api-v3.strava.com`. That was wrong about the *current* host: `api.strava.com` has no DNS record at all and has never been a reachable Strava host (confirmed independently by the BO and the Architect on #26, via `dig`/`getent hosts` from unrestricted networks — not a sandbox-egress artifact). Strava's actual, current API base is `https://www.strava.com/api/v3` (confirmed reachable: `curl` against it returns 401, i.e. auth-required, not a DNS or connection failure; `stravalib`'s own `ApiV3.server` attribute independently targets the same host). The migration itself is real and Strava-documented — it simply migrates *from* `www.strava.com/api/v3`, not from the fictional `api.strava.com`; see the corrected `Origin` line above and its source.
```

Leave `## Decision` and `## Consequences` exactly as they are today — neither
references `api.strava.com` and both remain correct as written (verified
above: the "no hardcoded endpoints" decision and its consequences don't
depend on which specific hostname is correct).

### `projects/trainiq/BACKLOG.md` — `BL-001` entry

Replace the entire `BL-001` entry with:

```markdown
### BL-001 — Review `stravalib` releases before January 2027
**Raised:** Epic 1 self-review, carried forward from Milestone 1 (R-STRAVA-02).
**Corrected (2026-10, #29):** this item originally stated Strava's API base URL migrates from `api.strava.com` to `api-v3.strava.com`. `api.strava.com` has no DNS record and was never a valid Strava host (confirmed on #26). The real migration, per Strava's own developer changelog / community-hub announcement, is from `https://www.strava.com/api/v3` — the actual current host, also independently confirmed as `stravalib`'s own hardcoded `ApiV3.server` value — to `https://api-v3.strava.com`, available 2027-01-04, final deadline 2027-06-01. See ADR-007 for the same correction. The naming error doesn't change the underlying action item: `stravalib` still owns this host internally, and TrainIQ still depends on the maintainers migrating it before the deadline.
**Why it's here, not just in a comment:** Strava's API base URL migrates from `www.strava.com/api/v3` to `api-v3.strava.com` on **January 4, 2027** (final cutover deadline June 1, 2027). `stravalib` v2.5.0 hardcodes the current URL internally (`ApiV3.server = "www.strava.com"`), not exposed as a constructor override. TrainIQ's own code never hardcodes it (ADR-007 is satisfied at our layer), but the actual mitigation depends entirely on the `stravalib` maintainers updating their library before that date.
**Action required:** check `stravalib`'s changelog/releases in Q4 2026; if no fix has shipped by then, this becomes a real blocker requiring either a patched fork, a monkey-patch of `ApiV3.server`, or a move away from `stravalib` entirely for the base-URL portion of the client.
**Status:** Open, not urgent for the base-URL migration itself (roughly three months of runway as of this writing, 2026-10) but still required before RC1 per the Product Owner's live-verification priority. **Verification protocol:** `docs/verification/PROTOCOL.md`.
```

(Only change from the original: the corrected hostnames throughout, the new
"Corrected (2026-10, #29)" line, and the runway figure updated from the
original "six months" — stale relative to today's date — to the actual
~3 months between 2026-10 and the 2027-01-04 migration-availability date.)

## Task breakdown

1. In `docs/trainiq/adr/ADR-007-no-hardcoded-endpoints.md`, replace the
   `Status`/`Origin` header lines and the entire `## Context` section with
   the block specified above. Leave `## Decision` and `## Consequences`
   byte-for-byte unchanged.
2. In `projects/trainiq/BACKLOG.md`, replace the entire `BL-001` entry
   (from `### BL-001 — Review \`stravalib\` releases before January 2027`
   up to, but not including, the next `###` heading) with the block
   specified above. No other entry in the file changes.
3. Re-run the grep from "Affected components/files" above
   (`grep -rn "api.strava.com" --include=*.py projects/trainiq/`) after
   these two edits to confirm it still only matches the four pre-existing
   sandbox-egress comments/docstrings — this confirms AC4 without requiring
   any code change.
4. No tests to run or add — this issue touches no code, per requirements
   doc AC5. If any test happens to assert against ADR-007/BACKLOG.md text
   (none does today, per inspection), re-check it; otherwise the existing
   test suite is unaffected and does not need to be re-run for this change.

## Test strategy notes

Not applicable in the usual sense — this is a documentation-only change
(requirements doc AC5). QA's verification is a direct read-through of the
two corrected documents against the requirements doc's acceptance criteria
(AC1–AC4, AC6), plus re-running the same grep from "Task breakdown" step 3
to independently confirm AC4, rather than any automated test.

## Risks/tradeoffs

- **The migration-source verification relies on web search, not a direct
  fetch of Strava's own page**, because this sandbox's egress policy blocks
  both `developers.strava.com` and `communityhub.strava.com` directly
  (confirmed by attempting it, not assumed). This is the same category of
  limitation #26 already operated under for HTTP checks (DNS was checkable
  directly; HTTP wasn't). The two source URLs are named explicitly in this
  doc's "Independent verification" section for the BO to spot-check when
  next on an unrestricted network — low risk either way, since AC2 is
  satisfied by "a real, checkable source" existing and being cited, which
  it now is, independent of whether this routine could fetch it directly.
- **Scope boundary for future ADR/BACKLOG corrections:** this doc resolves
  the "who implements this" question for *this* issue only (see the section
  above) by pointing out that the issue's own scope already licenses it —
  it deliberately does not attempt to set a standing rule for every future
  issue that might touch `docs/trainiq/adr/*` or `BACKLOG.md`, since that
  would be a pipeline-configuration decision outside this routine's
  authority (per `CLAUDE.md`'s routine-configuration lock) and the
  requirements doc explicitly reserved it as a judgment call, not a mandate
  to change role configuration.
- **No behavioral change of any kind** — this is strictly Markdown text in
  two files; `Decision`/`Consequences` in ADR-007 and the action item in
  BL-001 are preserved, not reinterpreted, per requirements doc AC1–AC3 and
  "Out of scope."
