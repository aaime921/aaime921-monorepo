# Discovery: Zero-Cost Paths for Automated Strava Data Retrieval

**Issue:** #20  
**Date:** 2026-10-01  
**Constraints:** $0 ongoing cost, BO free Strava account, Peloton Bike + Apple Watch 7

---

## Context

Strava's official API blocked free accounts as of June 2026 (Standard Tier now requires paid subscription). The BO's data source is:
- **Primary:** Peloton Bike (direct connector exists, OAuth+refresh-token working)
- **Secondary:** Running via Apple Watch 7 (syncs to Strava)

Strava is valuable because it's the aggregation point for both training modalities. Goal: pull running data without paying $12/mo for Strava subscription.

**Three candidate paths identified by the BO:**
1. **Option A** — Official account bulk export + browser automation
2. **Option B** — Session-cookie-based unofficial access (strava-offline pattern)
3. **Option C** — Bypass Strava, connect directly to Apple Watch source

---

## Option A: Official Account Bulk Export + Browser Automation

**How it works:**
- Strava Settings → My Account → "Download your account" (Settings → Account → Privacy → Download Account)
- Triggers an email with a download link (GPX, TCX, FIT files + CSV export)
- Export includes full activity history, no API required, free on any Strava tier

**Feasibility:**
- ✅ **Free:** Included with free Strava account
- ✅ **Official:** Part of Strava's documented GDPR export flow, no ToS violation
- ✅ **No API subscription needed**
- ❌ **Manual request required:** Generates a new export archive each time; no programmatic trigger available
- ❌ **Automation is fragile:** Would require Selenium/Playwright to click "Download your account," wait for email, download ZIP, extract CSVs — breaks if Strava's UI changes
- ❌ **No incremental sync:** Always delivers full history; no "since last export" API

**Implementation effort:**
- High fragility. Strava's email delivery is asynchronous (can take hours), no status polling. Export must be manually triggered or automated via browser, both poor patterns.
- Script would need: login automation (fragile), element selector targeting (breaks on UI changes), email polling or manual link copy, ZIP extraction, CSV parsing.

**Recommendation:** 
- **Not viable for automated, zero-touch sync.** The manual step (request export, wait for email, download) defeats the goal of automatic data retrieval. Browser automation adds fragility without eliminating the core manual dependency.

---

## Option B: Session-Cookie-Based Unofficial Access

**How it works:**
- Strava issues a `_strava4_session` cookie after browser login
- Cookie can be used to authenticate direct HTTP requests to `api.strava.com` endpoints
- Open-source example: `strava-offline` (Python, GitHub: camjccc/strava-offline)
- No official API subscription needed; works with free accounts

**Feasibility:**
- ✅ **Free:** No API tier, no subscription
- ✅ **Automatic:** Once cookie is obtained and stored, can run sync on a schedule
- ✅ **Incremental sync support:** Can track `after` timestamp and poll only new activities
- ❌ **Unofficial / fragile:** Not documented by Strava; can break if they change cookie format or auth flow
- ❌ **Cookie expiry:** Session cookies expire, requiring periodic manual re-login (typically weeks, sometimes days under heavy load)
- ❌ **ToS gray area:** Strava's ToS prohibits scraping and automated access outside official API; session cookies are not "API use" but also not blessed
- ❌ **Bot detection risk:** Strava has activity-based bot detection; frequent automated requests can trigger rate limits or login challenges
- ❌ **Maintenance burden:** Every Strava web client update risks changing the cookie name, format, or auth flow

**Technical viability:**
- Cookie-based requests would hit `api.strava.com` directly (same endpoint official API uses), so rate limits and response shapes are compatible
- Strava-offline (2.5k stars, active updates as of 2024) proves the pattern works, but it's a research project, not production-grade
- Would need to:
  1. Extract/obtain `_strava4_session` cookie (manual from browser DevTools, or automate via Selenium — both fragile)
  2. Store in CredentialStore alongside expiry tracking
  3. Periodically refresh login if cookie expires (prompt user for re-auth)
  4. Handle bot-detection 429s with exponential backoff

**Real-world track record:**
- Strava has been actively discouraging and rate-limiting cookie-based automation (2024–2026)
- Projects like strava-offline acknowledge "works today, may break tomorrow"
- No official deprecation, but implied risk

**Recommendation:**
- **Viable as a fallback, not primary.** Works now, but:
  - Requires user to periodically refresh cookie (not fully automatic)
  - ToS violation risk (Strava's lawyers could shut it down anytime)
  - Maintenance burden if Strava changes auth flow
  - Better than bulk-export automation, worse than direct API

---

## Option C: Direct Apple Watch Data Source

**Context:**
- BO's running data originates from Apple Watch 7
- Currently syncs to Strava as an aggregation hub
- Goal: retrieve running data directly from the Watch source, bypassing Strava entirely

**Technical landscape:**
Apple's data export and API options:

### C.1: HealthKit (WatchOS/iOS native)
- **What it is:** Apple's on-device health database (heart rate, workouts, steps, etc.)
- **Access method:** iPhone Health app or third-party apps with HealthKit permission
- **Export format:** iPhone Health → Settings → Privacy → Health → Export/Share (CSV/XML)
- **Free:** Yes
- **Automatic API?** No official headless API for extracting HealthKit data programmatically without a mobile app
- **Verdict:** Export works, but no automation without building an iOS app

### C.2: Apple Health Records (EHR data)
- Not applicable — running data isn't EHR-grade medical data

### C.3: iCloud Drive backup
- Stored in encrypted iCloud backups; no programmatic export
- Not viable

### C.4: Third-party Watch apps that offer exports
- If the running app (e.g., Strava, Garmin Connect, Wahoo, Apple Fitness+) offers direct export/API access:
  - ✅ Garmin Connect: Free tier offers API access to device data (requires OAuth; free for personal use)
  - ✅ Wahoo Fitness: Similar (free tier available)
  - ❌ Apple Fitness+: No export API (integrates with Health only)
  - ❌ Strava: Already investigated (subscription blocked)

**Discovery question for BO:** Which app actually records the running data on the Watch?
- If it's Strava app → already syncs to Strava (Option B or bulk export required)
- If it's Garmin (Connect app on Watch) → can access Garmin API directly (potentially free)
- If it's native Apple Workouts → limited export options, no free headless API

**Recommendation:**
- **Not immediately viable without knowing the primary recording app.** If BO uses:
  - Garmin Watch/app + Garmin Connect → Option C.4 (Garmin API, likely free)
  - Apple Watch native workouts → limited to manual export via Health app
  - Strava app → circles back to Options A/B (Strava exports or cookies)

---

## Summary: Path Viability Matrix

| Path | Free | Automatic | ToS-Safe | Maintainable | Blocker |
|------|------|-----------|----------|--------------|---------|
| **A: Bulk Export** | ✅ | ❌ (manual request) | ✅ | ❌ (UI fragile) | Manual trigger required |
| **B: Session Cookies** | ✅ | ⚠️ (needs periodic re-login) | ❌ (gray area) | ❌ (Strava changes) | Cookie expiry, ToS risk |
| **C: Direct Watch Source** | ✅ | ⚠️ (app-dependent) | ✅ | ? | Need to identify recording app |

---

## Recommended Next Steps (for BO decision)

**If running data source is NOT Strava app (e.g., Garmin Connect, Apple native):**
- Pursue **Option C** — connect directly to that source, skip Strava entirely
- Running + Peloton data both pulled directly from sources, Strava becomes optional aggregate view, not a data source

**If running data IS recorded in Strava app (no alternative source):**
- Pursue **Option B** (session cookies) as primary
  - Pros: Automatic syncs, incremental via `after` timestamp, free
  - Cons: Requires periodic cookie refresh (~weekly), ToS gray area, maintainability risk
  - Fallback: Option A (manual bulk export) if Option B breaks

**Discovery action item:**
- **BO to confirm:** What app/device actually records running workouts on your Apple Watch 7? Does it sync to Strava, or does Strava pull Strava-recorded activities from the Watch?

---

## Implementation notes (for Architect, if Option B selected)

**If session-cookie approach is approved:**
1. New connector: `StravaUnofficial` (parallel to existing `StravaConnector` to keep them separate)
2. Credential types: `_strava4_session` (cookie), `session_obtained_at`, `session_expires_at`
3. Authenticate flow:
   - Check stored cookie; if valid (within expiry), skip re-login
   - If expired or missing, prompt user to log in via browser and paste cookie (same pattern as Peloton bearer-token setup)
4. Download: `GET https://api.strava.com/api/v3/athlete/activities?after={checkpoint}` (uses cookie auth)
5. Normalize: Same field mapping as existing `StravaConnector` (compatibility)
6. Sync: Reuse existing checkpoint pattern
7. Error handling: 429 (rate limit) → TransientError with Strava-provided `Retry-After` header; 401/403 → cookie stale, prompt for refresh
8. ToS note: Document in code that this is an unofficial, unsupported access method and may break

---

## References

- **strava-offline** (Python, open-source): https://github.com/camjccc/strava-offline
- **Strava official API pricing** (as of 2026-06): https://developers.strava.com/docs/getting-started/#account-terms
- **Strava GDPR export**: Settings → My Account → Privacy → Download Account
- **Apple HealthKit export**: iPhone Settings → Privacy → Health → Export Records
- **Garmin Connect API**: https://developer.garmin.com/ (free tier for personal use)
