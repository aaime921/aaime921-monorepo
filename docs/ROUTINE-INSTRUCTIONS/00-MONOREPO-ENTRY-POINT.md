# Monorepo Entry Point Protocol

Every routine runs this first. Be brief: each step that fails ends the session.

## Step 0: Event gate (before reading anything else)

The webhook payload names the issue (or PR) and the label that was just applied.
Work **only on that issue**. Never list or search other issues to find work.

If the applied label is not your trigger (see your routine file), end the
session immediately: no file reads, no comments, no label changes.

## Step 1: Project label

The issue needs exactly one `project:*` label. If not, add `needs:human`,
comment "Please add exactly one project: label (project:agent-pipeline,
project:trainiq, ...)", and stop. Otherwise `{project}` = the label suffix.

## Step 2: Single stage label (CLAUDE.md Rule 3)

The issue needs exactly one `stage:*` label. If it has several or none, remove
all `stage:*` labels, add `needs:human`, comment "Multiple or missing stage
labels; all removed. BO must set the correct single stage.", and stop.

## Step 3: Load only what your role needs

Read, in this order, and nothing else until needed:

1. `docs/{project}/PIPELINE.md`
2. `docs/{project}/roles/{your-role}.md`
3. `docs/{project}/CONVENTIONS.md` (only if your role file says so)
4. The issue body, then comments only since the last stage label change

If `docs/{project}/` is missing, add `needs:human`, comment, and stop.

## Step 4: Stay inside the project

- Edit only `projects/{project}/` and `docs/{project}/`.
- Docs-only commits may go straight to `main`; code goes on a branch with a PR
  whose branch name contains the issue number.
- Never edit another project, `docs/SHARED-PIPELINE.md`, or `shared/` without BO
  approval. Cross-project dependency: add `blocked:dependency`, don't work around it.

## Step 5: One handoff, then stop

Follow the handoff in `docs/{project}/PIPELINE.md`: one comment, swap the stage
label, stop. Do not start the next stage.
