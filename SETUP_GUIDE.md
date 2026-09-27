# MaxOps Local Setup Guide

This guide covers how to run MaxOps locally after cloning from GitHub.

## Option A: Docker (recommended)

**Prerequisites:** Docker and Docker Compose.

```bash
git clone https://github.com/maxopsdev/maxops.git
cd maxops
cp .env.example .env
docker compose up
```

Open **http://localhost:3000** for the UI and **http://localhost:8000/docs** for the API. The pricing database and app database are unpacked/created automatically on first start and persist across restarts in a named Docker volume.

Edit `.env` before starting (or restart with `docker compose up` again after editing) to set AWS credentials:

```bash
# .env -- uncomment only the option you use
AWS_PROFILE=default        # if you want to use a local AWS CLI profile
AWS_CONFIG_HOST_DIR=/Users/you/.aws   # required alongside AWS_PROFILE -- see below
# or
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

Comment out or delete the settings you aren't using rather than leaving them
blank. `AWS_PROFILE=` with nothing after it is not "unset" — it names a profile
called empty, and every AWS call then fails with
`ProfileNotFound: The config profile () could not be found`.

`AWS_PROFILE` alone isn't enough in Docker — the container needs your `~/.aws` files mounted in to actually read that profile. Set `AWS_CONFIG_HOST_DIR` to your local AWS config directory (macOS/Linux: `/Users/you/.aws`; Windows: `C:/Users/you/.aws`) and `docker-compose.yml` mounts it read-only automatically — no editing the compose file needed. Omit it and an empty placeholder is mounted instead, which is harmless if you're using access keys or configuring credentials from the onboarding UI.

To stop: `docker compose down` (add `-v` to also delete the persisted database/pricing-DB volume and start fully fresh).

Skip to [First Run Flow](#first-run-flow) below.

## Option B: Manual (Python + Node)

Use this if you're developing on the backend/frontend directly, or don't want to use Docker.

### 1. Prerequisites

- Git
- Python 3.13
- Node.js 20+ and npm
- AWS credentials (optional but recommended for real AWS checks)

### 2. Clone the repository

```bash
git clone https://github.com/maxopsdev/maxops.git
cd maxops
```

### 3. Backend

```bash
cd maxops-backend
python -m venv .venv
```

Activate the virtual environment:

- Windows (PowerShell): `.venv\Scripts\Activate.ps1`
- macOS/Linux: `source .venv/bin/activate`

Install dependencies and create your env file:

```bash
pip install --upgrade pip
pip install -r requirements.txt
cp env.example .env          # Windows PowerShell: Copy-Item env.example .env
```

Minimum `.env` values to verify:

- `DATABASE_URL=sqlite:///./maxops.db`
- `AWS_REGION=us-east-1`
- `AWS_PROFILE=<your-profile>` (or set access keys instead)

Unpack the bundled pricing database:

```bash
python staging_pricing/unpack_pricing_db.py
```

Run the backend:

```bash
uvicorn app.main:app --reload --port 8000
```

**Do not add `--host 0.0.0.0`** — the API is unauthenticated and can perform destructive AWS actions once enabled; keep it bound to localhost unless you've put your own auth/network controls in front of it.

Health check: `http://localhost:8000/health` · Swagger docs: `http://localhost:8000/docs`

### 4. Frontend

Open a new terminal:

```bash
cd maxops-frontend
npm install
npm run dev
```

Open **http://localhost:3000**. The dev server proxies `/api` to `http://localhost:8000` automatically (see `vite.config.ts`); no `VITE_API_URL` needed unless your backend runs somewhere else.

## First Run Flow

1. Open the frontend.
2. Follow the onboarding wizard: extract the pricing database (Docker does this for you), create/select a read-only IAM role, set environment/account/region/thresholds.
3. Let it run checks, then review results and land on the dashboard.

See the in-app onboarding screens for details — there's no separate account/login step, MaxOps runs entirely against your own AWS credentials.

## AWS Credentials Setup

Pick one:

- `AWS_PROFILE` (recommended locally) — reads from your existing `~/.aws/credentials`
- `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`
- IAM role-based credentials (for running the backend itself on EC2/ECS)

The onboarding wizard's IAM role step needs your credentials to already have `iam:CreateRole`/`iam:PutRolePolicy` permissions to provision the read-only scanning role it creates for you. If they don't, download the read-only policy JSON from that screen, have someone with IAM access create a role from it (or point at an existing read-only role), and paste the role's ARN into the "Credentials can't create IAM roles?" box instead — MaxOps won't attempt any IAM writes on that path.

## Troubleshooting

- **Frontend can't reach backend:** confirm the backend is running on `:8000`, and (manual setup only) that `VITE_API_URL` isn't pointed somewhere stale.
- **CORS errors:** the backend only allows `http://localhost:3000` and `http://localhost:5173` as origins — match one of those, or edit the `allow_origins` list in `app/main.py`.
- **Port conflicts:** change the port in the `uvicorn`/`npm run dev` command, or the port mapping in `docker-compose.yml`.
- **Python dependency issues (manual setup):** delete `.venv`, recreate it, and reinstall (`pip install -r requirements.txt`).
- **Docker build is slow/fails on `pandas`/`duckdb`/`pyarrow`:** these ship prebuilt wheels for Python 3.13 on `linux/amd64` and `linux/arm64`; if you're on an unusual platform the build may need to compile from source, which is slower but should still succeed.

## Useful Commands

From `maxops-backend`: `python -m compileall app`, `pytest tests/<file>.py` (see [CONTRIBUTING.md](CONTRIBUTING.md#running-tests-safely) before running anything broader).

From `maxops-frontend`: `npm run dev`, `npm run type-check`, `npm test`.
