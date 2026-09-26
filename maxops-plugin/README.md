# MaxOps Browser Plugin

Browser extension package that overlays local MaxOps EC2 cost and optimization context inside the AWS console.

## Project Layout

```text
maxops-plugin/
  shared/              Shared extension pages, scripts, and styles
  manifests/           Browser-specific manifest files
  scripts/build.ps1    Local build script
  dist/                Generated browser-specific extensions
```

Current targets:

- `chrome`: Manifest V3
- `edge`: Manifest V3
- `firefox`: Manifest V2

## Data Source

The plugin does not call the internet. It reads EC2 inventory from the local MaxOps backend:

```text
GET http://localhost:8000/api/v1/inventory/ec2/overview
```

The default backend URL is `http://localhost:8000`. It can be changed from the extension options page, but only local HTTP origins are accepted (`localhost` or `127.0.0.1`).

## What It Shows

When an AWS console page contains EC2 instance IDs, the content script adds inline MaxOps chips with:

- Estimated monthly cost
- Potential monthly savings
- Current MaxOps finding or healthy status

The extension is intentionally static and dependency-free, so it can be built and loaded without installing packages or using internet access.

## Build

Build all browser targets:

```powershell
.\scripts\build.ps1
```

Build one target:

```powershell
.\scripts\build.ps1 -Browser chrome
.\scripts\build.ps1 -Browser edge
.\scripts\build.ps1 -Browser firefox
```

## Local Installation

1. Start `maxops-backend`.
2. Build the target browser extension.
3. Open the browser extension management page.
4. Enable developer mode.
5. Load the generated folder from `dist/<browser>`.
6. Visit the AWS console EC2 instance list or detail page.

## Permissions

The extension is scoped to:

- AWS console pages for content injection
- `http://localhost:*/*`
- `http://127.0.0.1:*/*`

No remote scripts, fonts, images, or third-party services are used.
