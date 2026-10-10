# Monorepo guardrails

Monorepo for several projects (`agent-pipeline`, `trainiq`, future ones). Work flows through
project-agnostic routines that read the issue's `project:*` label and load `docs/{project}/`.
Routines load this file on every run, so it is kept short.

## Rules

1. **Routine settings are locked.** Never change triggers, filters, models, connectors or
   routine settings without explicit BO approval: ask, wait, change only what was approved,
   test, then update this file. (Instruction text in `docs/ROUTINE-INSTRUCTIONS/` and docs
   under `docs/{project}/` may evolve.) The settings live only in the Claude Code UI, so
   mistakes are invisible in git and can stall the whole pipeline.
2. **Every new issue starts with `project:*` and `stage:ba`.** `stage:ba` triggers the BA routine.
3. **Exactly one `stage:*` label per issue.** Each role removes its stage before adding the
   next (`ba` → `architect` → `dev` → `qa` → `done`). Several stages would fire several
   routines at once (merge conflicts, duplicate work). If a routine sees several or none:
   remove all `stage:*`, add `needs:human`, comment, stop.

## Routines (7)

| Routine | Trigger | Filter | Model |
|---|---|---|---|
| ba | Issue: Labeled | `stage:ba` | Sonnet 5.5 |
| architect | Issue: Labeled | `stage:architect` | Sonnet 5.5 |
| developer | Issue: Labeled | `stage:dev` | Sonnet 5.5 |
| qa | Issue: Labeled | `stage:qa` | Sonnet 5.5 |
| lead | Pull request: Closed | `Is merged = true` | Sonnet 5.5 |
| lead-router | Issue: Labeled | `needs:routing` | Opus 5.5 |
| pipeline-watchdog | Schedule: hourly (`7 * * * *`) | (none) | Haiku 4.5 |

Only the Composio connector. `pipeline-watchdog` (added 2026-10-10, BO-approved)
restarts issues stalled 45+ min on a `stage:*` label once, then adds `needs:human`, and
unblocks `blocked:dependency` after the referenced PR merges. Each routine reads its instruction file in
`docs/ROUTINE-INSTRUCTIONS/` (entry point: `00-MONOREPO-ENTRY-POINT.md`).

## Ringfencing

- Every issue has exactly one `project:*` label; otherwise `needs:human`.
- Edit only `projects/{project}/` and `docs/{project}/` for issues of that project.
- Never edit another project, `docs/SHARED-PIPELINE.md` (read-only), or `shared/` without BO approval.
- No cross-project changes in one PR; use `blocked:dependency` for cross-project dependencies.
- New project: copy `docs/trainiq/` to `docs/{name}/` (PIPELINE.md, CONVENTIONS.md, roles/),
  create `projects/{name}/`, label issues `project:{name}`. No routine changes needed.

## Labels

- `project:*`, `stage:ba|architect|dev|qa|done`
- `needs:human` (BO), `needs:routing` (lead-router), `blocked:dependency`
- `priority:*`, `type:*` are informational only; roles ignore them.
