# Customer AI conversation-quality review record

## Corrected focus

The purpose is not to install every reusable mechanism. The purpose is to improve the lightweight model's answer accuracy across the first question and multiple follow-up turns.

The runtime must preserve:

- what the user is trying to solve
- the current topic
- confirmed details already supplied
- unanswered points
- the latest bounded conversation turns
- the KB pages relevant to the continuing conversation

## Removed as unnecessary

- generic structured-skill registry
- generated `$customer-ai.*` skills
- large generic execution contracts
- routine bot supervisor
- question-insight automation
- persistent Worker Thread pool
- six-way analysis worker split

These mechanisms added weight without directly improving the user's multi-turn answer continuity.

## Remaining processing path

```text
Current message
+ bounded session context
→ lightweight V8 turn analysis
→ context-expanded KB search with short query cache
→ lightweight model response using recent turns and KB
→ V8 consistency verification
→ bounded session context update
```

## Cache rules

- Session context is cached in memory for fast follow-ups.
- The same context is persisted to the mounted private bucket for restart recovery.
- Only a bounded number of recent turns is retained.
- KB query results use a short bounded cache.
- No unlimited transcript or unlimited KB result cache is allowed.

## Answer-quality checks

1. A follow-up without a repeated product name must keep the previous active topic.
2. The original user goal must remain available to the model.
3. Already answered questions must not be asked again.
4. New details in a follow-up must be merged into confirmed details.
5. The KB search query must include the current message and prior goal when necessary.
6. The model may cite only KB IDs supplied in the current packet.
7. A response that drifts to another topic must be rejected.
8. If the model is unavailable, the confirmed KB answer remains usable.

## Verification commands

```bash
python scripts/review.py --all
pytest -q
npm test
```
