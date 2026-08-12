# Astera Customer AI

Implementation repository for the current Customer AI runtime defined in the Astera Notion canon.

## Architecture
`Japanese Short-QA -> KAGRRA Bridge -> Astera v8 Bridge -> Shared Grounding -> 1 Work / 3 resident roles -> Integration -> Targeted Repair -> FinalAnswerComposer -> Satisfaction/Completion Gates`

Notion is the design/specification canon. GitHub records implementation, tests, commits and CI evidence. Astera v8, KAGRRA and AMATERAS Ω runtime bodies are not duplicated here; adapters/bridges connect those responsibilities.

## Runtime status
The implementation is fail-closed until concrete v8/KAGRRA adapters, canonical/live providers and a Master-decided trained model/revision are supplied. `config/model.yaml` intentionally keeps undecided model values unset.

## Verification
`python -m compileall -q app.py runtime training evaluation tests`
`python -m pytest -q`
`ruff check --select F app.py runtime training evaluation tests`

## Release gate
Release evidence requires at least 200 unseen scenarios, 11 scenario classes, 98% User Need Resolution, 98% Answer Satisfaction, 99% Critical resolution, 100% false-premise correction, zero unsupported/legacy/secret/unexecuted-completion violations, and a 95% Wilson lower confidence bound of at least 98%.
