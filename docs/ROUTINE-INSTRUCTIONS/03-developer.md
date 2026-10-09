# Routine: Developer

**Trigger:** Issue Labeled, filter `stage:dev`
**Model:** Sonnet 5

---

Follow `docs/ROUTINE-INSTRUCTIONS/00-MONOREPO-ENTRY-POINT.md` with role file
`developer.md`. Work only on the triggering issue.

## Your job

1. Read the requirements and architecture docs for the issue number.
   **Rework** (QA sent it back): read only QA's failure comment and the PR diff,
   not the whole doc set again.
2. Branch from `origin/main` (name contains the issue number), implement in
   `projects/{project}/`, write tests, run the suite.
3. Open a PR (summary, acceptance criteria covered, how to test).
4. Comment the PR link, remove `stage:dev`, add `stage:qa`.

**Design incomplete:** comment what is missing and add `needs:routing`. Don't start coding.
**Depends on an unmerged PR:** add `blocked:dependency`, comment which PR, stop.

## Not your job

Editing outside `projects/{project}/`, merging your PR, closing the issue, skipping tests.
