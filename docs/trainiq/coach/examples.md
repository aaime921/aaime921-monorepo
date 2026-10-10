# Example prompts

Five prompts QA can run against a sample `coach/` export (a hand-built folder
shaped like `aaime921/trainiq-data/coach/`), reading `grok-instructions.md` as
the LLM would. For (d) and (e), edit the sample `status.md` to match the
named fixture condition before running the prompt.

## (a) Peloton request

**Prompt:** "I'm going on the Peloton for 30 min."

**Files used:** `status.md`, `peloton_classes.md`, `load.md`, `recent.md`,
`last_done.md`.

**Expected kind of answer:** names one real class (title, instructor, class
type) from the 30-minute section of `peloton_classes.md`, with a one-line
reason referencing fitness/fatigue (`load.md`) and/or recent class balance.
Short, answer-first.

**Must not:** invent a class if the 30-minute section is missing or the file
says "class catalog unavailable" — in that case it must instead give a class
type and intensity, and say the catalog is unavailable.

## (b) Run request

**Prompt:** "Plan a run for tomorrow."

**Files used:** `last_done.md`, `performance.md`.

**Expected kind of answer:** a target duration, an intensity (HR zone from
rest 69 / max 181 with the method named, or a pace from `performance.md`),
and how many days since the last run.

**Must not:** give a pace or HR zone not derivable from the stated rest/max
HR or `performance.md`; must not skip saying when the last run was.

## (c) Weight progress

**Prompt:** "How is my weight going?"

**Files used:** `weight.md`.

**Expected kind of answer:** current trend/rate vs. the 0.5-1% body-weight-
per-week target, and progress vs. the 82 kg -> goal trajectory; if the trend
is outside the target range, says so and ties it to a training suggestion
(volume/intensity/recovery).

**Must not:** use a weigh-in flagged as implausible in the trend; must not
give a medical opinion.

## (d) Stale data -> refresh offer

**Fixture:** `status.md` last-run time set to 2 days ago, no `⚠️` lines.

**Prompt:** "What's my status?"

**Files used:** `status.md`.

**Expected kind of answer:** states the data is more than 24 hours old and
offers to create the `refresh` issue (one tap to confirm) before giving any
training/weight advice from the stale data.

**Must not:** give normal advice without first flagging staleness and
offering a refresh.

## (e) Strava-expired warning

**Fixture:** `status.md` contains the line
`⚠️ Strava cookie expired: Strava data not refreshed since <date>. Renew: …`.

**Prompt:** "I'm going on the Peloton for 45 min."

**Files used:** `status.md`, `peloton_classes.md`, `load.md`, `recent.md`,
`last_done.md`.

**Expected kind of answer:** quotes the `⚠️` line verbatim, including the
renewal pointer, **before** any Peloton-class advice; then answers the
Peloton request as in (a) using whatever data is available.

**Must not:** give the Peloton pick first and the warning afterward, or
omit the warning.
