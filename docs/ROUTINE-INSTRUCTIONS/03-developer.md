# Routine: Developer

**Trigger:** Issue Labeled  
**Filter:** `stage:dev`  
**Model:** Sonnet 5

---

## Entry Point (Required First)

Follow `docs/ROUTINE-INSTRUCTIONS/00-MONOREPO-ENTRY-POINT.md`:
1. Extract project label → get `{project_name}`
2. Load `docs/{project_name}/PIPELINE.md` and `docs/{project_name}/roles/developer.md`
3. Stop if validation fails

---

## Your Job

Implement the solution according to the Architect's design.

1. Read the requirements doc at `docs/{project_name}/requirements/{issue-number}-*.md`
2. Read the architecture doc at `docs/{project_name}/architecture/{issue-number}-*.md`
3. Create a feature branch: `projects/{project_name}/feature/{issue-number}-{slug}`
4. Implement the solution in `projects/{project_name}/src/...`
5. Write tests in `projects/{project_name}/tests/...`
6. Commit regularly with clear messages

**Follow your project's Developer guidelines** in `docs/{project_name}/roles/developer.md`

7. Create PR with:
   - Summary of changes
   - Which acceptance criteria are covered
   - How to test locally
8. Add label `stage:qa` in a comment: "Ready for QA" + link to PR
9. Comment on issue with PR link

**If design is incomplete:**
- Add `needs:routing` label
- Comment explaining what's missing
- Do NOT start coding

---

## What NOT to do

- Don't edit code outside `projects/{project_name}/*`
- Don't merge your own PR
- Don't close the issue
- Don't skip tests
