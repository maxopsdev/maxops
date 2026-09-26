# Onboarding Walkthrough

The app is hard-gated behind onboarding — every route redirects to `/onboarding` until you've completed it. It's five steps:

## 1. Welcome / pricing database

An intro to MaxOps, plus a gate on the bundled pricing database. Docker unpacks this automatically; the manual setup path shows an **Extract Pricing Database** button if it hasn't been unpacked yet.

## 2. IAM role

Pick a local AWS profile, then either have MaxOps create a dedicated read-only IAM role in your account, or register an existing role's ARN if your credentials can't create IAM resources. See [AWS Setup & Permissions](aws-setup.md) for both paths in detail.

## 3. Account settings

Set your environment name, AWS account, region(s), and the idle/threshold day counts checks use to decide what counts as "unused." These autofill from the account resolved in step 2.

## 4. Run checks

MaxOps runs all registered checks against the account/region you configured — the first real scan of your AWS environment. A progress bar with a rough ETA tracks this; with 112 checks it typically takes a couple of minutes depending on account size.

## 5. Results

A summary of total/completed/failed checks, resources found, and potential savings, with a per-check breakdown. From here, **View Dashboard** takes you into the main app.

## What's next

- [Concepts: Checks](concepts/checks.md) — how a check works and how to write one
- [Concepts: Actions](concepts/actions.md) — enabling remediation
- The Settings screen lets you turn individual checks and actions on/off after onboarding, independent of the global `MAXOPS_ENABLE_ACTIONS` gate
