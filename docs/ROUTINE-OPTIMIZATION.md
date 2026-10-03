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

---

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

