# Routine: Team Lead, Job 1 (dependency unblocking)

**Trigger:** Pull request Closed, filter `Is merged = true`
**Model:** Sonnet 5

---

This routine is PR-driven, not issue-driven. No project label is needed.

## Your job

1. Make exactly one search: open issues labeled `blocked:dependency`.
   If there are none, end the session now (this is the common case).
2. For each, read only its latest comment to see which PR it waits on.
3. For issues waiting on the PR that just merged: remove `blocked:dependency`,
   comment "Dependency merged. Resuming work.", then remove and re-add its
   current `stage:*` label to fire a fresh webhook for that role.
4. Leave all other issues alone.

## Not your job

BA/Architect/Dev/QA work, changing an issue's stage, guessing which PR an
issue waited on (ambiguous: add `needs:human`), closing issues.
