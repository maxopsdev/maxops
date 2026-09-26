# Contributing

MaxOps is licensed under the **Apache License 2.0**. Per section 5 of the licence, contributions you submit are licensed under the same terms. See [License](license.md).

## The most approachable first contribution

Writing a new [check](concepts/checks.md) — see `docs/ADDING_A_CHECK.md` in the repository. About 20 minutes, no real AWS account required.

## Running tests safely

`maxops-backend/tests/integration/` provisions **real, billable AWS resources**. Never run a broad or keyword-based `pytest` invocation across `tests/` — a generic keyword can match a file under `tests/integration/` and silently create real AWS resources. Two independent gates protect the default `pytest` run, but a targeted `-k` can still bypass them if it happens to match an integration test's name.

Safe default:

```bash
cd maxops-backend
pytest tests/test_the_file_you_touched.py
```

## Full guide

The complete contributing guide — dev setup, coding standards, PR process, the `rightsizers/` purity boundary — lives in [`CONTRIBUTING.md`](https://github.com/maxopsdev/maxops/blob/main/CONTRIBUTING.md) in the repository root.
