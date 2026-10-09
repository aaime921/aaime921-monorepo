# Role: Developer

Read `docs/trainiq/PIPELINE.md` first (handoff protocol). This file covers only the Developer role.

Also read `docs/trainiq/CONVENTIONS.md` (project invariants), then the TrainIQ section at the end of this file.

## Your job

Implement the Architect's design, satisfying the BA's acceptance criteria, with tests.

## What to do

1. Read `docs/trainiq/requirements/<issue-number>-*.md` and
   `docs/trainiq/architecture/<issue-number>-*.md`. Don't copy them onto
   your branch; they're already on `main`. On rework from QA, read only QA's
   failure comment and the PR diff.
2. If the design mentions a dependency on another issue's code, check that the
   PR has actually **merged**. If not, follow the dependency block in
   `docs/trainiq/PIPELINE.md` and stop.
3. Branch off `origin/main`, never local `main` (it can be stale in the
   sandbox): `git fetch origin main && git checkout -b <branch> origin/main`.
   Include the issue number in the branch name. If files you just read look
   missing, `git fetch origin main && git reset --hard origin/main`.
4. Implement the design's task breakdown. Explain any deviation in the handoff.
5. Write tests for the acceptance criteria; run the whole suite.
6. Open a PR against `main` referencing the issue (`Closes #N` is fine but
   QA still signs off) listing what was implemented. Push.

Design missing something non-trivial: comment the gap, add `needs:human`, stop.
If you can't tell whether it needs the BO or another stage, add `needs:routing`.

## What NOT to do

- Don't merge your own PR or mark acceptance criteria met (QA's call).
- Don't skip tests because "it's simple".

## Handoff

Comment with a summary, PR link, and how you tested. Remove `stage:dev`, add `stage:qa`.

## TrainIQ-specific

- Code lives in `projects/trainiq/trainiq/` (connectors, credentials, sync, storage, normalization), tests in `projects/trainiq/tests/`.
- Credentials only through `CredentialStore` (`.set/.rotate/.get/.exists`; OS keyring behind it).
- Connector recovery (e.g. `submit_manual_recovery()` for bearer tokens) belongs in the connector and transitions back to Connected on success; failure escalates to RecoveryRequired.
- Tests: fixtures from real captures (`docs/trainiq/verification/`), mocked API clients, multi-page pagination, string cursor across two syncs, and separate 429 / 401-403 / 5xx cases. No live-account tests in CI.
- DB changes: follow the existing approach in `projects/trainiq/trainiq/storage/schema.py`; handle NULL or backfill for existing rows and test both a fresh DB and an upgrade of existing data.
- Never fabricate data in `normalize()`; never assume cursor type, always coerce to string.
