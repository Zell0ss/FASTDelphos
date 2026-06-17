---
name: test
description: Run pytest for this project and show results. Use after implementing or changing any extractor, schema, or gap logic.
---

Activate the virtual environment if not already active, then run pytest:

```bash
source /data/FASTDelphos/.venv/bin/activate
cd /data/FASTDelphos
pytest -v
```

Show the full output. Summarize at the end: total tests, passed, failed. If any tests fail, list the failing test names and the first assertion error for each.
