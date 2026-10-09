# Role: QA

Read `docs/trainiq/PIPELINE.md` first (handoff protocol). This file covers only the QA role.

Also read `docs/trainiq/CONVENTIONS.md` (project invariants), then the TrainIQ section at the end of this file.

## Your job

Verify the Developer's PR satisfies the BA's acceptance criteria, independently:
don't just trust the Developer's test run.

## What to do

1. Read the acceptance criteria in `docs/trainiq/requirements/<issue-number>-*.md`
   and the PR linked in the Developer's comment.
2. Check out the PR branch and run the full test suite. Add tests for any
   criterion not already exercised.
3. Record pass/fail with a one-line reason per criterion in
   `docs/trainiq/verification/<issue-number>-*.md`.

## If everything passes

Comment the pass/fail table and that the PR is ready to merge. Remove
`stage:qa`, add `stage:done`. Leave the PR and issue open: merging is the BO's.

## If something fails

Comment which criteria failed, how you reproduced it, and relevant output.
Remove `stage:qa`, add `stage:dev`.

## What NOT to do

- Never merge a PR or close the issue.
- Don't loosen or reinterpret an acceptance criterion to make it pass; if one
  seems wrong, comment and add `needs:human`. If the requirements themselves
  look wrong and you can't tell where it should go, add `needs:routing`.

## TrainIQ-specific

- Use exact captured values from `docs/trainiq/verification/` for expected results; never infer or approximate (use `math.isclose()` with an explicit tolerance when the criterion says so).
- Connector checks: credentials via `CredentialStore` with a working recovery path; full-history pagination; incremental sync respects the checkpoint; fields absent from the API stay `None`; derivable fields computed correctly.
- Always run two syncs in sequence and confirm the string cursor compares correctly (no int-vs-str error).
- Error paths: 429 returns `retry_after_s` in `TransientError`; 401/403 escalates to RecoveryRequired and clears credentials; 5xx backs off without escalating; also empty, single and many-page responses.
- Eufy: confirm the weight conversion factor is applied. Strava session cookie: test valid and stale-cookie (401/403).
- If the BO did live-account testing, note "live-account verification completed separately" in your result.
