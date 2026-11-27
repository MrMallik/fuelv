#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_HOST="${1:-${DEPLOY_HOST:-cloud-endurance}}"
DELETE_FLAG="${2:---delete-local}"

if [[ -z "$TARGET_HOST" || "$TARGET_HOST" == -* ]]; then
  echo "Invalid target host: $TARGET_HOST" >&2
  exit 1
fi

case "$DELETE_FLAG" in
  ""|--delete-local|--dry-run) ;;
  *)
    echo "Invalid migration flag: $DELETE_FLAG (allowed: --delete-local, --dry-run, or empty)" >&2
    exit 1
    ;;
esac

echo "Connecting to $TARGET_HOST and migrating FuelV production images to Cloudflare R2 (public-bucket/fvs/)..."

ssh -o BatchMode=yes -- "$TARGET_HOST" "
  set -Eeuo pipefail
  docker exec fuelv python3 -m backend.scripts.migrate_images_to_r2 $DELETE_FLAG
"

echo "Migration complete. Verify images at https://r2.pritabrata.com/fvs/ and live at https://pritabrata.com/fuelv/"
