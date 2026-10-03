# Phase 2: Reconfigure Routines — Setup Guide

This guide shows how to apply the monorepo-aware routine instructions to Claude Code.

## Quick Summary

You have **8 routines** that need to be configured in Claude Code UI. Each routine must:
1. Keep its current trigger/filter/connector setup
2. Use the new instruction from `docs/ROUTINE-INSTRUCTIONS/{NN-routine}.md`
3. Enforce monorepo entry point (project label detection)

---

## Routine Configuration

### ✅ What stays the same:
- Trigger events (Issue Opened, Issue Labeled, PR Closed)
- Filters (stage:architect, stage:dev, stage:qa, needs:routing, etc.)
- Connectors (Composio/GitHub)
- Models (Sonnet 5, Haiku 4.5, Opus 5)

### ⚠️ What changes:
- **Instruction content** — copy from `docs/ROUTINE-INSTRUCTIONS/{NN-routine}.md`
- **First step** — All routines now read project label first
- **Work location** — All routines now work in `projects/{project_name}/` and `docs/{project_name}/`

---

## Setup Steps (Claude Code UI)

### For each of the 8 routines:

1. **Open Claude Code UI** → Go to your routine
2. **Copy the instruction** from the corresponding file:
   - **ba** → Copy `docs/ROUTINE-INSTRUCTIONS/01-ba.md`
   - **architect** → Copy `docs/ROUTINE-INSTRUCTIONS/02-architect.md`
   - **developer** → Copy `docs/ROUTINE-INSTRUCTIONS/03-developer.md`
   - **developer-lite** → Copy `docs/ROUTINE-INSTRUCTIONS/04-developer-lite.md`
   - **qa** → Copy `docs/ROUTINE-INSTRUCTIONS/05-qa.md`
   - **qa-lite** → Copy `docs/ROUTINE-INSTRUCTIONS/06-qa-lite.md`
   - **lead** → Copy `docs/ROUTINE-INSTRUCTIONS/07-lead.md`
   - **lead-router** → Copy `docs/ROUTINE-INSTRUCTIONS/08-lead-router.md`

3. **In Claude Code UI:**
   - Click on the routine
   - Paste the instruction into the "Behavior" or "Instructions" field
   - **Do NOT change** trigger, filter, or connector settings
   - Save

4. **Test the routine** by creating a test issue with:
   ```
   project:agent-pipeline  (or project:trainiq)
   type:test
   ```

---

## Verification Checklist

After updating all 8 routines, verify:

- [ ] **ba** reads project label first, loads `docs/{project}/PIPELINE.md`
- [ ] **architect** creates design docs in `docs/{project}/architecture/`
- [ ] **developer** works in `projects/{project}/src/`
- [ ] **developer-lite** only handles `complexity:simple`
- [ ] **qa** creates verification docs in `docs/{project}/verification/`
- [ ] **qa-lite** only handles `complexity:simple`
- [ ] **lead** unblocks issues with `blocked:dependency`
- [ ] **lead-router** escalates stuck issues with correct routing
- [ ] No routine edits files outside its project folder
- [ ] All routines validate project label before proceeding

---

## Testing Strategy

### Test 1: agent-pipeline issue
1. Create issue with `project:agent-pipeline`
2. Watch BA routine process it
3. Verify: requirements doc goes to `docs/agent-pipeline/requirements/`
4. Verify: issue gets labeled `stage:architect` after BA completes

### Test 2: trainiq issue
1. Create issue with `project:trainiq`
2. Watch BA routine process it
3. Verify: requirements doc goes to `docs/trainiq/requirements/`
4. Verify: issue gets labeled `stage:architect` after BA completes

### Test 3: missing project label
1. Create issue WITHOUT project label
2. Watch BA routine process it
3. Verify: issue gets labeled `needs:human`
4. Verify: BA comments requesting project label
5. Verify: BA stops processing

### Test 4: cross-project check
1. Create feature branch in `projects/agent-pipeline/`
2. Create PR with code change
3. Create issue with `project:trainiq` labeled `stage:qa`
4. Verify: QA does NOT accept the PR (wrong project folder)

---

## Rollback Plan

If a routine update breaks something:

1. **Identify the issue** from GitHub workflow logs
2. **Revert the instruction** to previous version (or clear field)
3. **Comment on failing issue** with `needs:human` label
4. **Report to BO** with error details

---

## After Routine Setup

Once all 8 routines are configured:

1. **Install Claude GitHub App** on aaime921-monorepo (if not already done)
2. **Configure webhook** to point to aaime921-monorepo
3. **Test full pipeline:**
   - Create test issue → BA processes → Architect processes → Dev processes → QA processes → Done
4. **Migrate open issues** from agent-pipeline and trainiq repos:
   - Add `project:agent-pipeline` or `project:trainiq` label to each
   - Re-trigger with stage label (e.g., if in dev, remove then re-add `stage:dev`)

---

## Resources

- **Monorepo docs:** https://github.com/aaime921/aaime921-monorepo
- **CLAUDE.md:** https://github.com/aaime921/aaime921-monorepo/blob/main/CLAUDE.md
- **Architecture plan:** https://claude.ai/artifact/5B4AcTU997fn7Y2ZWM6e7A
- **Routine instructions:** `docs/ROUTINE-INSTRUCTIONS/`
