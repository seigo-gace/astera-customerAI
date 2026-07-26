# Controlled Customer AI implementation review record

## Fixed correction

- Customer AI does not execute Astera itself.
- No `kagura-engine.js`, Astera adapter, Astera bootstrap, or Astera environment variable is allowed.
- The implementation only reuses internal engineering structures cultivated in Astera/KAGRRA: deterministic scripts, structured skills, V8 parallel workers, state capsules, evidence gates, recovery, and routine bots.
- A language model is an exchangeable engine inside the Control Core. It cannot own routing, tools, action execution, facts, state, or completion.

## Pass 1 — Controlled execution and modular-catalog reuse

**Prompt:** Review whether routine work is owned by Script, structured Skill, V8 workers, and bots before a language engine is considered. Verify that modular-catalog patterns are adapted without runtime dependency.

Applied corrections:

- Added `ControlledExecutionCore`.
- Added machine-readable Execution Contract, State Capsule, Evidence list, Stop Conditions, and Uncertainty Rule.
- Added an ACTIVE-only `$` structured `SkillRegistry`.
- Adapted worker lifecycle, timeout, crash recovery, and one-time regeneration from `astera-worker-lifecycle-pool`.
- Adapted deterministic human-context signals from `astera-human-context-reading`.
- Adapted deterministic routing and normalization patterns from `astera-domain-template-routing`.
- Kept safe JSON, logging outbox, and provider adapter boundaries as design contracts.

## Pass 2 — Engine boundary

**Prompt:** Review whether the language engine can be called directly or as a standalone support agent.

Applied corrections:

- Removed direct model invocation from `CustomerAIService`.
- Added `ControlledLanguageEngine.execute(packet)` with mandatory packet keys.
- The engine is blocked unless the Control Core explicitly allows one call.
- Verified Evidence and structured Skill results are mandatory.
- Deterministic draft is generated before engine invocation.
- Engine output cannot execute actions and must cite only provided Evidence IDs.
- Model/provider identity is removed by the Output Guard.

## Pass 3 — V8 parallelism and bots

**Prompt:** Review light processing, routine processing, recovery, and CPU limits.

Applied corrections:

- Added a persistent Node.js Worker Thread pool.
- Runs normalization, human-context, routing, question decomposition, entity extraction, and safety detection in parallel.
- Worker timeout causes one bounded regeneration; recursive worker spawning is forbidden.
- Added deterministic recovery, question-insight aggregation, and optional Notion KB sync bots.
- Bots never call the language engine.

## Pass 4 — Security and completion

**Prompt:** Review secrets, PII, prompt injection, internal implementation leakage, unverified action claims, evidence coverage, and completion ownership.

Applied corrections:

- Output Guard and V8 verification are both required.
- Completion is based on Evidence, coverage, and blocking violations—not model self-evaluation.
- Private implementation patterns and engine identity are rejected.
- User statements remain KB candidates and never become confirmed product facts without source evidence.

## Verification commands

```bash
python scripts/review.py --all
pytest -q
npm test
```
