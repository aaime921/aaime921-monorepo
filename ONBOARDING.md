# Monorepo Onboarding for New Claude Sessions

**Purpose:** Get up to speed on the trainiq project, monorepo architecture, and active work.

**Read time:** 10 minutes to full context

---

## Priority 1: Critical Context (READ FIRST)

### 1. Global Guardrails
**File:** `CLAUDE.md`
- Rule 1: Routine configuration is LOCKED (no AI modifications)
- Rule 2: Every issue starts with `stage:ba` label
- Rule 3: Exactly ONE `stage:*` label per issue (critical constraint)
- Project ringfencing rules
- Label conventions

**Why:** These are enforcement rules that break the pipeline if violated.

### 2. Active Work Status
**File:** `docs/ROUTINE-OPTIMIZATION.md` (Section: "Current Issues Being Monitored")
- Issue #18: Strava session-cookie connector (currently `stage:architect`)
- Known observations and learnings
- Monitoring checklist for each issue

**Why:** Know what's actively being worked on and watch for.

### 3. Project-Specific Pipeline
**File:** `docs/trainiq/PIPELINE.md`
- TrainIQ workflow and acceptance criteria
- Which components: Peloton, Eufy, Strava
- Label conventions for trainiq issues
- Role responsibilities

**Why:** Defines how trainiq work flows through stages BA→Architect→Dev→QA→Done.

---

## Priority 2: System Architecture (READ NEXT)

### 4. Implementation Summary
**File:** `IMPLEMENTATION_SUMMARY.md`
- 8 project-agnostic routines overview
- End-to-end workflow tested and verified
- What works, what to monitor
- Next steps

**Why:** Understand the automated pipeline infrastructure.

### 5. Routine Instructions
**Files:** `docs/ROUTINE-INSTRUCTIONS/00-MONOREPO-ENTRY-POINT.md` + `01-ba.md` through `08-lead-router.md`
- Entry-point protocol all routines follow
- Individual routine behaviors
- Project detection and documentation loading

**Why:** Understand how each automated agent works and what it validates.

### 6. Shared Pipeline Protocol
**File:** `docs/SHARED-PIPELINE.md` (read-only)
- Cross-project coordination rules
- Dependency management via `blocked:dependency`
- Escalation via `needs:routing`

**Why:** Understand cross-project constraints.

---

## Priority 3: TrainIQ Role Docs (READ BY ROLE)

Read the relevant role file for the work you're doing:

| Role | File | When to Read |
|------|------|--------------|
| **BA** | `docs/trainiq/roles/business-analyst.md` | Creating requirements or clarifying scope |
| **Architect** | `docs/trainiq/roles/technical-architect.md` | Designing solutions or creating architecture docs |
| **Developer** | `docs/trainiq/roles/developer.md` | Implementing features or fixing bugs |
| **QA** | `docs/trainiq/roles/qa.md` | Testing or verifying acceptance criteria |
| **Team Lead** | `docs/trainiq/roles/team-lead.md` | Unblocking dependencies or routing stuck issues |

---

## Priority 4: Background & Context (REFERENCE)

### Persistent Memory
**Location:** `/Users/andre/.claude/projects/-Users-andre/memory/`
- `routine_monitoring_log.md` — Observations from routine execution
- `MEMORY.md` — Index of all project memories
- `trainiq_pipeline_project.md` — TrainIQ project history
- `agent_pipeline_project.md` — Agent Pipeline project history

**Why:** Context for decisions made, patterns observed, why certain guardrails exist.

### TrainIQ-Specific Docs
**Location:** `docs/trainiq/`
- `requirements/*.md` — Acceptance criteria (issues #1-24+)
- `architecture/*.md` — Design decisions and technical approach
- `verification/*.md` — Test evidence and validation
- `adr/*.md` — Architecture decision records (ADR-006 through ADR-038)
- `setup/peloton-oauth.md` — OAuth setup protocol
- `discovery/*.md` — Analysis docs

**Why:** Deep dive into trainiq components when working on specific features.

---

## Current State at a Glance

| Component | Status | Details |
|-----------|--------|---------|
| **Monorepo** | ✅ Live | aaime921/aaime921-monorepo on GitHub |
| **Routines** | ✅ Deployed | 8 routines actively processing issues |
| **Issue #18** | 🔄 Active | Strava connector at `stage:architect` |
| **Trainiq Issues** | 4 OPEN | #20, #18, #17, #16 (all in or past `stage:dev`) |
| **Agent-Pipeline Issues** | 1 OPEN | #19 at `stage:done` |
| **Critical Guardrails** | ✅ Active | Rule 3 (single stage) actively enforced |

---

## Quick Start: Starting Work on TrainIQ

### Scenario A: New Issue Creation
1. Read: `CLAUDE.md` (Rules 1-3)
2. Read: `docs/trainiq/PIPELINE.md`
3. Create issue with labels: `project:trainiq`, `stage:ba`, `complexity:simple/medium/high`
4. BA routine will auto-process

### Scenario B: Resume Issue #18 Work
1. Read: `docs/ROUTINE-OPTIMIZATION.md` (current observations)
2. Read: `docs/trainiq/roles/technical-architect.md` (next phase)
3. Check issue: https://github.com/aaime921/aaime921-monorepo/issues/18
4. Review: `docs/trainiq/requirements/18-strava-session-cookie-connector.md`
5. Create: `docs/trainiq/architecture/18-strava-session-cookie-connector.md` (if Architect)

### Scenario C: Implement TrainIQ Feature
1. Read: `CLAUDE.md` (Rules)
2. Read: `docs/trainiq/roles/developer.md`
3. Check: Which issue to work on (find `stage:dev` issue)
4. Read: `docs/trainiq/requirements/{issue}-*.md` (acceptance criteria)
5. Work in: `projects/trainiq/src/` and create PR against main

### Scenario D: QA a Feature
1. Read: `CLAUDE.md` (Rules)
2. Read: `docs/trainiq/roles/qa.md`
3. Check: Which issue to test (find `stage:qa` issue)
4. Read: `docs/trainiq/requirements/{issue}-*.md` (acceptance criteria)
5. Verify: Create `docs/trainiq/verification/{issue}-{date}.md`

---

## Key Files Summary

### Must-Read (Do This First)
```
CLAUDE.md                                    (5 min)
docs/ROUTINE-OPTIMIZATION.md                 (3 min)
docs/trainiq/PIPELINE.md                     (5 min)
```

### Should-Read (Do This Next)
```
IMPLEMENTATION_SUMMARY.md                    (8 min)
docs/ROUTINE-INSTRUCTIONS/00-MONOREPO-ENTRY-POINT.md  (5 min)
docs/trainiq/requirements/18-strava-...md    (5 min - for current work)
```

### Reference-as-Needed
```
docs/trainiq/roles/{role}.md                 (by role, 5-10 min each)
docs/trainiq/architecture/*.md               (design patterns)
docs/trainiq/adr/*.md                        (decision rationale)
```

---

## Repository Links

- **Monorepo:** https://github.com/aaime921/aaime921-monorepo
- **Issue #18 (Active):** https://github.com/aaime921/aaime921-monorepo/issues/18
- **All TrainIQ Issues:** Filter by `project:trainiq` label

---

## Verify You're Ready

Before starting work, confirm:
- [ ] Read CLAUDE.md (especially Rule 3: single stage per issue)
- [ ] Read docs/trainiq/PIPELINE.md
- [ ] Checked current issue status on GitHub
- [ ] Know which stage you're working at (BA/Architect/Dev/QA)
- [ ] Read the appropriate role doc for your stage
- [ ] Understand monorepo folder structure (projects/trainiq/ and docs/trainiq/)

---

**Questions during onboarding?**
- Check CLAUDE.md first (answers most questions)
- Check docs/trainiq/roles/{your-role}.md (role-specific guidance)
- Check issue #18 comments (real examples of pipeline in action)

**Ready to go!** 🚀

