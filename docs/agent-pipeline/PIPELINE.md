# Pipeline: how work moves through this repo

This repo is worked by automated roles, each a separate Claude Code cloud
routine. They hand work to each other by relabeling GitHub issues — never by
a human copying/pasting between them. Read this file first, then your
role-specific file in `docs/roles/`.

## Ticketing system

**GitHub Issues on this repo is the ticketing system for this pipeline —
full stop.** No external tracker (e.g. Linear) is used for work that
executes here; using one alongside GitHub issues would just create a second,
unsynchronized source of truth for something the pipeline already tracks via
labels. If a human operator's other projects use a different tracker, that's
unrelated — this repo's issues are authoritative for its own work.

## Ticket categories

Every issue carries exactly one `type:*` label, set when the issue is filed:

- `type:project` — feature/bug work that flows through the automated
  pipeline below (Triage → BA → Architect → Dev → QA → Done). This is the
  default: an issue with no `type:*` label is treated as `type:project`.
- `type:tooling` — a change to the pipeline's own infrastructure (routine
  models/prompts, webhook wiring, this file, `docs/roles/*.md`). The
  automated roles can't do this work — they only have repo-checkout access,
  not access to reconfigure their own cloud routines — so Triage and BA both
  skip `type:tooling` issues entirely (Triage adds `needs:human` and stops;
  see `docs/roles/triage.md`). These are worked directly by whoever has
  routine-admin access, and closed manually when done.

## Roles and stage labels

| Order | Role                | Label            | Doc                                |
|-------|----------------------|-------------------|-------------------------------------|
| 0     | Triage               | (unlabeled issue) | `docs/roles/triage.md`              |
| 1     | Business Analyst     | `stage:ba`        | `docs/roles/business-analyst.md`    |
| 2     | Technical Architect   | `stage:architect` | `docs/roles/technical-architect.md` |
| 3     | Developer             | `stage:dev`       | `docs/roles/developer.md`           |
| 4     | QA                    | `stage:qa`        | `docs/roles/qa.md`                  |
| —     | Done                  | `stage:done`      | (issue closed)                      |
| —     | Team Lead             | `needs:routing`, or PR-merge event | `docs/roles/team-lead.md` |

An issue carries **exactly one** `stage:*` label at a time. That label is the
single source of truth for whose turn it is. The Team Lead is different from
the other roles: it doesn't own a stage. It has two jobs, run as two separate
routines: reacting to PR merges to unblock issues stuck on a cross-issue
dependency (see "Cross-issue dependencies" below), and acting as the
pipeline's fallback dispatcher whenever another role can't work out the next
owner itself (see "Routing escalations" below). Triage is also different: it
never sets a `stage:*` label itself — it only tags complexity (and, for
tooling issues, `needs:human`) on brand-new issues before BA picks them up
(see below).

## Modifier labels

Unlike `stage:*`, these can coexist with a stage label — they don't replace it.

- `priority:high` / `priority:medium` / `priority:low` — optional, set at issue-filing
  time to indicate urgency. Each role processes its candidate issues in priority order:
  1. All `priority:high` issues (oldest first within this priority level)
  2. All `priority:medium` issues (oldest first within this priority level)
  3. All `priority:low` or no-priority issues (oldest first within this priority level)
  If no priority label is set, the issue is treated as `priority:low` for ordering purposes.
- `project:*` (e.g. `project:trainiq`, `project:agent-pipeline`) — optional, set at
  issue-filing time purely for human/cross-repo clarity when issues are mentioned in Slack,
  PRs, or notifications outside their home repo. **Not load-bearing for routing or agent logic.**
- `needs:human` — something is blocked and needs the BO. See below.
- `needs:routing` — the role handling this issue could not determine either
  (a) which stage/role should own it next, or (b) whether this genuinely
  needs the BO. Rather than guess, or default to `needs:human` for something
  that might just need re-routing, add `needs:routing` and stop — this is a
  job for the Team Lead, not the BO, as a first resort. See "Routing
  escalations" below.
- `blocked:dependency` — this issue's work depends on another PR that hasn't
  merged yet. The issue keeps its current `stage:*` label; every role's
  candidate search must **skip** any issue that also has `blocked:dependency`,
  even if it otherwise matches their stage. The Team Lead removes this label
  (and re-triggers the stage) once the referenced PR merges.
- `complexity:simple` / `complexity:complex` — set once by Triage on every new
  issue, and left alone by every other role. It exists purely to control
  **which model tier** handles the Developer and QA stages for this issue
  (see "Cost tiering" below) — it carries no other meaning and never gates
  whether a role picks up an issue the way `blocked:dependency` does.

## Cost tiering

Developer and QA each run as **two separate routines** on different models,
selected by the `complexity:*` label Triage applied:

- `complexity:simple` → the lite routine (cheaper model).
- `complexity:complex`, or **no `complexity:*` label at all** (older issues
  predating Triage, or anything Triage couldn't classify) → the full routine
  (fails safe to the more capable model rather than risk under-powering a
  hard issue).

**Verification note (found the hard way):** the issue-search tool's
multi-label filter is not reliable as an AND — it has been observed
returning an issue that matched only one of several requested labels, not
all of them. Never trust the filtered result list alone. After listing
candidates, **read each returned issue's actual `labels` field yourself**
and confirm it genuinely carries the label(s) your tier requires before
touching it — skip (don't comment on, don't act on) any issue the filter
returned that doesn't actually match on inspection. This matters most for
the lite/full split: acting on a stale or wrong filter result means the
wrong model tier does real work on an issue, or two tiers race on the same
one.

Both routines for a given stage otherwise follow the exact same role doc and
handoff protocol — the split is purely an infrastructure/cost decision, not a
difference in job. BA and Architect are not tiered: their output quality
gates everything downstream, so a bad requirements or design doc wastes far
more tokens later than tiering would save.

Team Lead is **also** two routines, but split by job rather than by
complexity: the PR-merge/dependency-unblock job is fixed, mechanical work
that doesn't vary with issue complexity, so it stays on the cheaper model;
the routing-escalation job (see "Routing escalations" below) is only invoked
when a full-capability role already got stuck, so it needs the more capable
model, not less — downgrading the one role responsible for resolving hard
cases would defeat the point of tiering everything else.

## Handoff protocol (every role follows this)

1. Find open issues labeled with your stage that you haven't already acted on
   (check for a comment from you since the label was last applied — if you've
   already commented since the most recent `stage:<you>` label event, the
   issue is waiting on someone else; skip it).
2. Do your role's work (see your role doc).
3. Commit any files you produced, on a branch/PR if your role doc says so.
4. Post **one** comment on the issue summarizing what you did and why,
   in plain language the next role can act on without reading your mind.
5. Remove your `stage:*` label and add the next role's `stage:*` label
   (or `stage:done` + close the issue, if you're QA and everything passed).
6. Stop. Do not act on the next stage yourself, even if you can see how —
   that's a different routine's job, and it will be triggered automatically
   by the label change.

If you get stuck or the request is unclear/infeasible, you have two options —
pick whichever actually fits, don't default to the same one out of habit:

- **You know this needs the BO's judgment** (a business decision, a genuinely
  ambiguous requirement, something only the BO can resolve): leave the
  `stage:*` label as-is, comment explaining the blocker, and add
  `needs:human`.
- **You don't know who should own this next, or you're not even sure this
  needs the BO** (the issue seems miscategorized, spans stages in a way the
  protocol doesn't cover, or you genuinely can't tell): leave the `stage:*`
  label as-is, comment explaining what's unclear, and add `needs:routing`
  instead. This is the Team Lead's job to sort out, not the BO's, as a first
  resort — see "Routing escalations" below.

Do not guess at either the next stage or the BO's answer.

## Cross-issue dependencies

Sometimes an issue's work genuinely depends on code from another issue's PR
that hasn't merged yet (e.g. issue #3 builds on the CLI issue #1 is still
adding). Reading that code from the other PR's branch is fine — Architects
do this routinely to design against real interfaces. **Never branch your own
work off another open PR's branch, though**, even to "borrow" its commits:
that entangles your PR's history with one that's still mid-review, and it
will silently make your PR unmergeable later even when the diff looks clean
locally (GitHub's merge-commit computation can fail on this in ways `git
merge --no-commit` locally won't catch). If you find yourself needing code
that isn't on `main` yet to actually implement or verify something:

1. Keep your current `stage:*` label — do not advance or revert it.
2. Add `blocked:dependency`.
3. Comment stating exactly which PR/issue you're waiting on.
4. Stop. The Team Lead will remove `blocked:dependency` and re-trigger this
   stage once that PR merges — you'll get a fresh run then.

BA and Architect can still do their normal work even when a dependency is
unmerged (writing requirements or reading another branch for design purposes
doesn't require merged code) — just say so clearly in your handoff comment
so the Developer knows to check merge status before implementing. The
Developer is the role most likely to actually need to invoke this block,
since it's the one creating branches and PRs.

## Routing escalations (Team Lead)

Any role — BA, Architect, Developer, QA, or Triage — that can't determine
the next owner of an issue, or can't tell whether it needs the BO at all,
adds `needs:routing` and stops (see "Handoff protocol" above). This is a job
for the Team Lead's routing routine (`docs/roles/team-lead.md`, "Routing
escalations" section), triggered by that label:

1. Team Lead reads the issue: its full history, comments, requirements/design
   docs, and current labels.
2. If Team Lead can work out the correct next stage: remove `needs:routing`,
   set the correct `stage:*` label (re-routing the issue, possibly to a
   different stage than where it got stuck), and comment explaining the
   reassignment and why.
3. If Team Lead **also** can't determine the right owner: remove
   `needs:routing`, add `needs:human` instead, and comment explaining why
   this genuinely needs the BO — don't leave an issue silently stuck on
   `needs:routing` forever.
4. Team Lead never does the stuck role's actual work (writing requirements,
   designs, code, or test verdicts) — only re-routing.

This is separate from the PR-merge/dependency-unblock job below, and runs as
a different routine (see "Cost tiering" above) — same role, two jobs, two
triggers.

## Failure loop

QA can send an issue backward: remove `stage:qa`, add `stage:dev`, and write a
comment describing exactly what failed and how to reproduce it. The Developer
picks it up like any other `stage:dev` issue.

## Starting new work

The BO (project owner) opens a new GitHub issue describing what they want,
using the "Feature request" template, and labels it `type:project` or
`type:tooling` (see "Ticket categories" above) — leaving it unlabeled is
treated as `type:project`. New issues start with no `stage:*` label. Triage
picks up any open, not-yet-classified issue first: for `type:tooling`
issues it adds `needs:human` and stops (see `docs/roles/triage.md`); for
everything else it adds a `complexity:*` label. Either way, this does not
block or delay BA, which independently picks up any open, unlabeled,
non-`type:tooling` issue as if it were labeled `stage:ba`, whether or not
Triage has run yet.

**Dedup note for BA specifically:** every other role's entry point is a
`stage:*` label, so "already commented since that label was applied" (see
"Handoff protocol" above) is a natural dedup check. BA's entry point is the
*absence* of a `stage:*` label, which has no equivalent natural checkpoint —
an issue BA escalated via `needs:human` or `needs:routing` stays unlabeled
(per protocol, "leave the stage:* label as-is"), so without an explicit
check BA would keep re-matching and re-processing it on every subsequent
label event anywhere in the repo. BA must therefore also skip any unlabeled
issue that already carries `needs:human` or `needs:routing` — those mean
"already escalated, waiting on someone else," not "untouched."

## Ground rules for all roles

- Only touch this repository. Never message the BO directly — the issue
  thread is the only channel.
- Never invent requirements or architecture decisions that weren't given to
  you — ask via a comment + `needs:human` label instead.
- Keep comments concise and factual, matching what actually happened.
- Everything you produce (docs, diagrams, code, tests) is committed to the
  repo — nothing lives only in your own run output.
- If you ever run `git checkout main` for any reason (not just Developer's
  branching, e.g. comparing against main to check whether a test failure is
  pre-existing): local `main` in this sandbox can be stale (pinned to clone
  time), even though the detached HEAD you started on is current. Use
  `origin/main` instead (`git fetch origin main` first) for any comparison
  or branch-off point. If files you already read now appear missing, that's
  the symptom — fetch and use `origin/main`, don't conclude they don't exist.
