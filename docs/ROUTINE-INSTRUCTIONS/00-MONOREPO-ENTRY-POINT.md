# Monorepo Entry Point Protocol

**All 8 routines must follow this protocol at startup.**

## Step 1: Detect Project Label

Read the GitHub issue and extract the project label:

```python
project_labels = [label for label in issue.labels if label.startswith('project:')]

if len(project_labels) != 1:
    # Missing or ambiguous project label
    add_label('needs:human')
    comment("""Please add exactly one project: label to this issue.
    
Valid labels: project:agent-pipeline, project:trainiq, project:{name}""")
    return  # STOP PROCESSING
    
project_name = project_labels[0].replace('project:', '')
```

## Step 1.5: Validate Single Stage Label (CRITICAL)

Check that exactly ONE `stage:*` label is active:

```python
stage_labels = [label for label in issue.labels if label.startswith('stage:')]

if len(stage_labels) != 1:
    # Multiple stages or no stage = ambiguous pipeline state
    remove_all_labels([l for l in issue.labels if l.startswith('stage:')])
    add_label('needs:human')
    comment("""⚠️ Multiple active stages detected. All stages removed.

Pipeline requires exactly ONE active stage label per issue:
- stage:ba (Business Analyst)
- stage:architect (Technical Architect)
- stage:dev (Developer)
- stage:qa (QA)
- stage:done (Complete)

This is a critical constraint to prevent:
1. Multiple routines triggering simultaneously
2. Merge conflicts and duplicate work
3. Ambiguous Lead Router routing

BO: Please label with the correct single stage and re-open.""")
    return  # STOP PROCESSING

current_stage = stage_labels[0]
```

**Why this matters:** Without this constraint, Developer and QA could run simultaneously on the same issue, causing merge conflicts and broken workflow. See `CLAUDE.md` Rule 3.

## Step 2: Load Project-Specific Docs

Based on the detected project, load the project-specific documentation:

```
docs/{project_name}/PIPELINE.md          ← Project pipeline protocol
docs/{project_name}/roles/{role}.md      ← Role-specific instructions
```

**Example:** If issue has `project:trainiq`:
- Read `docs/trainiq/PIPELINE.md` for pipeline workflow
- Read `docs/trainiq/roles/business-analyst.md` (or appropriate role)
- Follow ONLY those instructions

## Step 3: Work in Project-Specific Folder

All work must stay within the project's folder:

```
✅ ALLOWED:
- Edit projects/{project_name}/* for code changes
- Create docs/{project_name}/requirements/{issue-number}-*.md for specs
- Create docs/{project_name}/architecture/{issue-number}-*.md for designs
- Create docs/{project_name}/verification/{issue-number}-*.md for test results

❌ NOT ALLOWED:
- Edit projects/OTHER-PROJECT/*
- Edit docs/OTHER-PROJECT/*
- Edit docs/SHARED-PIPELINE.md
- Edit shared/ without explicit BO approval
```

## Step 4: Validate Branch Naming

If creating a branch, use convention:
```
projects/{project_name}/{stage}/{issue-number}-{slug}

Examples:
- projects/trainiq/feature/25-strava-oauth
- projects/agent-pipeline/bugfix/3-hello-flag
```

## Step 5: Commit to docs/{project_name}/ Only

When committing documentation changes:
- **docs-only changes:** Commit directly to main with clear message
- **code changes:** Push to feature branch, create PR
- **Always use:** `projects/{project_name}/...` paths in commit

---

## Summary: If any step fails

1. **No project label?** → Add `needs:human`, comment, STOP
2. **Can't find docs/{project}/?** → Add `needs:human`, comment, STOP
3. **Issue references wrong project code?** → Add `needs:human`, comment, STOP
4. **Cross-project dependencies?** → Use `blocked:dependency` label, don't try to fix it yourself

**The monorepo ringfencing is enforced at runtime. Stop processing if validation fails.**
