# agent-pipeline

An automated software delivery pipeline: Claude Code cloud routines, each
covering one role (Business Analyst, Technical Architect, Developer, QA),
hand a GitHub issue between each other in sequence until it's done — no
manual copy-paste between agents. Team Lead doesn't do pipeline work itself —
it watches for merged PRs and resumes any issue that was blocked waiting on
one. Triage runs first on every new issue, tagging it `complexity:simple` or
`complexity:complex` so Developer and QA run on a cheaper model when the
issue doesn't need full-strength reasoning (see "Cost tiering" in
[`docs/PIPELINE.md`](docs/PIPELINE.md)).

See [`docs/PIPELINE.md`](docs/PIPELINE.md) for how handoffs work, and
`docs/roles/` for each role's responsibilities.

## Starting new work

Open an issue using the "Feature request" template. The Business Analyst
routine picks up new, unlabeled issues automatically.

## Hello World CLI

A minimal smoke-test CLI that prints a greeting.

Run:

```sh
python3 src/hello.py [NAME]
```

Prints `Hello <NAME>! [YYYY-MM-DD HH:MM:SS]`, or `Hello world! [YYYY-MM-DD HH:MM:SS]`
if no name is given, with the current timestamp.

Pass `--shout` to print the greeting in all caps, e.g.
`python3 src/hello.py --shout Alice` prints `HELLO ALICE! [YYYY-MM-DD HH:MM:SS]`.

Test:

```sh
python3 -m unittest discover -s tests
```
