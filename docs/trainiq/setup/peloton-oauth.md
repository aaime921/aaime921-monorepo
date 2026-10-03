# Setting up Peloton's OAuth+PKCE auth path

Issue: [#7](https://github.com/aaime921/trainiq/issues/7)

This is the one-time, human-performed step that lets `PelotonConnector`
authenticate via OAuth authorization-code+PKCE against Peloton's own
Auth0 tenant, instead of needing a manually-extracted bearer token pasted
in roughly every 48 hours. It coexists with, and does not replace, the
existing automated email/password login (confirmed broken, see
`BACKLOG.md` BL-008) and the manual-bearer-token recovery path
(`submit_manual_recovery()`), which stay available as a fallback.

This step is deliberately manual and is not scripted end-to-end: a real
person has to log into Peloton in a browser and copy an authorization
code out of the address bar. That part is not automated on purpose (see
issue #7) — only the token exchange and every refresh after it are.

## Why JavaScript has to be blocked first

`members.onepeloton.com`'s own frontend consumes the OAuth redirect's
`code` query parameter as part of its normal login flow, as soon as the
callback page loads — racing any attempt to manually copy it out of the
address bar. Blocking JavaScript for that one site stops its callback
page from running that consumption logic, so the redirect fails to load
(expected) and the `code` parameter sits in the address bar long enough
to copy.

This detail comes directly from the BO's own live testing while writing
issue #7. `BACKLOG.md`'s BL-008 entry does **not** document this — it's
an unrelated, already-closed finding about the workout-list endpoint
shape (issue #5) — so issue #7 itself, and this document, are the
record of it.

## Steps

1. **Block JavaScript for `members.onepeloton.com`** before logging in:
   - Chrome: `chrome://settings/content/javascript` → "Add" under
     "Not allowed to use JavaScript" → enter `https://members.onepeloton.com`.
   - This is a per-site setting; it does not affect any other site, and
     you can remove it again afterward (`chrome://settings/content/javascript`
     → remove the entry).
2. **Run the setup script**:
   ```
   python3 scripts/setup_peloton_oauth.py
   ```
   It prints a reminder to complete step 1, then an authorization URL.
3. **Open that URL in the browser where JavaScript is blocked for
   `members.onepeloton.com`**, and log in with your normal Peloton
   credentials if prompted.
4. After login, the page will attempt to redirect and **fail to load**
   — this is expected, since JavaScript is blocked. Look at the address
   bar: it will contain a URL like
   `https://members.onepeloton.com/callback?code=<long-string>&...`.
   Copy the value of the `code` parameter (everything between `code=`
   and the next `&`).
5. **Paste that value back into the running script** when it prompts for
   it. On success, the script stores the initial access and refresh
   tokens and prints a confirmation.
6. Optional: remove the JavaScript block for `members.onepeloton.com`
   added in step 1 — it's only needed during this one-time setup, not
   for ongoing use.

## After setup

Once this completes, `PelotonConnector.authenticate()` tries the OAuth
path first on every call, refreshing the access token silently (and
persisting the rotated refresh token every time — Peloton invalidates
the previous one on each use) with no further manual steps, for as long
as the refresh token stays valid.

If the OAuth path ever fails (the refresh token is revoked or rejected —
this uses an unofficial, reverse-engineered `client_id` with no written
permission from Peloton, and could stop working without notice), the
connector falls back to the existing manual-bearer-token recovery path
automatically. Re-run this script to restore OAuth once that happens.

## Troubleshooting

- **"The authorization code was rejected"**: codes are single-use and
  short-lived. Restart the script from step 2 and get a fresh code —
  don't reuse one that already failed.
- **The redirect actually loads a page instead of failing**: JavaScript
  probably isn't blocked for `members.onepeloton.com` yet, or was
  blocked for the wrong URL — double check step 1, then start over from
  step 2 (a stale code cannot be reused).
