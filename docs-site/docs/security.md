# Security Model

MaxOps is designed to run entirely on infrastructure you control. A few things are true by design — not bugs, but worth understanding before you deploy it anywhere beyond `localhost`:

## The backend API is unauthenticated

It's meant to sit behind `localhost` only. **Do not** bind it to `0.0.0.0` or expose it on a shared network without adding your own authentication/network controls in front of it. The Docker Compose setup publishes both services to `127.0.0.1` only, for exactly this reason.

## MaxOps can perform destructive AWS actions

Once you opt in via `MAXOPS_ENABLE_ACTIONS=true` *and* enable individual actions in Settings, MaxOps can stop, terminate, delete, or modify AWS resources. Both gates default to off on a fresh install. See [Concepts: Actions](concepts/actions.md).

## The onboarding flow creates a real IAM role

By default, the IAM role step provisions an actual read-only role in your AWS account using whatever local credentials/profile you give it. See [AWS Setup & Permissions](aws-setup.md) for the exact permissions granted, and for a path that creates nothing at all (register an existing role instead).

## Reporting a vulnerability

Please **do not** open a public GitHub issue. Use [GitHub's private vulnerability reporting](https://github.com/maxopsdev/maxops/security/advisories/new) for this repository instead. Full policy: [`SECURITY.md`](https://github.com/maxopsdev/maxops/blob/main/SECURITY.md) in the repository.
