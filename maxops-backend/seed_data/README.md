# Seed Data - Default Policies

This directory contains default policies that are automatically loaded into the database on first run.

## Policy Files

All policies are stored as YAML files following the Cloud Custodian policy format.

### Included Policies

1. **idle-ec2-instances.yaml** - Find idle EC2 instances (7+ days)
2. **unattached-ebs-volumes.yaml** - Find unattached EBS volumes
3. **old-ebs-snapshots.yaml** - Find snapshots older than 90 days
4. **stopped-instances.yaml** - Find stopped EC2 instances
5. **unused-elastic-ips.yaml** - Find unassociated Elastic IPs
6. **idle-rds-instances.yaml** - Find idle RDS instances
7. **oversized-instances.yaml** - Find oversized EC2 instances
8. **missing-cost-center-tag.yaml** - Find resources missing CostCenter tag
9. **unused-load-balancers.yaml** - Find unused load balancers
10. **development-idle-resources.yaml** - Find idle dev environment resources
11. **unused-nat-gateways.yaml** - Find unused NAT gateways

## How It Works

1. On first application startup, the system checks if the database is empty
2. If empty, it automatically loads all YAML files from this directory
3. Policies are created with `active` status and ready to use
4. Subsequent startups skip seeding if policies already exist

## Adding New Seed Policies

1. Create a new YAML file in this directory
2. Follow the Cloud Custodian policy format
3. Include required fields: `name`, `description`, `resource`, `filters`, `execution`
4. The policy will be automatically loaded on next first run

## Manual Seeding

You can manually trigger seeding via API:

```bash
# Seed if database is empty
POST /api/v1/seed/policies

# Force re-seed (deletes existing policies)
POST /api/v1/seed/policies?force=true
```

## Policy Format

Each policy file should follow this structure:

```yaml
name: Policy Name
description: What this policy does
resource: aws.ec2  # or aws.rds, aws.ebs, etc.
filters:
  - type: idle
    days: 7
    metrics: [CPUUtilization]
# Note: Execution mode is selected by user when running the policy
```

## Notes

- All seed policies are set to `active` status by default
- Policies are only seeded if the database is empty (unless force=true)
- Policy names must be unique
- Resource type is automatically extracted from the policy YAML

