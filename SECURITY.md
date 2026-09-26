# Security Policy

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Instead, use [GitHub's private vulnerability reporting](https://github.com/maxopsdev/maxops/security/advisories/new) for this repository (Security tab → "Report a vulnerability"). This opens a private conversation with maintainers before anything is disclosed publicly.

Please include:
- A description of the vulnerability and its impact
- Steps to reproduce, or a proof of concept
- Affected version/commit

We'll acknowledge reports as soon as we can and keep you updated as we work on a fix.

## Scope and known design tradeoffs

MaxOps is designed to run entirely on infrastructure you control (your own machine, or your own server). Some things are true by design and are **not** vulnerabilities to report, but are worth understanding before you deploy it anywhere other than `localhost`:

- **The backend API is unauthenticated.** It's meant to sit behind `localhost` only. Do not bind it to `0.0.0.0` or expose it on a shared network without adding your own authentication/network controls in front of it.
- **MaxOps can perform destructive AWS actions** (stop/terminate/delete/modify resources) once you opt in via `MAXOPS_ENABLE_ACTIONS=true` and enable individual actions in Settings. This is off by default on a fresh install.
- The onboarding flow creates a **real read-only IAM role** in your AWS account using whatever local AWS credentials/profile you provide it.

If you find a way to escalate beyond these documented boundaries (e.g. bypass the `MAXOPS_ENABLE_ACTIONS` gate, or an injection vulnerability against AWS APIs), that's a real report — please send it through the private channel above.

## Supported versions

MaxOps does not yet have a formal release/support cadence. Please report against the latest commit on `main`.
