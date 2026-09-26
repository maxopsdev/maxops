# Troubleshooting

## Frontend can't reach the backend

Confirm the backend is running on `:8000`. For a manual (non-Docker) setup, also confirm `VITE_API_URL` isn't pointed somewhere stale.

## CORS errors

The backend only allows `http://localhost:3000` and `http://localhost:5173` as origins. Match one of those, or edit `allow_origins` in `maxops-backend/app/main.py`.

## Port conflicts

Change the port in the `uvicorn`/`npm run dev` command (manual setup), or the port mapping in `docker-compose.yml` (Docker setup).

## Python dependency issues (manual setup)

Delete `.venv`, recreate it, and reinstall:

```bash
rm -rf .venv
python -m venv .venv
pip install -r requirements.txt
```

## Docker build is slow or fails on `pandas`/`duckdb`/`pyarrow`

These ship prebuilt wheels for Python 3.13 on `linux/amd64` and `linux/arm64`. On an unusual platform the build may need to compile from source — slower, but should still succeed.

## Pricing database shows "not ready"

- **Docker:** check `docker compose logs backend` for an unpack error on first start; retry by clicking **Extract Pricing Database** on the onboarding Welcome screen.
- **Manual:** run `python staging_pricing/unpack_pricing_db.py` from `maxops-backend`.

## An action returns 403

Two independent gates must both be satisfied: `MAXOPS_ENABLE_ACTIONS=true` in the environment, **and** the specific action enabled in Settings. See [Concepts: Actions](concepts/actions.md).

## Still stuck?

Open an issue on [GitHub](https://github.com/maxopsdev/maxops/issues) — see [Contributing](contributing.md) for what to include.
