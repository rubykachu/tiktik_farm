#!/usr/bin/env bash
# Pull latest tiktok-signature JS files from upstream makiisthenes/TiktokAutoUploader.
# Run from repo root: bash scripts/update-tiktok-signature.sh
#
# Only updates the signing JS files — does NOT touch tiktok.py (has local bug fixes).
# Use when upload fails with: "[-] Failed to generate signatures"
set -euo pipefail

UPSTREAM_RAW="https://raw.githubusercontent.com/makiisthenes/TiktokAutoUploader/main/tiktok_uploader"
SIG_DIR="tiktok_uploader/tiktok-signature"

JS_FILES=(
  "javascript/signer.js"
  "javascript/webmssdk.js"
  "javascript/xbogus.js"
  "index.js"
  "browser.js"
)

echo "==> Backing up current files..."
BACKUP_DIR="${SIG_DIR}/backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR/javascript"
for f in "${JS_FILES[@]}"; do
  src="${SIG_DIR}/${f}"
  if [ -f "$src" ]; then
    cp "$src" "${BACKUP_DIR}/${f}"
  fi
done
echo "    Backup saved to $BACKUP_DIR"

echo "==> Downloading latest signing files from upstream..."
for f in "${JS_FILES[@]}"; do
  url="${UPSTREAM_RAW}/tiktok-signature/${f}"
  dest="${SIG_DIR}/${f}"
  echo "    $f"
  curl -fsSL "$url" -o "$dest"
done

echo ""
echo "==> Done. Next steps:"
echo "    1. Test upload: make test-local  (or run a pipeline manually)"
echo "    2. If OK, commit:"
echo "       git add ${SIG_DIR}/"
echo "       git commit -m 'chore(tiktok-signature): sync JS signing files from upstream'"
echo ""
echo "    If broken, restore backup:"
echo "       cp -r ${BACKUP_DIR}/. ${SIG_DIR}/"
