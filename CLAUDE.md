# Monorepo Guardrails & Project Ringfencing

## Overview

**trainiq-platform** is a consolidated monorepo for multiple projects (agent-pipeline, trainiq, and future projects). This document defines how projects are identified, ringfenced, and routed to the appropriate pipeline routines.

## 🔒 CRITICAL GUARDRAILS

### Rule 1: NO ROUTINE MODIFICATIONS BY AI
- **The 8 routines are LOCKED**
- Claude Code must NEVER modify, reconfigure, or adjust routine settings
- Routine changes require **EXPLICIT USER APPROVAL only**
- This includes: triggers, filters, labels, connectors, instructions, or any settings
- If a routine needs fixing, document the issue and request user approval before proceeding
- See "Routine Configuration Lock" section below for details

### Rule 2: EVERY ISSUE MUST START WITH `stage:ba` LABEL
- **When creating a new issue, ALWAYS add the `stage:ba` label**
- This label is the trigger that starts the BA routine
- Without `stage:ba`, the issue will not enter the pipeline
- The `stage:ba` label must be added alongside the `project:*` label
- Required labels for new issues:
  ```
  project:agent-pipeline (or project:trainiq)  ← Project identification
  stage:ba                                      ← Workflow entry point
  ```

---

## Project Identification

**Every GitHub issue MUST have exactly one `project:*` label.**

Valid project labels:
```
project:agent-pipeline    ← Agent Pipeline project
project:trainiq           ← TrainIQ project
project:{name}            ← Future projects go here
```

If an issue is missing a project label or has multiple project labels, the BA routine will:
1. Add the `needs:human` label
2. Comment requesting clarification
3. Stop processing until the label is fixed

## Project Structure

### Folder Layout

```
trainiq-platform/
├── projects/
│   ├── agent-pipeline/    ← agent-pipeline project code
│   ├── trainiq/           ← trainiq project code
│   └── [future-projects]  ← new projects follow same pattern
├── docs/
│   ├── SHARED-PIPELINE.md ← cross-project protocol (read-only)
│   ├── agent-pipeline/    ← project-specific pipeline & roles
│   ├── trainiq/           ← project-specific pipeline & roles
│   └── [future-projects]  ← new projects follow same pattern
└── .github/workflows/     ← generic, auto-discovers projects
```

### Code Isolation Rules

**ALLOWED:**
- Edit `projects/{project}/*` for issues labeled `project:{project}`
- Edit `docs/{project}/*` for issues labeled `project:{project}`
- Edit `.github/workflows/*.yml` for cross-project improvements

**BLOCKED:**
- Cross-project code changes in single PR (use `blocked:dependency` if needed)
- Editing another project's `projects/{project}/*` folder
- Editing another project's `docs/{project}/*` folder
- Modifying `docs/SHARED-PIPELINE.md` (read-only)
- Editing `shared/` folder without explicit approval from BO

## 8 Routine Pipeline

All issues flow through 8 project-agnostic routines. **Routines automatically detect project from the `project:*` label and load project-specific docs.**

### Routine Specifications

| Routine | Trigger | Filter | Model | Notes |
|---------|---------|--------|-------|-------|
| **ba** | Issue: Opened, Labeled | (none) | Sonnet 5 | Entry point; reads project label → loads docs/{project}/PIPELINE.md |
| **architect** | Issue: Labeled | `stage:architect` | Sonnet 5 | Design phase; loads docs/{project}/roles/technical-architect.md |
| **developer** | Issue: Labeled | `stage:dev` | Sonnet 5 | Full implementation; loads docs/{project}/roles/developer.md |
| **developer-lite** | Issue: Labeled | `stage:dev` | Haiku 4.5 | Simple issues only (complexity:simple); loads developer.md |
| **qa** | Issue: Labeled | `stage:qa` | Sonnet 5 | Full testing; loads docs/{project}/roles/qa.md |
| **qa-lite** | Issue: Labeled | `stage:qa` | Haiku 4.5 | Simple issues only (complexity:simple); loads qa.md |
| **lead** | Pull request: Closed | `Is merged = true` | Sonnet 5 | Dependency unblocking; loads docs/{project}/roles/team-lead.md |
| **lead-router** | Issue: Labeled | `needs:routing` | Opus 5 | Routing escalations; full reasoning for stuck work |

### Adding a New Project

To add a new project (e.g., `project-x`) to the monorepo:

1. **Create folder structure:**
   ```
   projects/project-x/      (copy from existing project template)
   docs/project-x/          (copy from agent-pipeline or trainiq)
   ```

2. **Copy and adapt:**
   - `docs/project-x/PIPELINE.md` — copy from trainiq, adapt project-specific workflow
   - `docs/project-x/roles/*.md` — copy 5 role files (ba, architect, developer, qa, team-lead)
   - `projects/project-x/pyproject.toml` — adapt dependencies

3. **Create a GitHub issue with `project:project-x` label**
   - The 8 routines automatically detect it and load `docs/project-x/PIPELINE.md`
   - No routine configuration changes needed

4. **(Optional) Update CI/CD** if project has special requirements:
   - Edit `.github/workflows/build.yml` to add project-specific steps
   - Most projects work with generic workflow as-is

**Why this works:** Routines read `project:{name}` label → automatically load `docs/{name}/PIPELINE.md` and `docs/{name}/roles/{role}.md`. No code changes to routines needed.

## Ringfencing Enforcement

### Label Validation

All routines validate:
```python
project_labels = [l for l in issue.labels if l.startswith('project:')]
if len(project_labels) != 1:
    add_label('needs:human')
    comment("Please add exactly one project: label")
    return
```

### Documentation Isolation

- `docs/agent-pipeline/` → ONLY for agent-pipeline work
- `docs/trainiq/` → ONLY for trainiq work
- `docs/SHARED-PIPELINE.md` → Read by all routines, never edited by any routine
- `docs/[future-project]/` → ONLY for that project

### CI/CD Isolation

Workflows use `paths:` filters to run only on project changes:

```yaml
# build-agent-pipeline.yml
on:
  push:
    paths:
      - 'projects/agent-pipeline/**'
      - 'docs/agent-pipeline/**'
      - '.github/workflows/build-agent-pipeline.yml'

# build-trainiq.yml
on:
  push:
    paths:
      - 'projects/trainiq/**'
      - 'docs/trainiq/**'
      - '.github/workflows/build-trainiq.yml'
```

## ⚠️ Routine Configuration LOCK (CRITICAL)

**THE 8 ROUTINES ARE 100% LOCKED. NO MODIFICATIONS WITHOUT EXPLICIT USER APPROVAL.**

### What is locked?
- Routine triggers (e.g., "Issue: Labeled")
- Routine filters (e.g., "stage:ba")
- Routine connectors (e.g., Composio)
- Routine models (e.g., Sonnet 5, Haiku 4.5)
- ANY settings or configuration in the Claude Code UI

### What is NOT locked?
- Routine instructions (the text/logic Claude Code executes) — these can be updated
- Project-specific docs in `docs/{project}/` — these are meant to evolve
- Code in `projects/{project}/` — this is the actual work

### Why this lock exists
1. **Trigger/filter issues are invisible in git** — they're in Claude Code UI only, can't be reviewed
2. **Small mistakes break the entire pipeline** — a wrong filter blocks all workflow
3. **Configuration evolved through 10+ iterations** — fixing issue #8 took multiple refinement cycles
4. **BA trigger is particularly critical** — it controls entry point to the entire pipeline

### If you need to modify a routine:
1. **STOP** — do not make changes
2. **Document** the problem clearly (which routine, what's wrong, why it needs changing)
3. **Request user approval** — message the BO with the specific change needed
4. **Wait for approval** — get explicit permission before touching any routine settings
5. **Execute** — make only the approved change
6. **Test** — verify the pipeline still works
7. **Update this CLAUDE.md** — document the change for future reference

**Remember:** The pipeline only works because these settings are stable. Breaking them breaks everything.

## Shared Code (shared/ folder)

The `shared/` folder is for code reused across projects:
- Base classes (e.g., connector interfaces)
- Common utilities
- Storage abstraction layers

**Modification rules:**
- Any change to `shared/` must be approved by representatives from ALL affected projects
- Changes should be documented in PR description with impact analysis
- Add `blocked:dependency` to any project issues waiting on shared/ changes

## Labels

All issues use these labels:

**Project identification (required):**
```
project:agent-pipeline, project:trainiq, project:{name}
```

**Stage progression:**
```
stage:ba          → Business Analyst (entry point)
stage:architect   → Technical Architect
stage:dev         → Developer
stage:qa          → QA/Testing
stage:done        → Complete
```

**Priority (used for queue ordering):**
```
priority:high, priority:medium, priority:low
```

**Type (work classification):**
```
type:feature, type:bug, type:tooling, type:docs
```

**Complexity (for lite/full routine split):**
```
complexity:simple, complexity:medium, complexity:high
```

**Special flags:**
```
blocked:dependency    → Issue waiting on another issue to complete
needs:human          → Needs BO intervention
needs:routing        → Routine got stuck, escalate to lead-router
```

## Preventing Cross-Project Accidents

### What prevents accidents?

1. **Routines validate project labels** — stop processing if missing or ambiguous
2. **Branch naming convention** — `projects/{project}/...` makes project clear
3. **Code paths enforced** — separate `projects/{project}/*` folders
4. **CI/CD filters** — workflows only run on project-specific paths
5. **PR reviews** — changes in unexpected folders are visible in diff

### What to do if you see cross-project changes in a PR

1. Request the author split into separate PRs by project
2. Add `needs:human` to the issue
3. Assign to BO for routing decision

---

**Last updated:** 2026-10-03
