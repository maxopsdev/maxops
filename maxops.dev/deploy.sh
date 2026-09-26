#!/usr/bin/env bash
# Publish maxops.dev to S3 + CloudFront.
#
# Two things this handles that a bare `aws s3 sync` does not:
#
#  1. Per-type Cache-Control. A plain sync leaves objects with whatever headers
#     they had, so HTML could end up cached for a year.
#
#  2. Cache busting. CSS/JS filenames are not content-hashed, so a returning
#     visitor's browser would keep serving its cached copy until max-age
#     expired -- a CloudFront invalidation clears the CDN, never the browser.
#     So we stamp a content hash onto every stylesheet/script reference
#     (style.css -> style.css?v=ab12cd34) in a staging copy before upload.
#     A changed file becomes a new URL and is fetched immediately; an unchanged
#     one keeps its URL and stays cached. Source files are never modified.
#
#   ./deploy.sh          build, upload, invalidate
#   ./deploy.sh --dry    show what would change, touch nothing
set -euo pipefail

BUCKET="maxops-dev-site"
DISTRIBUTION_ID="EAV34M7QR0SN"
PROFILE="${AWS_PROFILE:-voxdev}"
SITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE="$SITE_DIR/.deploy-stage"

DRY=""
[[ "${1:-}" == "--dry" ]] && DRY="--dryrun"

cd "$SITE_DIR"
echo "==> Staging $SITE_DIR"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -r assets "$STAGE"/ 2>/dev/null || true
cp ./*.html ./*.css ./*.js "$STAGE"/

# --- stamp content hashes onto asset references -----------------------------
for f in "$STAGE"/*.css "$STAGE"/*.js; do
  [[ -e "$f" ]] || continue
  base="$(basename "$f")"
  hash="$(sha1sum "$f" | cut -c1-8)"
  for html in "$STAGE"/*.html; do
    # ./name.ext -> ./name.ext?v=hash  (only unversioned refs)
    sed -i "s|\(href=\"\./\)$base\(\"\)|\1$base?v=$hash\2|g; s|\(src=\"\./\)$base\(\"\)|\1$base?v=$hash\2|g" "$html"
  done
  echo "    $base -> ?v=$hash"
done

echo "==> Uploading to s3://$BUCKET (profile: $PROFILE)"
cd "$STAGE"

sync_group() {
  local desc="$1" cache="$2" ctype="$3"; shift 3
  echo "  - $desc"
  aws s3 sync . "s3://$BUCKET/" --profile "$PROFILE" $DRY \
    --exclude "*" "$@" \
    --cache-control "$cache" \
    --content-type "$ctype" \
    --no-progress
}

# Images: stable filenames, cache hard. Split by type -- a single assets/*
# rule would stamp image/jpeg onto the SVG logos, and a browser will not
# render an SVG served as a JPEG.
sync_group "photos" "public,max-age=31536000,immutable" "image/jpeg"                     --include "assets/*.jpg"
sync_group "vectors" "public,max-age=31536000,immutable" "image/svg+xml"                 --include "assets/*.svg"
sync_group "pngs"    "public,max-age=31536000,immutable" "image/png"                     --include "assets/*.png"
# CSS/JS: safe to cache hard now -- every reference to them is version-stamped.
sync_group "css"    "public,max-age=31536000,immutable" "text/css; charset=utf-8"        --include "*.css"
sync_group "js"     "public,max-age=31536000,immutable" "text/javascript; charset=utf-8" --include "*.js"
# HTML: short. It carries the version pointers, so it must stay fresh.
sync_group "html"   "public,max-age=300,must-revalidate" "text/html; charset=utf-8"      --include "*.html"

cd "$SITE_DIR"
if [[ -n "$DRY" ]]; then
  echo "==> Dry run, no invalidation issued. Staging kept at $STAGE"
  exit 0
fi
rm -rf "$STAGE"

echo "==> Invalidating CloudFront"
ID=$(aws cloudfront create-invalidation \
  --distribution-id "$DISTRIBUTION_ID" --paths "/*" \
  --profile "$PROFILE" --query 'Invalidation.Id' --output text)
echo "    invalidation $ID created"
echo "==> Done: https://maxops.dev/"
