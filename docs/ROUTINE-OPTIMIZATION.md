# Routine Optimization & Learning Log

**Purpose:** Track observations, edge cases, and optimization opportunities from routine execution in production.

**Principle:** Monitor first, improve only when significant (>20% efficiency or critical blockers).

---

## Current Issues Being Monitored

### Issue #18: Strava Connector
- **Status:** Escalated to needs:human (multiple stage labels detected and corrected by Rule 3)
- **Observations:**
  - ✅ Multiple stage guardrail worked correctly
  - ⚠️ Documentation mismatch between comments and repo state exposed sync issue
  - ⚠️ Both Developer and QA ran simultaneously (guardrail prevented damage)

---

## Completed Improvements

| Date | Change | Routine | Impact | Commits |
|------|--------|---------|--------|---------|
| 2026-10-03 | Add single-stage validation (Rule 3) | All | HIGH | `14b0a88`, `fdafbb0` |
| 2026-10-09 | Token cut (AND-6): event gate + triggering-issue-only in all instructions; PIPELINE.md 14.5KB to 3KB; CLAUDE.md 11KB to 3KB; role docs deduplicated into `CONVENTIONS.md`; lite routines and Triage retired from docs; code-review workflow limited to one run per non-docs PR | All | HIGH | see PR |

---

## UI changes applied 2026-10-09 (BO; routine settings are locked)

Findings: issue #58 produced 8 `labeled` events, so the unfiltered BA fired about 8 times for 1 real job,
and `developer-lite`/`qa-lite` fired on every `stage:dev`/`stage:qa` although no issue has a `complexity:*`
label (no Triage routine exists). About 16 runs per issue vs about 5 needed, each paying roughly 10k tokens of startup context.

1. **ba**: remove trigger "Issue: Opened"; keep "Issue: Labeled" and add filter `stage:ba`. Paste `01-ba.md`.
2. **Delete `developer-lite` and `qa-lite`.** Decision: no complexity tiering. A Triage routine would add one
   run (about 10k tokens) per issue to save model cost only on rare simple issues, and lite routines sharing the
   `stage:dev` / `stage:qa` filter double-fire on every handoff. If tiering is wanted later, use distinct stage
   labels (`stage:dev-lite`) set by BA/Architect so triggers filter exactly.
3. **Models**: ba/architect/developer/qa/lead to Sonnet 5.5 (same price as Sonnet 5, newer); lead-router to Opus 5.5
   ($4/$20 vs $5/$25 per M tokens). Only if available in the routine model picker.
4. Paste the new `02`, `03`, `05`, `07`, `08` instruction files into their routines.
5. Test (see `SETUP-GUIDE.md` smoke checks). Done.
6. Optional: `~/.claude/CLAUDE.md` still lists 8 routines and the lite rows.

Verified by smoke tests #64 (full pipeline: one run per stage) and #67 (BA runs once; the earlier double run came from the Opened trigger and is fixed by #66).

Measure: compare runs per issue in the routines run history before and after; target is about 5.

## Potential Improvements (Monitoring List)

### P1: Routine Idempotency Check
- **What:** Detect if routine already processed this issue (check for prior comment from same routine)
- **Why:** Prevent duplicate work if webhook fires twice on same event
- **Trigger:** Implement if we observe duplicate routine execution on >1 issue
- **Status:** MONITORING

### P2: Document Existence Verification  
- **What:** Before proceeding, verify required docs exist in repo (not just claimed in comments)
- **Why:** Architect blocked when docs didn't exist despite comment claiming they did
- **How:** Use git CLI to check for file existence before loading
- **Trigger:** Implement if we see >2 issues blocked by missing docs
- **Status:** MONITORING

### P3: Explicit Stage Transition Messages
- **What:** When removing old stage and adding new stage, add clear handoff comment
- **Why:** Makes pipeline transparent, easier to debug issues
- **Impact:** Medium (observability improvement)
- **Trigger:** Implement if BO requests it or routing confusion occurs
- **Status:** LOW PRIORITY

### P4: Project-Specific Label Validation
- **What:** Some projects may require additional labels (complexity, priority, type) 
- **Why:** Better routing decisions and work prioritization
- **When:** After patterns stabilize (3+ projects)
- **Status:** FUTURE

---

## Routine Behavior Summary (as observed)

### BA Routine
- ✅ Detects project labels correctly
- ✅ Loads project-specific docs
- ✅ Creates requirements documents
- Status: STABLE

### Architect Routine  
- ✅ Escalates properly when documentation integrity questions arise
- ✅ Creates architecture docs in correct project folder
- ⚠️ May run before git changes are fully synced to GitHub
- Status: STABLE (monitor git sync timing)

### Developer Routine
- ⚠️ May run even when prior stages have unresolved questions
- Status: MONITORING

### QA Routine
- ⚠️ May run before Developer completes
- Status: MONITORING (Rule 3 should prevent this)

### Lead Router
- Status: Not yet observed in action
- Status: MONITORING

---

## Data Collection

For each new issue processed:
1. Note which routines ran and in what order
2. Record any `needs:human` or `needs:routing` escalations
3. Check for duplicate routine execution on same event
4. Verify all stage transitions happened
5. Note any unexpected labels or behaviors

---

## Update Criteria

**Update routine instructions ONLY when:**
1. ✅ Significant efficiency gain (>20% time savings)
2. ✅ Critical blocker preventing pipeline progress  
3. ✅ Recurring pattern observed on 3+ issues
4. ✅ Explicit user request

**Do NOT update for:**
- ❌ One-off edge cases
- ❌ Minor improvements (<5%)
- ❌ Hypothetical scenarios
- ❌ Cosmetic wording

