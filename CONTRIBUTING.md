# Contributing to SmplCache

SmplCache is an Apache 2.0 open-source project. We welcome contributions, but we hold a very strict standard for architectural and mathematical honesty.

## The Professional Bar
Before submitting a Pull Request, ensure your changes adhere to these invariants:

1. **No silent under-invalidation**: If a SQL shape intersects a CDC write, and you do not have mathematical proof of exact repairability, you MUST emit an `invalidate` or `unsupported` decision.
2. **No repair claim without evidence**: Theorem 7 dictates the exact CDC evidence required to repair aggregates. Do not assume evidence exists. Validate it.
3. **No unsupported SQL feature quietly accepted**: If the `extractor` encounters a construct it does not understand, it must explicitly reject it. 

## Claim Discipline
We do not make heuristic guesses and call them optimizations. We compute boundaries and certify them. 
If you add a new feature, you must also add tests that explicitly demonstrate the limits of that feature.

## Development Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
pytest
black .
```
