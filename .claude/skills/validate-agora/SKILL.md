---
name: validate-agora
description: Run the Comprehension Compiler against the agora repo and report Phase 1 extraction stats — route recovery rate, node counts, and gaps. Use to measure progress toward Phase 1 acceptance criteria.
disable-model-invocation: true
---

Run the compiler against agora and compare static extraction vs. oracle introspection.

If the user passes a path with $ARGUMENTS, use that as the agora repo path. Otherwise ask the user for the path before proceeding.

Steps:

1. Activate venv:
   ```bash
   source /data/FASTDelphos/.venv/bin/activate
   ```

2. Run the compiler against agora:
   ```bash
   python -m cc compile <agora-path> --out /tmp/cc-agora-out
   ```

3. If the `--oracle` flag is implemented, also run:
   ```bash
   python -m cc compile <agora-path> --out /tmp/cc-agora-out --oracle
   ```
   The oracle imports `app.routes`/`app.openapi()` to produce ground truth (only valid for agora — boots clean in dev; never use in Corporate context).

4. Report:
   - Route recovery rate: static endpoints found vs. oracle ground truth
   - Node counts by type: `endpoint`, `function`, `model`, `table`
   - Edge counts by type: `handles`, `uses_model`, `calls`, `reads`, `writes`
   - Gap report: count by kind (`missing_artifact` vs. `unresolved_dynamic`)
   - Any errors or extraction failures

5. Flag the 3 eval questions from ESQUEMA_POC.md and note whether the current graph can answer them:
   1. Where is `cost_usd` written? (via `writes → messages` + `via`)
   2. What does the synthesize endpoint touch? (endpoint → handles → calls* → reads/writes)
   3. Where are character prompts assembled? (the hardest — measures call graph completeness)
