# AWS Setup & Permissions

MaxOps needs AWS credentials to scan your account. You have two ways to provide them.

## Option 1: Configure credentials directly

Set one of these before starting MaxOps (see [Configuration](configuration.md)):

- `AWS_PROFILE` — a local AWS CLI profile
- `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`
- `AWS_USE_IAM_ROLE=true` + `AWS_ROLE_ARN` — for running the backend on EC2/ECS

## Option 2: Let the onboarding wizard create a read-only role for you

The onboarding wizard's IAM role step can provision a dedicated `MaxOpsReadOnlyRole` in your AWS account, scoped to `List`/`Get`/`Describe` permissions only — no write access, no CUR export setup, no remediation actions. It covers:

- EC2, EBS, VPC (instances, volumes, snapshots, flow logs, VPC endpoints)
- CloudWatch metrics and alarms, CloudWatch Logs
- RDS (including Performance Insights)
- S3 bucket configuration (not object contents)
- Auto Scaling, ECS, Lambda
- DynamoDB, Athena, Glue, Kinesis, Firehose
- ElastiCache, EFS, EMR, Redshift
- OpenSearch
- Cost Explorer pricing lookups and `sts:GetCallerIdentity`

This requires your local AWS credentials to already have `iam:CreateRole`/`iam:PutRolePolicy` permissions.

### Don't have IAM-write permissions?

If your credentials can't create IAM resources:

1. On the IAM role step, click **Download JSON** to get the exact read-only policy above.
2. Have someone with IAM access create a role from it (or point at any existing role with equivalent read permissions).
3. Paste that role's ARN into the **"Credentials can't create IAM roles?"** box on the same screen and click **Use this role**.

This path makes **no IAM write calls at all** — MaxOps only reads your caller identity (`sts:GetCallerIdentity`) and writes a local AWS CLI profile pointing at the role you gave it.

## What gets written locally

Either path results in a `MaxOpsReadOnlyRole` profile in your local `~/.aws/config` (or wherever `AWS_CONFIG_FILE` points), which the backend uses for subsequent scans.
