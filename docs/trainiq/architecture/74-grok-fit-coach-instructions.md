# 74 – Grok fit-coach instructions and setup guide: design

Requirements: `docs/trainiq/requirements/74-grok-fit-coach-instructions.md` (11 ACs). Docs only; nothing under `projects/trainiq/` changes. Export files: `docs/trainiq/architecture/71-coach-export.md`, `72-peloton-class-candidates.md`. `status.md` and the `refresh` flow: `73-cloud-sync-trainiq-data.md`.

## Answers to the BA's open questions
- **Examples location:** separate `docs/trainiq/coach/examples.md` (keeps `setup.md` short and gives QA one file to run). AC10 "5 example prompts" is met by that file.
- **Grok instruction length limit:** unverified; design for ≤ 4,000 characters (~1,000 tokens) in `grok-instructions.md`, which is the common custom-instruction ceiling. `setup.md` flags the limit as unverified and gives a split: Part A (role, safety, data rules) in custom instructions; Part B (Peloton/run/weight playbooks) as a Project instruction or a first message. The file is written as two clearly marked blocks so the split is a copy of one block.
- **`status.md` wording:** the renderer (`render_status.py`, #73) is not on `main` yet, so exact field names are not fixed. Instructions therefore must not depend on exact labels: "find the last-run time (UTC) in `status.md`" and "any line starting with `⚠️`". Only the Strava message is fixed by #73 AC7: `⚠️ Strava cookie expired: Strava data not refreshed since <date>. Renew: <steps>`. **Developer must re-check the real `status.md` (or `render_status.py`) once #73 is merged and align the wording; if it differs materially, comment and use `needs:routing`.**

## Approach
Plain Markdown, no code. The instructions are a short, imperative prompt written for an LLM that has GitHub read tools: ordered "every conversation" steps first, then playbooks per request type, then hard rules. Reference files by name relative to `coach/` in repo `aaime921/trainiq-data`; do not restate file formats. Athlete facts are in the prompt (AC1) with "if `profile.md` differs, trust `profile.md`".

## Files (all new, under `docs/trainiq/coach/`)
1. `grok-instructions.md`: a one-line intro for the BO, then fenced block(s) to paste. Sections inside the paste:
   - **Role** (athlete facts, goal −10 kg from 82 kg, no injuries, Peloton + runs + walks; no medical claims, refer to a doctor).
   - **Start of every conversation:** (1) read `coach/status.md`; (2) quote every `⚠️` line verbatim with its renewal pointer *before* any advice; (3) last run > 24 h ago or unreadable → offer `refresh` (create issue titled exactly `refresh` in `trainiq-data`, tell the BO to tap confirm, wait for the result comment, re-read files) — advise on old data only if the BO declines, labelling it with its age.
   - **Peloton N min:** nearest of 20/30/45/60 min section in `peloton_classes.md`; weigh `load.md` (TSB low → easier/recovery, high → harder), class-type balance in `recent.md`, `last_done.md`; prefer `new`/not recently done; answer title, instructor, type + one-line reason. "Class catalog unavailable" or section unavailable → say so, give class type + intensity only, no invented class.
   - **Run:** duration, intensity as HR zone (rest 69 / max 181; say which method, e.g. HRR %) or pace from `performance.md`, and days since the last run from `last_done.md`.
   - **Weight:** from `weight.md`; target 0.5–1 % body weight/week (~0.4–0.8 kg at 82 kg); if actual trend is outside, say so and tie volume/intensity/recovery to it; flagged weigh-ins (ADR 039) are not trends.
   - **Never invent:** `-` or missing file = "unknown". **Style:** answer first, ≤ ~6 short lines, no tables.
2. `setup.md`: sections Mobile (primary) / Web: connect GitHub connector in Grok settings, grant `aaime921/trainiq-data` (private, read access; issue-write needed only for the `refresh` tap-to-confirm), create a Project, paste instructions, first test prompt (`What's my status?`), what the one-tap `refresh` confirmation looks like, troubleshooting (repo not listed, empty reply, stale data). No tokens or secret values (AC9). Include the length-limit note above.
3. `examples.md`: exactly 5 prompts, each as: prompt / files it must use / expected kind of answer / must-not. Covering: (a) "I'm going on the Peloton for 30 min", (b) "Plan a run for tomorrow", (c) "How is my weight going?", (d) stale `status.md` (> 24 h) → refresh offer before advice, (e) `status.md` with the Strava-expired warning → quoted first, then advice. Prompts (d)/(e) name the fixture condition ("status.md last run 2 days ago"), so QA can edit a sample export.

## Task breakdown
1. Confirm #71, #72 merged (done per BA) and #73 merged; if #73's `status.md` exists, align wording (see above).
2. Write `grok-instructions.md` (count characters; keep ≤ 4,000 across the paste blocks).
3. Write `setup.md`, then `examples.md`.
4. Self-check against the 11 ACs; grep new files for `token`/`ghp_`/`github_pat` (must be none) and for file names not in the export list (AC11).

## Test strategy (QA)
Docs-only: review each AC against the files; character count; run the 5 examples against a sample export (a hand-built `coach/` folder; `status.md` variants for d/e) by reading the instructions as the LLM would. Live Grok behaviour is BO/ops verification, not QA's.

## Risks
- Grok tool behaviour (connector read, issue creation, one-tap confirm) is only BO-confirmed on mobile; web is best effort, say so in `setup.md`.
- LLM adherence to "≤ 6 lines" and "warnings first" is probabilistic; instructions put those rules at the very top and repeat them in a closing checklist.
- Hard-coded athlete facts go stale (FTP, weight); mitigated by "trust `profile.md`".
