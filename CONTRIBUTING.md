# Contributing to MaxOps

Thanks for considering a contribution.

## License terms, up front

MaxOps is licensed under the **Apache License 2.0** ([LICENSE](LICENSE)) — you can use, modify, distribute and sell it, commercially or otherwise, provided you keep the copyright and licence notices and state any changes you made. It also grants an express patent licence from contributors.

Per section 5 of the licence, any contribution you intentionally submit for inclusion is licensed under these same terms, unless you explicitly state otherwise. No separate contributor agreement is required.

## Development setup

See [SETUP_GUIDE.md](SETUP_GUIDE.md) for getting the backend and frontend running locally (Docker or manual).

## Running tests safely

`maxops-backend/tests/integration/` provisions **real, billable AWS resources** via the `maxops` AWS profile — it is not a mock or simulator. Two independent gates protect against running it by accident:

1. `pytest.ini` defaults to `-m "not integration"`, so a bare `pytest` run skips it.
2. The integration fixture additionally requires `MAXOPS_RUN_AWS_INTEGRATION_TESTS=1` in the environment.

**Never run a broad or keyword-based pytest invocation** across `maxops-backend/tests/` — `-k` matches on file/function names, not markers, so a generic keyword like `"ec2"` or `"scan"` can match a file under `tests/integration/` and silently create real AWS resources. This has happened before and left orphaned RDS/EC2/EBS resources that had to be cleaned up manually.

Safe default:

```bash
cd maxops-backend
pytest tests/test_the_file_you_touched.py
# or, scoped:
pytest tests/ -k "your_narrow_keyword"   # -m "not integration" default still applies
```

Only run `tests/integration/` deliberately, with both `-m integration` and `MAXOPS_RUN_AWS_INTEGRATION_TESTS=1` set on purpose.

For the frontend: `npm run type-check`, `npm run lint`, and `npm test` from `maxops-frontend/`.

## Adding a new check

Writing a new cost-optimization check is the most common first contribution — see [docs/ADDING_A_CHECK.md](docs/ADDING_A_CHECK.md) for the walkthrough (create the module, register `CheckMetadata`, add a payload fixture and test — no real AWS account needed).

## The `rightsizers/` purity boundary

`rightsizers/` (EC2, ASG, RDS, ElastiCache) is a **pure** package: deterministic functions over already-normalized telemetry, with no AWS calls and no database access. `app/services/{ec2,asg,rds,elasticache}_rightsizer.py` are the **impure** adapters that fetch data (via `app/adapters/aws/`) and feed it into the pure layer.

Keep it that way. If you're changing rightsizer logic, ask: does this belong in the pure evaluation layer (testable with plain data in, plain data out) or the impure fetch/adapt layer? Don't let AWS calls creep into `rightsizers/`.

## Architecture reference

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) covers the backend's layered structure and the end-to-end flow from onboarding through scan, view, rightsize, tune, and act.

## Reporting bugs / suggesting features

Open a GitHub issue with:
- **Bugs:** clear title, repro steps, expected vs. actual behavior, environment (OS, Python/Node version), and whether Docker or manual setup.
- **Features:** the use case and benefit, and a proposed approach if you have one.

Security issues should **not** go through a public issue — see [SECURITY.md](SECURITY.md).

## Pull requests

1. Fork the repository and create a feature branch.
2. Make your changes, with tests where applicable (see "Running tests safely" above).
3. Run `pytest` (backend) and `npm run type-check && npm test` (frontend) before opening the PR.
4. Use clear commit messages (`feat: ...`, `fix: ...`, `docs: ...`, `refactor: ...`, `test: ...`).
5. Open the PR against `main` and describe what changed and why.

## Coding standards

**Backend (Python):** PEP 8, type hints, `black` for formatting, `flake8` for linting, `mypy` for type checking.

**Frontend (TypeScript/React):** functional components with hooks, existing code style, `eslint` clean before committing.
