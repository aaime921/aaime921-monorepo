# TrainIQ

Personal macOS training-data aggregation and evidence-based coaching system.

**Start here:** [`CONSTITUTION.md`](CONSTITUTION.md) — the eight founding principles, and why they exist.
Then [`docs/adr/INDEX.md`](docs/adr/INDEX.md) — the complete architecture decision record (38 ADRs, design-phase and implementation-phase).
Then [`BACKLOG.md`](BACKLOG.md) — what's open, what's closed, and what's intentionally deferred, with reasons.

Committed as of the Phase 0B acceptance review — previously these existed only in design-phase conversation history. The Phase 0/0B milestone reports and the Tier A/B Implementation Roadmaps remain outside the repository as of this writing (see `docs/adr/INDEX.md`'s closing note); the Constitution and ADR archive above are the durable, in-repository summary of the reasoning behind them.

## Setup

    python3 -m venv .venv && source .venv/bin/activate
    pip install -e ".[dev]"

## Debug: capture a real Eufy device-data response

    python3 scripts/debug_eufy.py

Requires Eufy already connected (run the app once first). Writes the
complete, unmodified JSON response to `debug/eufy_download_response.json`
— read-only, does not sync or persist anything. See the script's own
docstring for exactly what it does and does not do.

## Run tests

    pytest

## Establish or re-check the performance baseline

    python3 scripts/benchmark.py --out docs/benchmarks/$(date +%Y-%m-%d).md

See `scripts/benchmark.py`'s module docstring for what these numbers do and do not represent — in short, compare against a PREVIOUS run on the SAME machine, never against a different machine's numbers.

## Regenerate the synthetic dataset

    python3 -m trainiq.synthetic_dataset

## Build the macOS app (macOS only — see scripts/build.sh)

    TRAINIQ_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
        scripts/build.sh
