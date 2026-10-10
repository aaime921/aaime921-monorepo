# 74 – Grok fit-coach instructions and setup guide

Docs only. Builds on merged work: coach export (#71/#78, #72/#82, #79) and cloud sync + `coach/status.md` + `refresh` issue flow (#73, design: `docs/trainiq/architecture/73-cloud-sync-trainiq-data.md`). Nothing under `projects/trainiq/` changes.

## Summary
The BO wants Grok (mobile app is the primary path, web secondary) to act as their fit coach: an endurance-training and weight-loss expert that reads the TrainIQ export in the private repo `aaime921/trainiq-data`, under `coach/`. This ticket delivers the text to paste into a Grok Project / custom instructions, a setup guide, and example prompts QA can check against a sample export.

## Scope
In: `docs/trainiq/coach/grok-instructions.md`, `docs/trainiq/coach/setup.md`, example prompts (in a third file or a section of setup; Architect decides).
Out: code, export or workflow changes, live Grok testing (ops), medical advice.
Facts from BO: Grok GitHub connector works in the mobile app; Grok write actions (creating the `refresh` issue) need one tap to confirm.

## Acceptance criteria
1. `grok-instructions.md` is pasteable as-is and states the role: training and weight-loss coach for one athlete (male, 50, FTP 166 W, HR rest 69 / max 181, goal −10 kg from 82 kg, no injuries; Peloton bike plus runs and walks). Athlete facts are also pointed to `profile.md` as the source of truth if they differ.
2. Every conversation: first read `coach/status.md`. If its last-run time is older than 24 h (or unreadable), offer a `refresh`: create an issue titled exactly `refresh` in `trainiq-data`, then wait for the result comment before advising.
3. Any `⚠️` line in `status.md` (e.g. "⚠️ Strava cookie expired: …") is quoted to the athlete **before** any advice, with its renewal pointer.
4. Peloton request ("I'm going on the Peloton for N min"): picks a real class from `peloton_classes.md` for the nearest listed duration (20/30/45/60), using `load.md` (CTL/ATL/TSB), recent class-type balance (`recent.md`) and `last_done.md`; names title, instructor, class type; gives a one-line reason; prefers `new` or not-recently-done classes. If the catalog says "class catalog unavailable", says so and gives a class type/intensity instead, with no invented class.
5. Run request: gives target duration, intensity (HR zone from rest 69 / max 181, or pace from `performance.md`), and when the last run was (`last_done.md`).
6. Weight loss: uses `weight.md`; keeps the target rate about 0.5–1 % of body weight per week; if the actual trend is outside that range, says so and ties training advice (volume/intensity/recovery) to it. No medical claims; points to a doctor for health concerns.
7. Never invents data: a value shown as `-` or a missing file is reported as unknown.
8. Answers are brief and mobile-friendly (short answer first, few lines, no wide tables). Instructions fit Grok's custom-instruction length limit, or the guide states how to split them (flag the limit as unknown if not verified).
9. `setup.md` covers, for mobile (primary) and web: connecting GitHub in Grok, selecting `aaime921/trainiq-data` (private repo, read access), creating the Project / pasting the instructions, first test prompt, and confirming the one-tap `refresh` issue creation. No secret or token values in the doc.
10. Exactly 5 example prompts, each with the expected kind of answer and the files it must draw on. Together they cover: (a) Peloton N min, (b) a run, (c) weight progress/rate, (d) stale data → refresh offer, (e) a `⚠️` Strava-expired warning surfaced first. QA can run them against a sample export.
11. Docs reference only files that exist in the export (`status.md`, `profile.md`, `recent.md`, `last_done.md`, `load.md`, `weight.md`, `performance.md`, `peloton_classes.md`).

## Open questions (non-blocking, Architect)
- Whether the example prompts live in `setup.md` or a separate `examples.md`.
- Grok custom-instruction length limit (unverified).
- Exact `status.md` field wording for last-run time: read it from the #73 design/real file rather than assuming.
