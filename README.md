# trainiq-platform

Consolidated monorepo for agent-pipeline and trainiq projects with complete project ringfencing.

## Projects

- **agent-pipeline** (projects/agent-pipeline/) — GitHub webhook-based automation pipeline
- **trainiq** (projects/trainiq/) — Health data integration platform

## Structure

```
projects/          ← Project code (ringfenced)
docs/              ← Project-specific pipeline docs (ringfenced)
shared/            ← Shared utilities (cross-project)
.github/workflows/ ← CI/CD (auto-discovers projects)
```

## Adding a New Project

See `CLAUDE.md` → "Adding a New Project" section.

## Workflow

See `docs/SHARED-PIPELINE.md` for the cross-project pipeline protocol.

Each project has its own pipeline docs at `docs/{project}/PIPELINE.md`.
