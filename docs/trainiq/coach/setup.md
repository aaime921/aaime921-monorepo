# Setting up the Grok fit coach

Grok's mobile app is the primary way the BO uses this; the web app is covered
too but is best effort — the GitHub-connector and one-tap-confirm behaviour
below has only been confirmed on mobile.

Grok's exact custom-instruction length limit has not been verified. If
pasting both parts of `grok-instructions.md` together is rejected as too
long, paste only Part A and send Part B as your first message instead (see
that file for details).

No token or secret value ever goes into these instructions or into Grok —
the GitHub connector handles its own authorization.

## Mobile (primary)

1. Open the Grok app, go to its connector/integration settings, and connect
   GitHub if it isn't already connected.
2. When granting repo access, select `aaime921/trainiq-data` (the private
   data repo). Read access is enough for the coach to work; it also needs
   permission to create an issue, used only for the one-tap `refresh` flow
   below.
3. Create a new Grok Project for the fit coach.
4. Open `docs/trainiq/coach/grok-instructions.md` in this repo and paste
   Part A into the Project's custom instructions. Paste Part B too if the
   Project accepts the combined length; otherwise keep Part B to send as
   your first message in the Project each time you start a new chat there.
5. Send a first test prompt, e.g. "What's my status?" Grok should read
   `coach/status.md` from `trainiq-data` and answer, surfacing any `⚠️`
   warning first.
6. If the data is stale, Grok will ask to create a `refresh` issue. In the
   mobile app this needs **one tap to confirm** before the issue is
   actually created — Grok cannot create it silently. After confirming,
   wait for Grok to report the result comment on that issue before trusting
   its next answer.

## Web (secondary, best effort)

Same steps as mobile: connect the GitHub connector in Grok's web settings,
grant access to `aaime921/trainiq-data`, create a Project, and paste the
instructions. The GitHub connector and the `refresh` one-tap confirmation
are confirmed working on the mobile app; on web they are expected to behave
the same way but have not been separately verified — if the write-confirmation
step looks different on web, treat whatever confirmation step the web UI
shows as the equivalent of the mobile "tap to confirm."

## Troubleshooting

- **`trainiq-data` isn't listed when granting repo access:** the GitHub
  account connected to Grok doesn't have access to the private repo, or the
  connector needs to be reconnected/re-authorized from scratch.
- **Grok gives an empty or generic reply instead of reading the data:**
  remind it in the chat to read `coach/status.md` (and the relevant file for
  the question) from `aaime921/trainiq-data` before answering; re-paste the
  instructions if it still doesn't use the connector.
- **Answers look stale:** check `coach/status.md` directly in the repo for
  the actual last-run time; if it's genuinely old, trigger a `refresh`
  yourself (open an issue titled `refresh` in `trainiq-data`) rather than
  waiting on Grok to notice.
