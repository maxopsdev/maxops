# AWS Setup & Permissions

MaxOps needs AWS credentials to scan your account. You have two ways to provide them.

## Option 1: Configure credentials directly

Set one of these before starting MaxOps (see [Configuration](configuration.md)):

- `AWS_PROFILE` — a local AWS CLI profile
- `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`
- `AWS_USE_IAM_ROLE=true` + `AWS_ROLE_ARN` — for running the backend on EC2/ECS

## Using AWS IAM Identity Center (SSO)

SSO works, but it needs two things most setups miss: the login happens on your
**host machine**, and the container needs a writable mount to renew the token.
There is no browser inside the container, so it can never start an SSO login
itself — it can only read the token your host has already obtained.

### 1. Log in on the host

```bash
aws sso login --profile my-sso-profile
```

This writes a token to `~/.aws/sso/cache/`. Confirm it worked:

```bash
aws sts get-caller-identity --profile my-sso-profile
```

If that fails, stop here — MaxOps cannot do better than your own CLI.

### 2. Point MaxOps at your AWS directory

In `.env` at the repo root:

```bash
AWS_CONFIG_HOST_DIR=/Users/you/.aws     # Windows: C:/Users/you/.aws
AWS_CONFIG_MOUNT_MODE=rw
```

`AWS_CONFIG_HOST_DIR` mounts `~/.aws` into the container, which is how it sees
both your profile and the cached token.

`AWS_CONFIG_MOUNT_MODE=rw` matters for SSO specifically. The mount is read-only
by default so MaxOps can never rewrite your real AWS config. botocore renews a
cached SSO token **in place** in the 15 minutes before it expires, and on a
read-only mount that write fails — which surfaces as scans breaking a quarter
of an hour before the token was due to expire. `rw` lets the renewal stick.

Leave it read-only if you would rather re-run `aws sso login` by hand instead.

### 3. Start MaxOps and pick the profile

```bash
docker compose up --build -d
```

Your SSO profile appears in the onboarding **AWS profile** picker, and in
**Settings → Scan Credentials** afterwards. Both `sso-session` and the older
inline `sso_start_url` styles are supported.

### 4. When the token expires

SSO tokens are short-lived. With `rw` set, botocore renews them silently for as
long as the refresh token lasts. After that — typically daily — run
`aws sso login` on the host again. No restart is needed: MaxOps re-reads
credentials on every scan.

### Do you even need the read-only role?

Usually not, with SSO. Your permission set already has read access, and the
`MaxOpsReadOnlyRole` adds an `sts:AssumeRole` hop that Identity Center does not
permit by default:

```
AccessDenied ... assumed-role/AWSReservedSSO_.../you is not authorized to
perform sts:AssumeRole on resource .../MaxOpsReadOnlyRole
```

Two separate reasons it fails, and both need an Identity Center administrator:

- Permission sets do not grant `sts:AssumeRole` unless one is added.
- IAM stores a trust policy's role principal as that role's **unique principal
  ID**. Re-provisioning a permission set recreates the role with a new ID, so a
  trust policy written earlier stops matching — permanently, and silently. If
  the `AWSReservedSSO_..._<hash>` in the error does not match your current role,
  this is what happened.

So pick your SSO profile under **Settings → Scan Credentials** and skip the role.
MaxOps no longer switches you onto a role it could not assume: if the check
fails after setup, it keeps scanning with the profile you signed in as and says
why.

### Troubleshooting

| What you see | Cause |
|---|---|
| "no valid cached token" | No `aws sso login` on the host, or `AWS_CONFIG_HOST_DIR` is unset so the container cannot see `~/.aws/sso/cache` |
| Profile missing from the picker | `AWS_CONFIG_HOST_DIR` unset, or compose was run from outside the repo root so `.env` was never read |
| Worked, then failed ~15 minutes before expiry | `AWS_CONFIG_MOUNT_MODE=rw` is not set, so the token renewal could not be written |
| `Create Role` fails with "invalid principal" | Expected for SSO: those roles live under a path. MaxOps resolves the real ARN via `iam:GetRole`, so grant that permission |

Check what the container actually sees:

```bash
docker compose exec backend sh -c 'ls /root/.aws/sso/cache; python -c "import boto3;print(boto3.Session().available_profiles)"'
```

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
