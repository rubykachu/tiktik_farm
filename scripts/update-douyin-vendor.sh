#!/usr/bin/env bash
# Pull latest douyin-downloader from upstream into vendor/douyin-downloader.
# Run from repo root: bash scripts/update-douyin-vendor.sh
set -euo pipefail

VENDOR_DIR="vendor/douyin-downloader"
UPSTREAM="https://github.com/jiji262/douyin-downloader.git"
BACKUP_BRANCH="backup/douyin-vendor-$(date +%Y%m%d-%H%M%S)"

if [ ! -d "$VENDOR_DIR/.git" ]; then
  echo "ERROR: $VENDOR_DIR is not a git repo. Re-clone first." >&2
  exit 1
fi

cd "$VENDOR_DIR"

# Create backup branch before overwriting
git checkout -b "$BACKUP_BRANCH" 2>/dev/null || true

# Add upstream temporarily, fetch, then remove it
git remote add upstream "$UPSTREAM"
git fetch --depth=1 upstream main
git checkout upstream/main -- .
git remote remove upstream

echo "Done. Vendor updated. Backup branch: $BACKUP_BRANCH"
echo "Run: cd ../.. && git add vendor/douyin-downloader && git commit -m 'chore(vendor): update douyin-downloader'"
