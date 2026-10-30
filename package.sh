#!/bin/bash
# Build a Splunk app archive for a GitHub release.
# The version comes from default/app.conf. Tag the release with that version.
set -euo pipefail
cd "$(dirname "$0")"
version=$(awk '
  /^\[launcher\]/ { in_launcher = 1; next }
  /^\[/ { in_launcher = 0 }
  in_launcher && /^version[[:space:]]*=/ {
    value = $0
    sub(/^version[[:space:]]*=[[:space:]]*/, "", value)
    gsub(/[[:space:]]/, "", value)
    print value
    exit
  }
' default/app.conf)
if [[ -z "$version" ]]; then
  echo "version is missing from default/app.conf" >&2
  exit 1
fi
mkdir -p dist
out="dist/gnmi_trial-${version}.spl"
rm -f "$out"
COPYFILE_DISABLE=1 tar -czf "$out" \
  --exclude 'gnmi_trial/dist' \
  --exclude 'gnmi_trial/.git' \
  --exclude 'gnmi_trial/tools' \
  --exclude '.DS_Store' \
  --exclude '._*' \
  --exclude 'gnmi_trial/bin/__pycache__' \
  --exclude 'gnmi_trial/local' \
  --exclude 'gnmi_trial/metadata/local.meta' \
  --exclude '*.pyc' \
  -C .. gnmi_trial
echo "Wrote ${out}"
echo "GitHub release tag: v${version}"
