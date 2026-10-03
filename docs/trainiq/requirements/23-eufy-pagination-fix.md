# Requirements: Fix Eufy Connector Pagination for Large Datasets

**Issue:** #23  
**Test Issue for BA Pilot (Phase 2)**

## Summary

`EufyConnector.download()` currently assumes all weigh-in records fit in a single API response page. Real accounts with 100+ recorded weigh-ins will have paginated responses, and the connector silently drops pages beyond the first one, corrupting the stored dataset. Add pagination support following the checkpoint/incremental-sync pattern already established in the codebase.

## Scope

- Review Eufy API documentation (or live-captured response shape from issue #1's verification) to confirm pagination fields
- Modify `download()` to loop through all available pages until no results remain
- Preserve existing `after`-checkpoint behavior (resume from last-synced weigh-in timestamp)
- No changes to normalize(), authenticate(), or checkpoint extraction

### Out of scope

- Backfilling historical data on accounts that already synced partial data (that's an ops task, not this fix)
- Adding new fields or changing field mapping (issue #1 already handles that)

## Acceptance criteria

1. **Pagination fields identified:** Eufy response includes pagination metadata (e.g., `page`, `total_pages`, `has_next`, or cursor field). This must be verified against actual captured response, not assumed from docs.
2. **Loop until empty:** `download()` continues fetching pages while pagination indicates more results; stops when no results returned.
3. **Full history on first sync:** Given an account with 150 weigh-ins (3 pages at 50 per page), first sync without checkpoint retrieves all 150 across all 3 pages, not just the first 50.
4. **Incremental sync still works:** Given checkpoint `after=1696000000`, subsequent sync skips already-retrieved weigh-ins and only fetches new ones; no duplicate syncs of old data.
5. **Cursor consistency:** `extract_resume_cursor()` returns the most recent weigh-in's timestamp as a string (same type-safety fix as issue #12), so checkpoint comparison is safe.
6. **Existing tests pass:** All existing unit tests for EufyConnector pass; new pagination tests added.
7. **Pagination test coverage:** Unit tests verify:
   - Multi-page response is fully retrieved (not just first page)
   - Single-page response still works (no breaking change)
   - Empty response (no weigh-ins) handled gracefully

## Open questions

**One verification question (blocks Architect):** What is the actual pagination shape in Eufy's API response? The requirements above assume standard pagination metadata; if the API uses a different pattern (cursor-based, relative links, etc.), that changes the implementation approach. 

**To unblock:** Check issue #1's live-captured payload (`docs/verification/eufy-*.md` or BACKLOG.md BL-xxx) to see the actual response structure and confirm pagination fields exist. If docs don't show the shape, this should be a live-verification task before Architect designs.
