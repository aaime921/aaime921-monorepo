# TrainIQ Engineering Constitution

*Read this before changing any code. If a change conflicts with a principle below, the principle wins — raise it as a discussion, not a silent override.*

**Committed to the repository:** as a Product Owner-designated pre-RC documentation item, completing the chain `Constitution → ADRs → Implementation → Tests`. Previously existed only in design-phase conversation history, alongside the ADR archive it was committed together with (see `docs/adr/INDEX.md`).

---

## Why this document exists

Two design phases and eight implementation epics produced thirty-eight ADRs. No single implementer — human or AI — should need to re-read all of them to remember what TrainIQ is supposed to be. This document is the distilled version: not a technical spec, but the identity the specs were written to protect.

---

## The eight principles

**1. Never fabricate missing data.**
If a value can't be honestly known — a training load, a confidence score, a recommendation — it is marked Unknown, not guessed, defaulted to zero, or interpolated. Unknown, Missing, and Zero are three distinct states and must never collapse into one another. *(ADR-016, ADR-019)*

**2. Never fabricate an unsupported recommendation.**
The same rule as above, applied to the system's final output, not just its inputs. A recommendation without sufficient evidence is not a weaker recommendation — it's a fallback message and nothing else. *(Milestone E, ADR-030)*

**3. Confidence is deterministic.**
No confidence score shown to the athlete is ever generated, estimated, or self-reported by a language model. It is always computed from Evidence Density and Confidence Propagation, and the model's only role regarding it is to narrate a number it did not produce. *(ADR-017, ADR-024, ADR-025)*

**4. The LLM is an explainer, not a decision authority.**
It synthesizes and communicates. It does not decide, does not invent quantitative claims, and does not reason from anything except the structured evidence it was explicitly handed. This is what keeps TrainIQ stable across model changes, vendor changes, and years of LLM evolution — the architecture doesn't move when the narrator changes. *(ADR-026, ADR-028)*

**5. Evidence before intelligence.**
Every decision the Coach makes must trace back to a chain of verifiable evidence, built before the model is ever invoked. The model intervenes only after that chain already exists — never to construct it. *(ADR-034)*

**6. Validation is a hard gate, not a warning.**
An unvalidated recommendation is never shown, never persisted, never used — no exceptions, no "just this once for testing." A gate that can be silently bypassed is not a gate. *(ADR-030)*

**7. Quality gates come before features are called done.**
A feature is finished when its output passes the quality bar defined for its layer — not when it produces output at all. This applies uniformly, from a connector's first sync to the Coach's final explanation. *(ADR-035)*

**8. The Evidence Package is a stable internal contract.**
It is the object nearly the entire system converges on. Treat its schema like a versioned external API: documented, tested independently, and never changed incompatibly without deliberate, visible review. *(ADR-036)*

---

## One-sentence summary

**TrainIQ prefers uncertainty over false certainty.**

Every principle above is a specific application of that one sentence, discovered independently at five different layers of the system and never once contradicted. If a future change would make TrainIQ *more* certain-sounding at the cost of being *less* honestly certain, that change is wrong — regardless of how reasonable it seems in isolation.

---

## What this document is not

It is not a replacement for the ADRs (006–038, see `docs/adr/INDEX.md`), the five Phase 0 milestones, the five Phase 0B milestones, or the two-tier Implementation Roadmap with its Quality Gates. Those remain the source of truth for *how*. This document exists only to answer *why*, quickly, for anyone — including a future instance of Claude — about to touch this codebase without having read all of the above first.

## Post-implementation note (added at Phase 0B acceptance)

Every principle above was written during the design phase, before implementation existed to test it against. Across Epics 0–7, none was found wrong. Several were independently re-derived from scratch when a new layer of the system hit the same underlying problem a different way — Principle 1's discipline reappeared unprompted in Epic 6's training-load engine (`training_load = NULL`, never estimated) and in Epic 7's forward-only profile behavior, neither of which existed when this Constitution was first written. That convergence, not the act of writing the document, is the real evidence these are load-bearing principles rather than aspirational ones.
