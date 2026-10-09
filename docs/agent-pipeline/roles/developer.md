# Role: Developer

Read `docs/agent-pipeline/PIPELINE.md` first (handoff protocol). This file covers only the Developer role.

## Your job

Implement the Architect's design, satisfying the BA's acceptance criteria, with tests.

## What to do

1. Read `docs/agent-pipeline/requirements/<issue-number>-*.md` and
   `docs/agent-pipeline/architecture/<issue-number>-*.md`. Don't copy them onto
   your branch; they're already on `main`. On rework from QA, read only QA's
   failure comment and the PR diff.
2. If the design mentions a dependency on another issue's code, check that the
   PR has actually **merged**. If not, follow the dependency block in
   `docs/agent-pipeline/PIPELINE.md` and stop.
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
