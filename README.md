# MaxOps

MaxOps is a **local-first FinOps control plane** for AWS: it scans your account, flags cost-optimization opportunities across 112 checks spanning 32 resource types (EC2, RDS, S3, EBS, ElastiCache, DynamoDB, ASG, and more), and can take corrective action — resize, stop, delete, snapshot — when you tell it to.

It runs entirely on your own machine or infrastructure. There is no MaxOps-hosted backend, no telemetry, and no data sent anywhere except directly between your browser, this app, and AWS.

- `maxops-backend/` — FastAPI backend (the whole product)
- `maxops-frontend/` — React + TypeScript UI
- `maxops-mcp/` — Model Context Protocol server, exposes MaxOps to MCP-compatible AI clients
- `maxops-plugin/` — browser extension that overlays MaxOps data on the AWS Console
- `maxops.dev/` — marketing site

## ⚠️ Security note before you run this

The backend API is **unauthenticated** by design (it's meant to run on `localhost`, not on a shared network) and, once you opt in, **can perform destructive AWS actions** (stopping/terminating/deleting resources). Two things follow from that:

1. Do not bind the backend to `0.0.0.0` or expose it beyond your own machine without adding your own auth/network controls in front of it.
2. Real (non-test) actions are off by default behind `MAXOPS_ENABLE_ACTIONS=false`. Nothing in MaxOps can modify or delete an AWS resource until you explicitly set that to `true` — see [Configuration](#configuration) below.

## Quickstart (Docker)

The fastest way to try MaxOps:

```bash
git clone https://github.com/maxopsdev/maxops.git
cd maxops
cp .env.example .env   # optional: add AWS credentials, see comments in the file
docker compose up
```

Then open **http://localhost:3000** and follow the in-app onboarding wizard. The backend API is at **http://localhost:8000** (Swagger docs at `/docs`).

No Docker? See [SETUP_GUIDE.md](SETUP_GUIDE.md) for the manual Python/Node setup.

## Configuration

MaxOps reads configuration from environment variables (see [maxops-backend/env.example](maxops-backend/env.example) for the full list with comments). The two you're most likely to touch:

| Variable | Default | What it does |
|---|---|---|
| `AWS_PROFILE` / `AWS_ACCESS_KEY_ID`+`AWS_SECRET_ACCESS_KEY` | none | Credentials MaxOps uses to scan/act on your AWS account |
| `MAXOPS_ENABLE_ACTIONS` | `false` | Global gate for real AWS actions (stop/terminate/delete/modify). Per-check and per-action toggles in Settings only take effect once this is `true` |

## Pricing Database

MaxOps ships a curated AWS pricing database as a bundled compressed artifact under `maxops-backend/pricing_artifacts/`. Nothing is downloaded in the background — the Docker image unpacks it automatically on first start, and the manual setup path unpacks it via a script or the onboarding screen's "Extract Pricing Database" action. See [SETUP_GUIDE.md](SETUP_GUIDE.md).

## Cost data: list prices vs. your actual bill

Out of the box, MaxOps prices findings from **public list prices**. That is enough to rank what is wasteful, and it needs nothing beyond read-only access.

If you hold Reserved Instances or Savings Plans, list prices overstate what you actually pay — sometimes by a lot. Connect your AWS **Cost and Usage Report** and MaxOps prices findings from what each resource really cost, discounts included.

It is optional, off by default, and everything works without it. To set it up, open **Cost Data** in the sidebar. The page walks four steps and always shows which one you are on:

1. Create the Cost and Usage Report export
2. Wait for AWS to deliver the first file (up to 24 hours)
3. Summarise the billing data locally
4. Turn on cost-data pricing

Two things to know before starting:

- **It needs more than read-only access.** Creating an export and running the summary queries is beyond the scan role, which is read-only by design. The page can create a separate `MaxOpsCostDataRole` scoped to just this job, or you can point it at a profile of your own. Scanning keeps using the read-only role either way.
- **Athena bills per terabyte scanned.** The page estimates the cost and shows it before you confirm. For most accounts a summary is a few cents or less, but the estimate is there so it is never a surprise.

Everything is computed and stored on your own machine. No billing data is sent anywhere.

## Documentation

Full docs: **[maxops.dev/docs.html](https://maxops.dev/docs.html)**

- [SETUP_GUIDE.md](SETUP_GUIDE.md) — local setup (Docker and manual)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the backend is laid out and how a scan flows end to end
- [docs/ADDING_A_CHECK.md](docs/ADDING_A_CHECK.md) — write a new cost-optimization check
- [CONTRIBUTING.md](CONTRIBUTING.md) — contributing guidelines, including license terms
- [maxops-mcp/README.md](maxops-mcp/README.md) — MCP server setup for AI-assistant integrations

## Getting help

- **Questions, ideas, "is this supposed to work like this?"** —
  [Discussions](https://github.com/maxopsdev/maxops/discussions). Answers there
  are public and searchable, so the next person with the same question finds it.
- **Bugs and feature requests** —
  [Issues](https://github.com/maxopsdev/maxops/issues).
- **Security vulnerabilities** — not a public issue. See [SECURITY.md](SECURITY.md).

## License

MaxOps is licensed under the **Apache License 2.0** — free to use, modify and distribute, including commercially, with an express patent grant. See [LICENSE](LICENSE) for the full text and [NOTICE](NOTICE) for attribution.
