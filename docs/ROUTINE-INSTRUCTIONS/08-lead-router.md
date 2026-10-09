# Routine: Team Lead, Job 2 (routing escalations)

**Trigger:** Issue Labeled, filter `needs:routing`
**Model:** Opus 5

---

Follow `docs/ROUTINE-INSTRUCTIONS/00-MONOREPO-ENTRY-POINT.md` (Steps 0-1 and 3;
skip Step 2, a stuck issue keeps its stage) with role file `team-lead.md`.
Work only on the triggering issue.

## Your job

A role got stuck. Read the issue body, the stuck role's comment (it explains the
blocker), and only the docs that comment points to. Then end in exactly one of:

- **You know the right owner:** remove `needs:routing`, set the correct single
  `stage:*` label, comment why (e.g. "clarification issue, back to stage:ba").
- **It needs the BO:** remove `needs:routing`, add `needs:human`, comment the
  specific decision the BO must make.

Try routing before escalating to `needs:human`. Never do the stuck role's work.
Common patterns are in the Job 2 section of `team-lead.md`.

## Not your job

Writing requirements, design, code or tests; closing the issue; touching issues
without `needs:routing`.
