# Routine setup guide

Applies the instruction files in this folder to the Claude Code routines UI.
Trigger, filter, connector and model settings live only in the UI; changes need BO approval (CLAUDE.md Rule 1).

## Routines (6)

| Routine | Instruction file | Trigger | Filter | Model |
|---|---|---|---|---|
| ba | `01-ba.md` | Issue: Labeled | `stage:ba` | Sonnet 5.5 |
| architect | `02-architect.md` | Issue: Labeled | `stage:architect` | Sonnet 5.5 |
| developer | `03-developer.md` | Issue: Labeled | `stage:dev` | Sonnet 5.5 |
| qa | `05-qa.md` | Issue: Labeled | `stage:qa` | Sonnet 5.5 |
| lead | `07-lead.md` | Pull request: Closed | `Is merged = true` | Sonnet 5.5 |
| lead-router | `08-lead-router.md` | Issue: Labeled | `needs:routing` | Opus 5.5 |

All use only the Composio connector. The table is the target; the current UI values
(BA unfiltered on Opened+Labeled, models Sonnet 5 / Opus 5, plus the retired
`developer-lite` and `qa-lite`) are listed in `docs/ROUTINE-OPTIMIZATION.md`.

## Applying a change

1. Open the routine in the Claude Code UI and paste the matching instruction file.
2. Change trigger, filter or model only if the BO approved that specific change.
3. Test with a throwaway issue labeled `project:agent-pipeline` + `stage:ba` and
   confirm: BA runs once, each later stage runs once, no other routine fires.
4. Rollback: paste the previous instruction (git history) or clear the field,
   comment on the failing issue with `needs:human`.

## Smoke checks

- Issue without `project:*`: first routine adds `needs:human` and stops.
- Issue with two `stage:*` labels: stages removed, `needs:human`, stop.
- Applying a non-trigger label (e.g. `bug`, `priority:high`) must start no routine run.
