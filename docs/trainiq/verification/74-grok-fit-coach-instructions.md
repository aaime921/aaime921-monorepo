# 74 – Verification: Grok fit-coach instructions (PR #84)

Docs-only PR (3 files in `docs/trainiq/coach/`; nothing under `projects/trainiq/`). pytest not installed in the QA sandbox and not applicable. Verified by reading the files against each AC, a char count of the paste blocks (A=1540, B=1438, total 2978) and a secret grep (none).

| AC | Result | Reason |
|---|---|---|
| 1 | Pass | Role and athlete facts stated; `profile.md` named as source of truth |
| 2 | Pass | Reads `status.md` first; >24 h/unreadable → offer `refresh` issue titled exactly `refresh`, wait for result comment |
| 3 | Pass | `⚠️` lines quoted verbatim with renewal pointer before anything else |
| 4 | Pass | Nearest 20/30/45/60, uses load/recent/last_done, title+instructor+type+reason, prefers new, no invented class if catalog unavailable |
| 5 | Pass | Duration, HR zone (%HRR from 69/181) or pace from `performance.md`, days since last run |
| 6 | Pass | `weight.md`, 0.5–1 %/wk (~0.4–0.8 kg), ties training to off-range trend, doctor for health |
| 7 | Pass | `-`/unreadable = unknown, never invent |
| 8 | Pass | Answer-first, ≤~6 lines, no tables; length limit flagged unverified with split instructions (Part A / Part B) |
| 9 | Pass | `setup.md` covers mobile+web connect, `trainiq-data` selection, Project paste, test prompt, one-tap refresh; no secrets |
| 10 | Pass | Exactly 5 examples (a–e) with files and expected answer; cover all required cases |
| 11 | Pass | Only status, profile, recent, last_done, load, weight, performance, peloton_classes referenced |

**Note (non-blocking, from Developer/Architect):** #73's real `status.md` wording isn't verified; instructions match "last-run time" and `⚠️` lines loosely. Re-check once #73 merges.

Verdict: all pass.
