# Routine: Developer (Lite)

**Trigger:** Issue Labeled  
**Filter:** `stage:dev` AND `complexity:simple`  
**Model:** Haiku 4.5

---

## Entry Point (Required First)

Follow `docs/ROUTINE-INSTRUCTIONS/00-MONOREPO-ENTRY-POINT.md`:
1. Extract project label → get `{project_name}`
2. Load `docs/{project_name}/PIPELINE.md` and `docs/{project_name}/roles/developer.md`
3. Stop if validation fails

---

## Your Job

Same as `03-developer.md` but **optimized for simple changes** (complexity:simple).

This routine handles:
- Small bug fixes
- Simple refactoring
- Minor feature additions
- Documentation updates

Follow **all the same steps as developer** but work efficiently using the lite model.

If the issue turns out to be more complex than "simple":
- Add `complexity:medium` or `complexity:high` label
- Add `needs:routing` label
- Comment: "This is more complex than initially assessed, escalating to full developer"
- Stop processing

---

## Output Format

Same as developer routine:
- Feature branch in `projects/{project_name}/feature/...`
- Code in `projects/{project_name}/src/...`
- Tests in `projects/{project_name}/tests/...`
- Create PR with summary
- Add `stage:qa` label + PR link
