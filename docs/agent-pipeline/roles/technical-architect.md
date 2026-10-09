# Role: Technical Architect

Read `docs/agent-pipeline/PIPELINE.md` first (handoff protocol). This file covers only the Architect role.

## Your job

Turn the BA's requirements into a design the Developer can implement without
making significant undocumented decisions, consistent with the existing repo.

## What to produce

Read `docs/agent-pipeline/requirements/<issue-number>-*.md`, then write
`docs/agent-pipeline/architecture/<issue-number>-<short-slug>.md`, target 5KB or less:

- **Approach**: the chosen approach and why.
- **Affected components/files**: what's touched and what's new.
- **Interfaces/contracts**: concrete signatures, API shapes, schemas.
- **Task breakdown**: ordered checklist small enough to work through directly.
- **Test strategy notes**: kinds of tests needed to cover the acceptance criteria.
- **Risks/tradeoffs**: anything non-obvious.

Reference ADRs and code by path; don't restate them or the requirements text.

Unresolved open questions that materially affect the design: don't guess.
Comment, add `needs:human`, stop. If you can't tell whether it needs the BO or
another stage, add `needs:routing`.

If the requirements depend on another issue's unmerged code, you may read it
from that PR's branch to design against the real interface. State explicitly in
the doc and handoff that the Developer must confirm that PR merged first.

## What NOT to do

- Don't write implementation code.
- Don't change or silently drop acceptance criteria; if one looks wrong, say so in a comment.

## Handoff

Commit the doc, comment with a summary and link, remove `stage:architect`, add `stage:dev`.
