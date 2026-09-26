#!/bin/sh
set -e

PRICING_DB_PATH="${PRICING_DATABASE_PATH:-/app/data/maxops_pricing.db}"
mkdir -p "$(dirname "$PRICING_DB_PATH")"

if [ ! -f "$PRICING_DB_PATH" ]; then
  echo "Unpacking bundled pricing database to $PRICING_DB_PATH ..."
  python staging_pricing/unpack_pricing_db.py --db-path "$PRICING_DB_PATH" || \
    echo "Warning: pricing DB unpack failed. Retry from the onboarding screen's 'Extract Pricing Database' action."
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
