# Manual Installation

Use this if you're developing on the backend/frontend directly, or don't want to use Docker.

## Prerequisites

- Git
- Python 3.13
- Node.js 20+ and npm
- AWS credentials (optional but recommended for real AWS checks)

## 1. Clone the repository

```bash
git clone https://github.com/maxopsdev/maxops.git
cd maxops
```

## 2. Backend

```bash
cd maxops-backend
python -m venv .venv
```

Activate the virtual environment:

=== "Windows (PowerShell)"

    ```powershell
    .venv\Scripts\Activate.ps1
    ```

=== "macOS/Linux"

    ```bash
    source .venv/bin/activate
    ```

Install dependencies and create your env file:

```bash
pip install --upgrade pip
pip install -r requirements.txt
cp env.example .env          # Windows PowerShell: Copy-Item env.example .env
```

Unpack the bundled pricing database:

```bash
python staging_pricing/unpack_pricing_db.py
```

Run the backend:

```bash
uvicorn app.main:app --reload --port 8000
```

!!! danger "Don't add `--host 0.0.0.0`"
    The API is unauthenticated and can perform destructive AWS actions once enabled. Keep it bound to localhost unless you've put your own auth/network controls in front of it. See [Security Model](security.md).

Health check: `http://localhost:8000/health` · Swagger docs: `http://localhost:8000/docs`

## 3. Frontend

Open a new terminal:

```bash
cd maxops-frontend
npm install
npm run dev
```

Open **http://localhost:3000**. The dev server proxies `/api` to `http://localhost:8000` automatically (see `vite.config.ts`) — no `VITE_API_URL` needed unless your backend runs somewhere else.

## Next steps

[Configuration](configuration.md) for the full environment variable reference, or jump into the [Onboarding Walkthrough](onboarding.md).
