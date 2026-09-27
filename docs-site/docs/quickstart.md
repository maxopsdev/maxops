# Quickstart (Docker)

**Prerequisites:** Docker and Docker Compose.

```bash
git clone https://github.com/maxopsdev/maxops.git
cd maxops
cp .env.example .env
docker compose up
```

Open:

- **http://localhost:3000** — the UI
- **http://localhost:8000/docs** — the API (Swagger)

The pricing database and app database are created/unpacked automatically on first start, and persist across restarts in a named Docker volume.

## Add AWS credentials

Edit `.env` before starting (or restart with `docker compose up` again after editing):

```bash title=".env"
# uncomment only the option you use
AWS_PROFILE=default
AWS_CONFIG_HOST_DIR=/Users/you/.aws   # macOS/Linux; Windows: C:/Users/you/.aws
# or
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

!!! warning "Don't leave settings blank"
    Comment out or delete what you aren't using. `AWS_PROFILE=` with nothing
    after it names a profile called empty rather than meaning "unset", and
    every AWS call fails with
    `ProfileNotFound: The config profile () could not be found`.

`AWS_PROFILE` alone isn't enough — the container needs your `~/.aws` files mounted in to read that profile. Set `AWS_CONFIG_HOST_DIR` and `docker-compose.yml` mounts it read-only automatically; omit it (the default) and a harmless empty placeholder is mounted instead.

You can skip this entirely and configure AWS credentials from the onboarding wizard's IAM role step instead. See [AWS Setup & Permissions](aws-setup.md).

## Stop / reset

```bash
docker compose down          # stop, keep your data
docker compose down -v       # stop and delete the persisted database + pricing DB volume
```

## Next steps

Follow the [Onboarding Walkthrough](onboarding.md), or jump straight to [Configuration](configuration.md) for the full environment variable reference.
