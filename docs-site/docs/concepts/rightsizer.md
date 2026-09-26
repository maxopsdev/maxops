# Rightsizer

The Rightsizer (`/rightsizer`) gives deeper, trend-aware sizing recommendations for EC2, ASG, RDS, and ElastiCache, beyond what a single check's threshold can express.

## The purity boundary

`rightsizers/` is a **pure** package: deterministic functions over already-normalized telemetry, with no AWS calls and no database access. `app/services/{ec2,asg,rds,elasticache}_rightsizer.py` are the **impure** adapters — they fetch data through `app/adapters/aws/` and feed it into the pure layer.

This split means the sizing logic itself is trivially unit-testable with plain data fixtures — no AWS account, no mocking framework, no database. If you're contributing to rightsizer logic, keep AWS calls out of `rightsizers/`.

## Classification

Recommendations are grouped into:

- **ACTIONABLE** — high confidence, safe to act on
- **CONDITIONAL** — likely correct, but depends on a factor MaxOps can't fully verify (e.g. an assumption about traffic patterns)
- **OPPORTUNITY** — a savings opportunity exists, but the signal is weaker
- **DEFERRED** — not enough data yet (e.g. insufficient metric history)

See `docs/ARCHITECTURE.md` in the repository for the full data flow from scan through recommendation.
