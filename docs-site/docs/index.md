# MaxOps

MaxOps is a **local-first FinOps control plane** for AWS. It runs entirely on your own machine or infrastructure — there is no MaxOps-hosted backend, no telemetry, and nothing sent anywhere except directly between your browser, this app, and AWS.

## What it does

- **Scans** your AWS account across 112 cost-optimization checks spanning 32 resource types (EC2, RDS, S3, EBS, ElastiCache, DynamoDB, ASG, Lambda, and more)
- **Rightsizes** compute and database resources using deterministic, testable algorithms — not a black box
- **Acts**, when you tell it to: stop, terminate, delete, modify, snapshot — gated off by default until you opt in
- **Integrates** with AI assistants via an MCP server, and overlays cost data directly in the AWS Console via a browser extension

## Get started

<div class="grid cards" markdown>

- :material-docker: **[Quickstart (Docker)](quickstart.md)**

    The fastest way to try MaxOps — one command, no dependencies beyond Docker.

- :material-language-python: **[Manual Installation](installation.md)**

    For development, or if you'd rather not use Docker.

</div>

## Before you run this

MaxOps's backend API is **unauthenticated by design** — it's meant to run on `localhost`, not on a shared network — and, once you opt in, it **can perform destructive AWS actions**. Read the [Security Model](security.md) page before deploying it anywhere beyond your own machine.

## License

MaxOps is licensed under the **Apache License 2.0** — free to use, modify and distribute, including commercially. See [License](license.md).
