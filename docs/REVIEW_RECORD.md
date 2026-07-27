# Astera Customer AI implementation review record

## Corrected implementation basis

The previous main-branch simplification preserved conversation continuity but reduced the support runtime to one broad KB query, one model response, and one small consistency check. That did not satisfy the required document decomposition, per-question retrieval, evidence binding, answer planning, controlled composition, and KB feedback loop.

This branch keeps the useful bounded conversation cache and private HF/Gateway boundaries, while rebuilding the response path around dedicated Customer AI responsibilities.

## Review 1 — Reuse without lazy addition

Question: Did the implementation merely add new modules beside the old one-pass path?

Result:

- `ConversationCore` now calls `SupportRuntime.prepare()` before any model decision.
- The old single retrieval query is no longer the answer path.
- Search is planned per `QuestionTask` and executed with bounded parallelism.
- Evidence is bound back to the task it supports.
- The answer blueprint is the single source for deterministic fallback and model composition.
- Existing conversation cache and Gateway/HF boundaries are reused because their responsibilities remain correct.

## Review 2 — No generic all-in orchestration

Question: Did the branch restore the overbuilt generic Skill Registry, many routine bots, or a general worker pool?

Result:

- No generic `SkillRegistry` exists.
- No `RoutineBotSupervisor` exists.
- No generic `WorkerPool` exists.
- The new module is purpose-built for Japanese support preparation.
- V8 performs bounded deterministic analysis and validation only.
- The implementation does not execute the Astera engine.

## Review 3 — Lightweight model boundary

Question: Can the model choose facts, routes, searches, tasks, actions, or completion?

Result:

- The model receives only a prepared Support Packet.
- Question tasks, search plans, evidence, and blueprint are prepared first.
- Exact deterministic answers do not invoke the model.
- Multi-task composition may invoke the model once.
- Validation failures allow one targeted repair only.
- A second failure returns to the deterministic blueprint.
- Unknown task or evidence references fail validation.

## Review 4 — Multi-turn answer continuity

Question: Does follow-up support remain coherent after adding structured task processing?

Result:

- Existing goal, active topic, confirmed details, and recent turns remain bounded and persistent.
- Answered question IDs and a question ledger are now persisted.
- Reusable evidence summaries and the previous blueprint are persisted.
- Same-session processing remains protected by the session lease.
- Follow-up analysis receives the compact state rather than an unlimited transcript.

## Review 5 — KB feedback safety

Question: Are user questions used to improve the KB without becoming unverified facts?

Result:

- Questions are normalized and redacted.
- Session IDs are hashed.
- Duplicate candidates use a deterministic fingerprint.
- Candidate records include tasks, search plans, matched KB IDs, gap types, and validation failures.
- Every candidate requires approval.
- Auto-publication is false.
- No direct Notion write is performed by `FeedbackStore`.

## Review 6 — Required verification

The branch is accepted only after all of the following pass on GitHub Actions:

```bash
python scripts/review.py --all
ruff check . --select E9,F63,F7,F82
pytest -q
npm test
```

HF deployment is a separate release action and must not occur until tests pass, production secrets are configured, the private Space and bucket are verified, Gateway delivery/callback succeeds, and Cloudflare end-to-end tests pass.
