#!/usr/bin/env bash
# Release EZ Maintenance++: build the deterministic zip, tag it, and publish
# the zip as a GitHub Release asset on THIS repo (moquette/ezmaintenanceplusplus).
#
# Mirrors estuary7's release discipline (tools/build_skin.py + `gh release`):
# build -> sha256 -> tag -> gh release create -> verify the asset is really,
# anonymously downloadable and the bytes match what was built. A release that
# fails the verification step is a release that would 404 or ship the wrong
# bytes to a live box, so this script treats verification as mandatory, not
# optional.
#
# Usage:
#   tools/release.sh              # tag + release + verify
#   tools/release.sh --dry-run    # build + show the plan, create nothing
set -euo pipefail
cd "$(dirname "$0")/.."

ADDON=script.ezmaintenanceplusplus
REPO="moquette/ezmaintenanceplusplus"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

./build.sh

VERSION=$(grep '<addon ' "$ADDON/addon.xml" | grep -oE 'version="[^"]+"' | head -1 | sed 's/version="//; s/"//')
ZIP="dist/${ADDON}-${VERSION}.zip"
SHA256=$(shasum -a 256 "$ZIP" | awk '{print $1}')
TAG="v${VERSION}"
ASSET_URL="https://github.com/${REPO}/releases/download/${TAG}/${ADDON}-${VERSION}.zip"

echo
echo "version:    ${VERSION}"
echo "zip:        ${ZIP}"
echo "sha256:     ${SHA256}"
echo "tag:        ${TAG}"
echo "asset url:  ${ASSET_URL}"

if [ "$DRY_RUN" = "1" ]; then
  echo
  echo "(--dry-run: nothing tagged or released)"
  exit 0
fi

if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  echo
  echo "FATAL: release ${TAG} already exists on ${REPO}." >&2
  echo "  Bump addon.xml's version before releasing again, or delete the" >&2
  echo "  existing release first if this is a genuine re-cut." >&2
  exit 1
fi

# --target main (NOT a local tag): the tag GitHub creates for this release must
# resolve against origin/main, never an unpushed local commit. `git tag -f`
# would tag local HEAD, and gh would then push THAT tag (and everything it
# points to - i.e. whatever you have locally, committed or not) to make it
# resolvable remotely. Anchoring to the branch name instead means a release
# can never smuggle out local work that hasn't been reviewed and pushed on
# its own terms.
gh release create "$TAG" "$ZIP" \
  --repo "$REPO" \
  --target main \
  --title "EZ Maintenance++ ${VERSION}" \
  --notes "sha256: ${SHA256}"

echo
echo "verifying the release asset is live and byte-correct..."
TMP=$(mktemp -t ezm_verify.XXXXXX).zip
curl -sSfL "$ASSET_URL" -o "$TMP"
DOWNLOADED_SHA=$(shasum -a 256 "$TMP" | awk '{print $1}')
rm -f "$TMP"

if [ "$DOWNLOADED_SHA" != "$SHA256" ]; then
  echo "FATAL: downloaded asset sha256 mismatch (expected ${SHA256}, got ${DOWNLOADED_SHA})" >&2
  echo "  The release exists but does NOT match the build - do not point the" >&2
  echo "  proxy's repository.json at this tag until this is resolved." >&2
  exit 1
fi

echo "OK - ${ASSET_URL}"
echo "OK - anonymous download sha256-verified against the local build"
