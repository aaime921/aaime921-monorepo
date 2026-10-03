# Routine: QA (Lite)

**Trigger:** Issue Labeled  
**Filter:** `stage:qa` AND `complexity:simple`  
**Model:** Haiku 4.5

---

## Entry Point (Required First)

Follow `docs/ROUTINE-INSTRUCTIONS/00-MONOREPO-ENTRY-POINT.md`:
1. Extract project label → get `{project_name}`
2. Load `docs/{project_name}/PIPELINE.md` and `docs/{project_name}/roles/qa.md`
3. Stop if validation fails

---

## Your Job

Same as `05-qa.md` but **optimized for simple changes** (complexity:simple).

This routine handles:
- Simple bug fix verification
- Small refactoring checks
- Minor feature testing

Follow **all the same steps as QA** but work efficiently using the lite model.

If the issue turns out to be more complex than "simple":
- Change `complexity:simple` to `complexity:medium` or `complexity:high`
- Add `needs:routing` label
- Comment: "This is more complex than initially assessed, escalating to full QA"
- Stop processing

---

## Output Format

Same as QA routine:
- Verification doc at `docs/{project_name}/verification/{issue-number}-*.md`
- If pass: add `stage:done` label
- If fail: add `stage:dev` label + comment with failure reason
