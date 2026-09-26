# MaxOps Backend

Python 3.13 / FastAPI backend for the MaxOps FinOps platform — 112 cost-optimization checks across 32 AWS resource types, inventory scanning, rightsizing recommendations, and (opt-in) automated remediation actions.

## Setup

See the root [SETUP_GUIDE.md](../SETUP_GUIDE.md) for Docker and manual setup instructions.

## Related

- **Frontend**: [`../maxops-frontend`](../maxops-frontend)
- **MCP server**: [`../maxops-mcp`](../maxops-mcp)

## Documentation

- [Architecture](../docs/ARCHITECTURE.md)
- [Adding a check](../docs/ADDING_A_CHECK.md)

## Pricing DB

The curated street-pricing database ships as a bundled compressed artifact in `pricing_artifacts/`. Users unpack it explicitly with:

```bash
python staging_pricing/unpack_pricing_db.py
```

There is no background download. The packaged artifact and checksum are part of the repository, and the raw `maxops_pricing.db` remains untracked. Regeneration and packaging steps live in [staging_pricing](./staging_pricing/README.md).

If the database has not been unpacked yet, the onboarding screen shows an `Extract Pricing Database` action that runs the same local unpack step from the bundled repository artifact. The Docker image runs this automatically on first start.
