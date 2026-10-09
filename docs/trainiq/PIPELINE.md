# Pipeline: how work moves through this project

Work is done by automated roles, each a separate Claude Code cloud routine.
They hand off by relabeling GitHub issues (the only ticketing system for this
work; no Linear). Read this, then your role file in `docs/trainiq/roles/`.

## Roles and labels

| Stage | Label | Role file |
|---|---|---|
| 1 BA | `stage:ba` | `business-analyst.md` |
| 2 Architect | `stage:architect` | `technical-architect.md` |
| 3 Developer | `stage:dev` | `developer.md` |
| 4 QA | `stage:qa` | `qa.md` |
| Done | `stage:done` | (BO merges the PR) |
| Team Lead | PR merged, or `needs:routing` | `team-lead.md` |

An issue carries **exactly one** `stage:*` label: it is the single source of
truth for whose turn it is. Every issue also carries one `project:*` label.

## Other labels

- `needs:human`: blocked on the BO. Comment the exact decision needed.
- `needs:routing`: you can't tell who should own this next, or whether it needs
  the BO. Leave the stage label, comment what is unclear, add this, stop. The
  Team Lead re-routes or escalates to `needs:human`.
- `blocked:dependency`: waits on another PR that hasn't merged. See below.
- `priority:*`, `type:*`: informational for the BO; roles ignore them.

## Handoff (every role)

1. Do your role's work for the **triggering issue only**.
2. Commit what you produced (branch + PR if your role file says so).
3. Post **one** short comment: what you did, why, what the next role needs.
4. Remove your `stage:*` label, add the next one.
5. Stop. Never act on the next stage yourself.

If stuck: use `needs:human` or `needs:routing` as described above. Don't guess
requirements, designs, or the BO's answer.

## Dependencies between issues

Reading another open PR's code to design against it is fine. **Never branch
from, or merge, another open PR's branch** (it silently makes your PR
unmergeable). If you need code that isn't on `main` yet: keep your stage label,
add `blocked:dependency`, comment which PR/issue you wait on, stop. The Team
Lead removes the label and re-triggers your stage when that PR merges. BA and
Architect can proceed normally; note the dependency in the handoff so the
Developer checks merge status first.

## Routing escalations

Team Lead reads the stuck role's comment, then either re-routes (remove
`needs:routing`, set the correct single `stage:*`, comment why) or escalates
(`needs:human` with the specific decision). It never does the stuck role's work.

## Failure loop

QA failure: remove `stage:qa`, add `stage:dev`, comment which criteria failed
and how to reproduce. The Developer reworks from that comment and the PR diff.

## Ground rules

- Only touch this repo; the issue thread is the only channel to the BO.
- Never invent requirements or architecture decisions.
- Keep comments short and factual. Commit everything you produce.
- Compare against `origin/main` (run `git fetch origin main` first), never
  local `main`: it can be stale in the sandbox. If files you already read look
  missing, that is the symptom.
