---
name: session-end
description: Update tomorrow.md with a session handoff — what was completed, decisions made, next task, and blockers. Run at the end of every work session.
disable-model-invocation: true
---

Review the current conversation and write a session handoff to `tomorrow.md` at `/data/FASTDelphos/tomorrow.md`.

Overwrite the file with the following structure:

```markdown
# Session handoff — <date>

## Completed
- <bullet per thing actually finished>

## Key decisions
- <decision> — <one-line rationale>

## Patterns established
- <any code pattern or convention discovered or locked in>

## Next task
<Single most important thing to do next — be specific, not vague>

## Gotchas / blockers
- <anything that slowed progress or will need attention next session>
```

Be concrete and specific. "Implemented endpoint extractor" is better than "worked on extractors." The next session's Claude has no conversation context — this file is all it gets.
