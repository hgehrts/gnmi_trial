#!/usr/bin/env bash
# gNMI collector for Splunk Enterprise.
#
# gnmic subscribes to Cisco, Junos, Arista, Nokia, and Huawei devices that
# speak gNMI. OpenTelemetry metrics go to the local Splunk OpenTelemetry
# Collector, and the same updates are written as JSON events. The collector
# sends metrics to a Splunk metrics index and events to an event index, both
# through HEC.
#
# Usage:
#   1. Edit the settings in this file, or export the same variable names.
#   2. On the Splunk indexer, install the indexes.conf this script prints and
#      create one HEC token allowed to write to the events, metrics, and
#      operations indexes.
#   3. sudo ./install.sh
#   4. Uncomment devices in the targets file the script names, then run it again.
#
# On the Splunk server itself, as the user that runs Splunk (no root, no systemd):
#   ./install.sh --container
#
# Render configs without installing:
#   ./install.sh --render-only /tmp/gnmi-collector-preview
#
# A token or password set here is copied into a root-only file on the collector
# host. Leave them empty and export them instead if this file is kept in git.

set -euo pipefail

# --- settings ---------------------------------------------------------------
# Environment variables of the same name override the values below.

# Splunk HEC. Use the collector endpoint, including the path.
SPLUNK_HEC_URL="${SPLUNK_HEC_URL:-}"
SPLUNK_HEC_TOKEN="${SPLUNK_HEC_TOKEN:-}"
SPLUNK_HEC_EVENTS_INDEX="${SPLUNK_HEC_EVENTS_INDEX:-gnmi}"
SPLUNK_HEC_METRICS_INDEX="${SPLUNK_HEC_METRICS_INDEX:-gnmi_metrics}"
SPLUNK_HEC_OPS_INDEX="${SPLUNK_HEC_OPS_INDEX:-gnmi_ops}"
SPLUNK_HEC_EVENTS_SOURCETYPE="${SPLUNK_HEC_EVENTS_SOURCETYPE:-gnmi:event}"
SPLUNK_HEC_METRICS_SOURCETYPE="${SPLUNK_HEC_METRICS_SOURCETYPE:-gnmi:metric}"
SPLUNK_HEC_SETUP_SOURCETYPE="${SPLUNK_HEC_SETUP_SOURCETYPE:-gnmi:setup}"
SPLUNK_HEC_RUNTIME_SOURCETYPE="${SPLUNK_HEC_RUNTIME_SOURCETYPE:-gnmi:runtime}"
# true skips verification of the HEC server certificate. Set this false and
# set SPLUNK_HEC_CA_FILE when the collector should trust a specific CA.
SPLUNK_HEC_TLS_SKIP_VERIFY="${SPLUNK_HEC_TLS_SKIP_VERIFY:-true}"
SPLUNK_HEC_CA_FILE="${SPLUNK_HEC_CA_FILE:-}"

# Local OpenTelemetry listener. gnmic sends metrics here.
OTLP_GRPC_LISTEN="${OTLP_GRPC_LISTEN:-127.0.0.1:4317}"
OTLP_HTTP_LISTEN="${OTLP_HTTP_LISTEN:-127.0.0.1:4318}"
OTLP_ENDPOINT="${OTLP_ENDPOINT:-127.0.0.1:4317}"

# gNMI. One account is used for every target that does not set its own.
GNMI_USERNAME="${GNMI_USERNAME:-}"
GNMI_PASSWORD="${GNMI_PASSWORD:-}"
GNMI_SAMPLE_INTERVAL="${GNMI_SAMPLE_INTERVAL:-60s}"
# insecure=true speaks plaintext gNMI. Leave false to use TLS.
GNMI_INSECURE="${GNMI_INSECURE:-false}"
# true skips verification of the device certificate. Set this false and set
# GNMI_TLS_CA when devices present a certificate signed by a known CA.
GNMI_SKIP_VERIFY="${GNMI_SKIP_VERIFY:-true}"
GNMI_TLS_CA="${GNMI_TLS_CA:-}"

# Optional path to a targets file. Empty uses the installed copy, or the
# example shipped next to this script when nothing is installed yet.
TARGETS_FILE="${TARGETS_FILE:-}"

# latest, or a release number such as 0.49.1 (gnmic) and 0.163.1 (collector).
GNMIC_VERSION="${GNMIC_VERSION:-latest}"
SPLUNK_OTEL_VERSION="${SPLUNK_OTEL_VERSION:-latest}"
SPLUNK_OTEL_INSTALLER_URL="${SPLUNK_OTEL_INSTALLER_URL:-https://dl.observability.splunkcloud.com/splunk-otel-collector.sh}"

# --- end of settings --------------------------------------------------------

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [[ -z "${TARGETS_FILE}" && -f "${SCRIPT_DIR}/config/targets.yaml" ]]; then
  TARGETS_FILE="${SCRIPT_DIR}/config/targets.yaml"
fi
RENDER_ONLY=""
CONTAINER=""

usage() {
  echo "Usage: sudo $0 [--render-only DIR]" >&2
  echo "       $0 --container" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --render-only)
      [[ $# -ge 2 ]] || usage
      RENDER_ONLY=$2
      shift 2
      ;;
    --container)
      CONTAINER=1
      shift
      ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
done

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

bool_or_die() {
  local name=$1 value=$2
  case "$value" in
    true|false) ;;
    *) echo "$name must be true or false" >&2; exit 1 ;;
  esac
}

reject_secret_chars() {
  local name=$1 value=$2
  case "$value" in
    *$'\n'*|*$'\r'*|*'\\'*|*'"'*|*$'\`'|*'$'*|*'}'*)
      echo "$name contains a character this installer will not embed (newline, quote, backslash, dollar, backtick, or brace)." >&2
      exit 1
      ;;
  esac
}

validate_settings() {
  [[ -n "$SPLUNK_HEC_URL" ]] || { echo "Set SPLUNK_HEC_URL" >&2; exit 1; }
  [[ -n "$SPLUNK_HEC_TOKEN" ]] || { echo "Set SPLUNK_HEC_TOKEN" >&2; exit 1; }
  case "$SPLUNK_HEC_URL" in
    https://*|http://*) ;;
    *) echo "SPLUNK_HEC_URL must start with https:// or http://" >&2; exit 1 ;;
  esac
  case "$SPLUNK_HEC_URL" in
    *services/collector*) ;;
    *) echo "SPLUNK_HEC_URL must include /services/collector" >&2; exit 1 ;;
  esac
  case "$SPLUNK_HEC_URL" in
    *'@'*|*'token='*)
      echo "SPLUNK_HEC_URL must not contain credentials" >&2
      exit 1
      ;;
  esac
  [[ "$SPLUNK_HEC_EVENTS_INDEX" =~ ^[A-Za-z][A-Za-z0-9_]*$ ]] || {
    echo "SPLUNK_HEC_EVENTS_INDEX must be a Splunk index name" >&2; exit 1; }
  [[ "$SPLUNK_HEC_METRICS_INDEX" =~ ^[A-Za-z][A-Za-z0-9_]*$ ]] || {
    echo "SPLUNK_HEC_METRICS_INDEX must be a Splunk index name" >&2; exit 1; }
  [[ "$SPLUNK_HEC_OPS_INDEX" =~ ^[A-Za-z][A-Za-z0-9_]*$ ]] || {
    echo "SPLUNK_HEC_OPS_INDEX must be a Splunk index name" >&2; exit 1; }
  [[ "$SPLUNK_HEC_EVENTS_INDEX" != "$SPLUNK_HEC_METRICS_INDEX" ]] || {
    echo "Use a separate metrics index from the events index" >&2; exit 1; }
  [[ "$SPLUNK_HEC_OPS_INDEX" != "$SPLUNK_HEC_EVENTS_INDEX" && "$SPLUNK_HEC_OPS_INDEX" != "$SPLUNK_HEC_METRICS_INDEX" ]] || {
    echo "Use an operations index that is not the events or metrics index" >&2; exit 1; }
  [[ "$GNMI_SAMPLE_INTERVAL" =~ ^[0-9]+(ns|us|ms|s|m|h)$ ]] || {
    echo "GNMI_SAMPLE_INTERVAL must look like 60s" >&2; exit 1; }
  [[ "$OTLP_ENDPOINT" =~ ^[^[:space:]]+:[0-9]+$ ]] || {
    echo "OTLP_ENDPOINT must look like 127.0.0.1:4317" >&2; exit 1; }
  [[ "$OTLP_GRPC_LISTEN" =~ ^[^[:space:]]+:[0-9]+$ ]] || {
    echo "OTLP_GRPC_LISTEN must look like 127.0.0.1:4317" >&2; exit 1; }
  [[ "$OTLP_HTTP_LISTEN" =~ ^[^[:space:]]+:[0-9]+$ ]] || {
    echo "OTLP_HTTP_LISTEN must look like 127.0.0.1:4318" >&2; exit 1; }
  bool_or_die SPLUNK_HEC_TLS_SKIP_VERIFY "$SPLUNK_HEC_TLS_SKIP_VERIFY"
  bool_or_die GNMI_INSECURE "$GNMI_INSECURE"
  bool_or_die GNMI_SKIP_VERIFY "$GNMI_SKIP_VERIFY"
  reject_secret_chars SPLUNK_HEC_TOKEN "$SPLUNK_HEC_TOKEN"
  reject_secret_chars SPLUNK_HEC_URL "$SPLUNK_HEC_URL"
  reject_secret_chars GNMI_USERNAME "$GNMI_USERNAME"
  reject_secret_chars GNMI_PASSWORD "$GNMI_PASSWORD"
  if [[ -n "$SPLUNK_HEC_CA_FILE" && ! -f "$SPLUNK_HEC_CA_FILE" && -z "$RENDER_ONLY" ]]; then
    echo "SPLUNK_HEC_CA_FILE does not exist: $SPLUNK_HEC_CA_FILE" >&2
    exit 1
  fi
  if [[ -n "$GNMI_TLS_CA" && ! -f "$GNMI_TLS_CA" && -z "$RENDER_ONLY" ]]; then
    echo "GNMI_TLS_CA does not exist: $GNMI_TLS_CA" >&2
    exit 1
  fi
}

render_configs() {
  local dest_gnmic=$1 dest_otel=$2 dest_indexes=$3 targets=$4
  export GNMI_USERNAME GNMI_PASSWORD GNMI_TLS_CA GNMI_INSECURE GNMI_SKIP_VERIFY
  export GNMI_SAMPLE_INTERVAL OTLP_ENDPOINT OTLP_GRPC_LISTEN OTLP_HTTP_LISTEN
  export GNMIC_EVENTS_FILE SPLUNK_HEC_EVENTS_INDEX SPLUNK_HEC_METRICS_INDEX
  export SPLUNK_HEC_OPS_INDEX SPLUNK_HEC_EVENTS_SOURCETYPE SPLUNK_HEC_METRICS_SOURCETYPE
  export SPLUNK_HEC_RUNTIME_SOURCETYPE SPLUNK_HEC_TLS_SKIP_VERIFY SPLUNK_HEC_CA_FILE
  export GNMIC_RUNTIME_LOG
  python3 - "$SCRIPT_DIR" "$dest_gnmic" "$dest_otel" "$dest_indexes" "$targets" <<'PY'
import os
import sys

script_dir, dest_gnmic, dest_otel, dest_indexes, targets_path = sys.argv[1:]

def esc(name):
    return os.environ[name].replace("\\", "\\\\").replace('"', '\\"')

replacements = {
    "__GNMI_USERNAME__": esc("GNMI_USERNAME"),
    "__GNMI_PASSWORD__": esc("GNMI_PASSWORD"),
    "__GNMI_TLS_CA__": esc("GNMI_TLS_CA"),
    "__GNMI_INSECURE__": os.environ["GNMI_INSECURE"],
    "__GNMI_SKIP_VERIFY__": os.environ["GNMI_SKIP_VERIFY"],
    "__GNMI_SAMPLE_INTERVAL__": os.environ["GNMI_SAMPLE_INTERVAL"],
    "__OTLP_ENDPOINT__": esc("OTLP_ENDPOINT"),
    "__OTLP_GRPC_LISTEN__": esc("OTLP_GRPC_LISTEN"),
    "__OTLP_HTTP_LISTEN__": esc("OTLP_HTTP_LISTEN"),
    "__GNMIC_EVENTS_FILE__": esc("GNMIC_EVENTS_FILE"),
    "__SPLUNK_HEC_EVENTS_INDEX__": esc("SPLUNK_HEC_EVENTS_INDEX"),
    "__SPLUNK_HEC_METRICS_INDEX__": esc("SPLUNK_HEC_METRICS_INDEX"),
    "__SPLUNK_HEC_OPS_INDEX__": esc("SPLUNK_HEC_OPS_INDEX"),
    "__SPLUNK_HEC_EVENTS_SOURCETYPE__": esc("SPLUNK_HEC_EVENTS_SOURCETYPE"),
    "__SPLUNK_HEC_METRICS_SOURCETYPE__": esc("SPLUNK_HEC_METRICS_SOURCETYPE"),
    "__SPLUNK_HEC_RUNTIME_SOURCETYPE__": esc("SPLUNK_HEC_RUNTIME_SOURCETYPE"),
    "__GNMIC_RUNTIME_LOG__": esc("GNMIC_RUNTIME_LOG"),
    "__SPLUNK_HEC_TLS_SKIP_VERIFY__": os.environ["SPLUNK_HEC_TLS_SKIP_VERIFY"],
    "__SPLUNK_HEC_CA_FILE__": esc("SPLUNK_HEC_CA_FILE"),
}

def fill(name):
    text = open(os.path.join(script_dir, "config", name), encoding="utf-8").read()
    for key, value in replacements.items():
        text = text.replace(key, value)
    import re
    if re.search(r"__[A-Z0-9_]+__", text):
        sys.exit(f"unreplaced placeholder in {name}")
    kept = []
    for line in text.splitlines(True):
        body = line.split("#", 1)[0].strip()
        if body in ('tls-ca: ""', 'ca_file: ""'):
            continue
        kept.append(line)
    return "".join(kept)

gnmic = fill("gnmic.yaml.template")
if not gnmic.endswith("\n"):
    gnmic += "\n"
targets = open(targets_path, encoding="utf-8").read()
if "targets:" not in targets:
    sys.exit(f"{targets_path} must contain a targets: section")
gnmic += "\n" + targets
if not gnmic.endswith("\n"):
    gnmic += "\n"
open(dest_gnmic, "w", encoding="utf-8").write(gnmic)
open(dest_otel, "w", encoding="utf-8").write(fill("otel-agent.yaml.template"))
open(dest_indexes, "w", encoding="utf-8").write(fill("indexes.conf"))
PY
  chmod 600 "$dest_gnmic"
  chmod 644 "$dest_otel" "$dest_indexes"
}

targets_have_address() {
  local file=$1
  grep -Eq '^[[:space:]]*address:[[:space:]]*[^[:space:]#]' "$file"
}

require_gnmi_account() {
  [[ -n "$GNMI_USERNAME" && -n "$GNMI_PASSWORD" ]] || {
    echo "Set GNMI_USERNAME and GNMI_PASSWORD before collecting from devices." >&2
    exit 1
  }
}

write_env_file() {
  local file=$1
  local tmp
  tmp=$(mktemp)
  if [[ -f "$file" ]]; then
    grep -Ev '^(SPLUNK_HEC_TOKEN|SPLUNK_HEC_URL|SPLUNK_CONFIG|OTELCOL_OPTIONS)=' "$file" > "$tmp" || true
  fi
  {
    printf 'SPLUNK_HEC_URL="%s"\n' "$SPLUNK_HEC_URL"
    printf 'SPLUNK_HEC_TOKEN="%s"\n' "$SPLUNK_HEC_TOKEN"
    if [[ -n "${SPLUNK_CONFIG_PATH:-}" ]]; then
      printf 'SPLUNK_CONFIG="%s"\n' "$SPLUNK_CONFIG_PATH"
    fi
  } >> "$tmp"
  install -m 600 "$tmp" "$file"
  rm -f "$tmp"
}

# Inside Splunk, LD_LIBRARY_PATH points at Splunk's own libcurl and OpenSSL,
# and the system curl fails with error 48 when it loads them.
curl() {
  env -u LD_LIBRARY_PATH -u OPENSSL_CONF -u OPENSSL_MODULES -u SSL_CERT_FILE curl "$@"
}

latest_version() {
  local repo=$1 url tag
  url=$(curl -fsSL -o /dev/null -w '%{url_effective}' "https://github.com/${repo}/releases/latest")
  tag=${url##*/}
  tag=${tag#v}
  if [[ -z "$tag" || "$tag" == "latest" ]]; then
    echo "Could not read the latest ${repo} release." >&2
    exit 1
  fi
  printf '%s\n' "$tag"
}

gnmic_asset_name() {
  local version=$1
  local os arch
  os=$(uname -s)
  arch=$(uname -m)
  case "${os}:${arch}" in
    Darwin:arm64) echo "gnmic_${version}_Darwin_aarch64.tar.gz" ;;
    Darwin:x86_64) echo "gnmic_${version}_Darwin_x86_64.tar.gz" ;;
    Linux:x86_64) echo "gnmic_${version}_Linux_x86_64.tar.gz" ;;
    Linux:aarch64|Linux:arm64) echo "gnmic_${version}_Linux_aarch64.tar.gz" ;;
    Linux:armv7l) echo "gnmic_${version}_Linux_armv7.tar.gz" ;;
    *)
      echo "Unsupported gnmic platform ${os} ${arch}" >&2
      exit 1
      ;;
  esac
}

verify_checksum() {
  local sums=$1 name=$2 file=$3
  python3 - "$sums" "$name" "$file" <<'PY'
import hashlib, sys
sums, name, path = sys.argv[1:]
want = None
for line in open(sums, encoding="utf-8"):
    parts = line.split()
    if len(parts) >= 2 and (parts[-1] == name or parts[-1].endswith("/" + name)):
        want = parts[0]
        break
if want is None:
    sys.exit(f"checksum list has no entry for {name}")
got = hashlib.sha256(open(path, "rb").read()).hexdigest()
if got.lower() != want.lower():
    sys.exit(f"checksum mismatch for {name}")
PY
}

install_gnmic_binary_to() {
  local dest=$1
  local version=$GNMIC_VERSION
  if [[ "$version" == latest ]]; then
    version=$(latest_version openconfig/gnmic)
  fi
  version=${version#v}
  if [[ -x "$dest" ]] && "$dest" version 2>&1 | grep -q "$version"; then
    echo "gnmic ${version} is already installed"
    return
  fi
  local asset url tmp sums
  asset=$(gnmic_asset_name "$version")
  url="https://github.com/openconfig/gnmic/releases/download/v${version}/${asset}"
  tmp=$(mktemp -d)
  echo "Downloading gnmic ${version}"
  curl -fsSL --retry 3 -o "${tmp}/${asset}" "$url"
  curl -fsSL -o "${tmp}/checksums.txt" \
    "https://github.com/openconfig/gnmic/releases/download/v${version}/checksums.txt"
  verify_checksum "${tmp}/checksums.txt" "$asset" "${tmp}/${asset}"
  tar -xzf "${tmp}/${asset}" -C "$tmp"
  local bin
  bin=
  while IFS= read -r candidate; do
    bin=$candidate
    break
  done < <(find "$tmp" -type f -name gnmic)
  [[ -n "$bin" ]] || { echo "gnmic archive did not contain the gnmic binary" >&2; exit 1; }
  install -m 755 "$bin" "$dest"
  rm -rf "$tmp"
}

install_gnmic_binary() {
  install_gnmic_binary_to /usr/local/bin/gnmic
}

otel_asset_name() {
  local os arch
  os=$(uname -s)
  arch=$(uname -m)
  case "${os}:${arch}" in
    Darwin:arm64) echo otelcol_darwin_arm64 ;;
    Darwin:x86_64) echo otelcol_darwin_amd64 ;;
    Linux:x86_64) echo otelcol_linux_amd64 ;;
    Linux:aarch64|Linux:arm64) echo otelcol_linux_arm64 ;;
    *)
      echo "Unsupported collector platform ${os} ${arch}" >&2
      exit 1
      ;;
  esac
}

install_otel_binary_to() {
  local dest=$1
  local version=$SPLUNK_OTEL_VERSION
  if [[ "$version" == latest ]]; then
    version=$(latest_version signalfx/splunk-otel-collector)
  fi
  version=${version#v}
  if [[ -x "$dest" ]] && "$dest" --version 2>&1 | grep -q "v${version}\$"; then
    echo "Splunk OpenTelemetry Collector ${version} is already installed"
    return
  fi
  local asset url tmp
  asset=$(otel_asset_name)
  url="https://github.com/signalfx/splunk-otel-collector/releases/download/v${version}/${asset}"
  tmp=$(mktemp -d)
  echo "Downloading Splunk OpenTelemetry Collector ${version}"
  curl -fsSL --retry 3 -o "${tmp}/${asset}" "$url"
  curl -fsSL -o "${tmp}/checksums.txt" \
    "https://github.com/signalfx/splunk-otel-collector/releases/download/v${version}/checksums.txt"
  verify_checksum "${tmp}/checksums.txt" "$asset" "${tmp}/${asset}"
  install -m 755 "${tmp}/${asset}" "$dest"
  rm -rf "$tmp"
}

install_otel_macos() {
  install_otel_binary_to /usr/local/bin/splunk-otel-collector
}

install_otel_linux() {
  if [[ -x /usr/bin/otelcol ]]; then
    echo "Splunk OpenTelemetry Collector is already installed"
    write_env_file /etc/otel/collector/splunk-otel-collector.conf
    return
  fi
  local installer version_args=()
  installer=$(mktemp)
  curl -fsSL --retry 3 -o "$installer" "$SPLUNK_OTEL_INSTALLER_URL"
  if [[ "$SPLUNK_OTEL_VERSION" != latest ]]; then
    version_args=(--collector-version "${SPLUNK_OTEL_VERSION#v}")
  fi
  # The installer accepts the HEC token only as an argument. It is a short-lived
  # process; the installed service reads the token from the environment file.
  # Enterprise HEC tokens are not Observability Cloud access tokens.
  VERIFY_ACCESS_TOKEN=false sh "$installer" \
    --without-instrumentation \
    --collector-config "$OTEL_CONFIG" \
    "${version_args[@]}" \
    --hec-url "$SPLUNK_HEC_URL" \
    --hec-token "$SPLUNK_HEC_TOKEN" \
    -- "$SPLUNK_HEC_TOKEN"
  rm -f "$installer"
  write_env_file /etc/otel/collector/splunk-otel-collector.conf
}

ensure_gnmic_user() {
  if ! id gnmic >/dev/null 2>&1; then
    local nologin=/usr/sbin/nologin
    [[ -x "$nologin" ]] || nologin=/sbin/nologin
    useradd --system --home-dir /var/lib/gnmic --create-home --shell "$nologin" gnmic
  fi
  chown root:gnmic "$CONFIG_DIR" "$GNMIC_CONFIG" "${CONFIG_DIR}/targets.yaml"
  chmod 750 "$CONFIG_DIR"
  chmod 640 "$GNMIC_CONFIG" "${CONFIG_DIR}/targets.yaml"
  chown gnmic:gnmic "$EVENTS_DIR" "$GNMIC_EVENTS_FILE"
  chmod 755 "$EVENTS_DIR"
  chmod 644 "$GNMIC_EVENTS_FILE"
  mkdir -p "$(dirname "$GNMIC_RUNTIME_LOG")"
  if [[ ! -f "$GNMIC_RUNTIME_LOG" ]]; then
    : > "$GNMIC_RUNTIME_LOG"
  fi
  chown gnmic:gnmic "$(dirname "$GNMIC_RUNTIME_LOG")" "$GNMIC_RUNTIME_LOG"
  chmod 755 "$(dirname "$GNMIC_RUNTIME_LOG")"
  chmod 644 "$GNMIC_RUNTIME_LOG"
}

write_gnmic_wrapper() {
  mkdir -p /usr/local/libexec/gnmi-collector
  cat > /usr/local/libexec/gnmi-collector/run-gnmic.sh <<EOF
#!/bin/bash
mkdir -p "$(dirname "$GNMIC_RUNTIME_LOG")"
exec /usr/local/bin/gnmic --config "${GNMIC_CONFIG}" subscribe >> "${GNMIC_RUNTIME_LOG}" 2>&1
EOF
  chmod 755 /usr/local/libexec/gnmi-collector/run-gnmic.sh
}

write_gnmic_unit() {
  cat > /etc/systemd/system/gnmic.service <<EOF
[Unit]
Description=gNMI collector
After=network-online.target splunk-otel-collector.service
Wants=splunk-otel-collector.service

[Service]
User=gnmic
Group=gnmic
ExecStart=/usr/local/libexec/gnmi-collector/run-gnmic.sh
Restart=always
RestartSec=5
UMask=0022

[Install]
WantedBy=multi-user.target
EOF
}

write_macos_helpers() {
  mkdir -p /usr/local/libexec/gnmi-collector /Library/LaunchDaemons
  cat > /usr/local/libexec/gnmi-collector/run-otel.sh <<EOF
#!/bin/bash
set -a
source ${ENV_FILE}
set +a
exec /usr/local/bin/splunk-otel-collector --config ${OTEL_CONFIG}
EOF
  cat > /usr/local/libexec/gnmi-collector/run-gnmic.sh <<EOF
#!/bin/bash
mkdir -p "$(dirname "$GNMIC_RUNTIME_LOG")"
exec /usr/local/bin/gnmic --config "${GNMIC_CONFIG}" subscribe >> "${GNMIC_RUNTIME_LOG}" 2>&1
EOF
  chmod 755 /usr/local/libexec/gnmi-collector/run-otel.sh /usr/local/libexec/gnmi-collector/run-gnmic.sh
  cat > /Library/LaunchDaemons/com.gnmi.otel-agent.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.gnmi.otel-agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/usr/local/libexec/gnmi-collector/run-otel.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/usr/local/var/log/gnmi-collector/otel-agent.log</string>
  <key>StandardErrorPath</key><string>/usr/local/var/log/gnmi-collector/otel-agent.err</string>
</dict>
</plist>
EOF
  cat > /Library/LaunchDaemons/com.gnmi.gnmic.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.gnmi.gnmic</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/usr/local/libexec/gnmi-collector/run-gnmic.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
EOF
  chmod 644 /Library/LaunchDaemons/com.gnmi.otel-agent.plist /Library/LaunchDaemons/com.gnmi.gnmic.plist
}

bootstrap_macos() {
  local label=$1 plist=$2
  launchctl bootout "system/${label}" >/dev/null 2>&1 || true
  launchctl bootstrap system "$plist"
  launchctl enable "system/${label}"
}

stop_background() {
  local pidfile=$1
  local pid
  [[ -f "$pidfile" ]] || return 0
  pid=$(cat "$pidfile" 2>/dev/null || true)
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
}

start_background() {
  local pidfile=$1
  shift
  stop_background "$pidfile"
  nohup "$@" >/dev/null 2>&1 &
  echo $! > "$pidfile"
}

container_finish() {
  local status=$?
  if [[ "$status" -ne 0 && -n "${PREFIX:-}" ]]; then
    echo failed > "${PREFIX}/run/install.status"
  fi
  stop_hec_logger "$status"
}

install_in_place() {
  if [[ "$(uname -s)" != Linux ]]; then
    echo "Install on this server runs on Linux. On macOS, run sudo ./install.sh on the collector host." >&2
    exit 1
  fi
  PREFIX=${GNMI_CONTAINER_PREFIX:-/opt/splunk/var/gnmi-collector}
  mkdir -p "$PREFIX/bin" "$PREFIX/etc" "$PREFIX/logs" "$PREFIX/run"
  chmod 700 "$PREFIX" "$PREFIX/etc" "$PREFIX/run"
  echo running > "$PREFIX/run/install.status"

  CONFIG_DIR=$PREFIX/etc
  EVENTS_DIR=$PREFIX/logs
  OTEL_DIR=$PREFIX/etc
  GNMIC_EVENTS_FILE=$PREFIX/logs/events.json
  GNMIC_RUNTIME_LOG=$PREFIX/logs/gnmic.log
  GNMIC_CONFIG=$CONFIG_DIR/gnmic.yaml
  OTEL_CONFIG=$OTEL_DIR/otel-agent.yaml
  INDEXES_CONFIG=$CONFIG_DIR/indexes.conf
  ENV_FILE=$CONFIG_DIR/hec.env
  SPLUNK_CONFIG_PATH=$OTEL_CONFIG

  start_hec_logger
  trap container_finish EXIT

  echo "Installing under ${PREFIX} as $(id -un)"
  mkdir -p "$CONFIG_DIR" "$EVENTS_DIR"
  if [[ ! -f "$GNMIC_EVENTS_FILE" ]]; then
    : > "$GNMIC_EVENTS_FILE"
  fi
  chmod 644 "$GNMIC_EVENTS_FILE"

  local installed_targets="${CONFIG_DIR}/targets.yaml"
  if [[ -n "$TARGETS_FILE" ]]; then
    install -m 644 "$TARGETS_FILE" "$installed_targets"
  elif [[ ! -f "$installed_targets" ]]; then
    install -m 644 "${SCRIPT_DIR}/config/targets.example.yaml" "$installed_targets"
  fi

  local collect=false
  if targets_have_address "$installed_targets"; then
    require_gnmi_account
    collect=true
  fi

  render_configs "$GNMIC_CONFIG" "$OTEL_CONFIG" "$INDEXES_CONFIG" "$installed_targets"
  write_env_file "$ENV_FILE"
  install_otel_binary_to "$PREFIX/bin/splunk-otel-collector"
  install_gnmic_binary_to "$PREFIX/bin/gnmic"

  cat > "$PREFIX/bin/run-otel.sh" <<EOF
#!/bin/bash
set -a
source ${ENV_FILE}
set +a
exec ${PREFIX}/bin/splunk-otel-collector --config ${OTEL_CONFIG} >> ${PREFIX}/logs/otel.log 2>&1
EOF
  cat > "$PREFIX/bin/run-gnmic.sh" <<EOF
#!/bin/bash
mkdir -p "$(dirname "$GNMIC_RUNTIME_LOG")"
exec ${PREFIX}/bin/gnmic --config ${GNMIC_CONFIG} subscribe >> ${GNMIC_RUNTIME_LOG} 2>&1
EOF
  chmod 755 "$PREFIX/bin/run-otel.sh" "$PREFIX/bin/run-gnmic.sh"

  : > "$PREFIX/logs/otel.log"
  start_background "$PREFIX/run/otel.pid" "$PREFIX/bin/run-otel.sh"
  sleep 2
  if ! kill -0 "$(cat "$PREFIX/run/otel.pid")" 2>/dev/null; then
    echo "OpenTelemetry Collector exited. Last log lines:" >&2
    tail -n 40 "$PREFIX/logs/otel.log" >&2 || true
    exit 1
  fi
  echo "OpenTelemetry Collector started"
  if [[ "$collect" == true ]]; then
    start_background "$PREFIX/run/gnmic.pid" "$PREFIX/bin/run-gnmic.sh"
    sleep 2
    if ! kill -0 "$(cat "$PREFIX/run/gnmic.pid")" 2>/dev/null; then
      echo "gnmic exited. Last log lines:" >&2
      tail -n 40 "$GNMIC_RUNTIME_LOG" >&2 || true
      exit 1
    fi
    echo "gnmic is subscribed to the devices in ${installed_targets}."
  else
    stop_background "$PREFIX/run/gnmic.pid"
    echo "No device address is uncommented yet. Edit ${installed_targets} and run this script again."
  fi
  echo "Configs: ${OTEL_CONFIG} and ${GNMIC_CONFIG}"
  echo ok > "$PREFIX/run/install.status"
}

main() {
  need_cmd python3
  need_cmd curl
  validate_settings

  if [[ -n "$CONTAINER" ]]; then
    install_in_place
    return
  fi

  local os targets_src
  os=$(uname -s)
  case "$os" in
    Linux)
      CONFIG_DIR=/etc/gnmic
      EVENTS_DIR=/var/log/gnmic
      OTEL_DIR=/etc/otel/collector
      ;;
    Darwin)
      CONFIG_DIR=/usr/local/etc/gnmi-collector
      EVENTS_DIR=/usr/local/var/log/gnmic
      OTEL_DIR=$CONFIG_DIR
      ;;
    *)
      echo "Unsupported operating system: $os" >&2
      exit 1
      ;;
  esac

  GNMIC_EVENTS_FILE=${GNMIC_EVENTS_FILE:-${EVENTS_DIR}/events.json}
  if [[ "$os" == Darwin ]]; then
    GNMIC_RUNTIME_LOG=${GNMIC_RUNTIME_LOG:-/usr/local/var/log/gnmi-collector/gnmic.log}
  else
    GNMIC_RUNTIME_LOG=${GNMIC_RUNTIME_LOG:-/var/log/gnmi-collector/gnmic.log}
  fi
  GNMIC_CONFIG=${CONFIG_DIR}/gnmic.yaml
  OTEL_CONFIG=${OTEL_DIR}/gnmi-agent.yaml
  INDEXES_CONFIG=${CONFIG_DIR}/indexes.conf
  ENV_FILE=${OTEL_DIR}/splunk-otel-collector.conf
  if [[ "$os" == Darwin ]]; then
    OTEL_CONFIG=${CONFIG_DIR}/otel-agent.yaml
    ENV_FILE=${CONFIG_DIR}/hec.env
  fi
  SPLUNK_CONFIG_PATH=$OTEL_CONFIG

  if [[ -n "$RENDER_ONLY" ]]; then
    mkdir -p "$RENDER_ONLY"
    targets_src=${TARGETS_FILE:-${SCRIPT_DIR}/config/targets.example.yaml}
    render_configs \
      "${RENDER_ONLY}/gnmic.yaml" \
      "${RENDER_ONLY}/otel-agent.yaml" \
      "${RENDER_ONLY}/indexes.conf" \
      "$targets_src"
    write_env_file "${RENDER_ONLY}/hec.env"
    echo "Wrote configs in ${RENDER_ONLY}"
    echo "Indexes to install on Splunk: ${RENDER_ONLY}/indexes.conf"
    exit 0
  fi

  start_hec_logger

  if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
  fi

  mkdir -p "$CONFIG_DIR" "$EVENTS_DIR" "$OTEL_DIR"
  chmod 755 "$EVENTS_DIR"
  if [[ ! -f "$GNMIC_EVENTS_FILE" ]]; then
    : > "$GNMIC_EVENTS_FILE"
  fi
  chmod 644 "$GNMIC_EVENTS_FILE"

  local installed_targets="${CONFIG_DIR}/targets.yaml"
  if [[ -n "$TARGETS_FILE" ]]; then
    install -m 644 "$TARGETS_FILE" "$installed_targets"
  elif [[ ! -f "$installed_targets" ]]; then
    install -m 644 "${SCRIPT_DIR}/config/targets.example.yaml" "$installed_targets"
  fi

  local collect=false
  if targets_have_address "$installed_targets"; then
    require_gnmi_account
    collect=true
  fi

  render_configs "$GNMIC_CONFIG" "$OTEL_CONFIG" "$INDEXES_CONFIG" "$installed_targets"

  case "$os" in
    Linux)
      install_otel_linux
      install_gnmic_binary
      ensure_gnmic_user
      write_gnmic_wrapper
      write_gnmic_unit
      systemctl daemon-reload
      systemctl enable splunk-otel-collector
      systemctl restart splunk-otel-collector
      if [[ "$collect" == true ]]; then
        systemctl enable gnmic
        systemctl restart gnmic
      else
        systemctl disable --now gnmic >/dev/null 2>&1 || true
      fi
      ;;
    Darwin)
      mkdir -p /usr/local/var/log/gnmi-collector
      install_otel_macos
      install_gnmic_binary
      write_env_file "$ENV_FILE"
      write_macos_helpers
      bootstrap_macos com.gnmi.otel-agent /Library/LaunchDaemons/com.gnmi.otel-agent.plist
      if [[ "$collect" == true ]]; then
        bootstrap_macos com.gnmi.gnmic /Library/LaunchDaemons/com.gnmi.gnmic.plist
      else
        launchctl bootout system/com.gnmi.gnmic >/dev/null 2>&1 || true
      fi
      ;;
  esac

  echo
  echo "Splunk OpenTelemetry Collector config: ${OTEL_CONFIG}"
  echo "gnmic config: ${GNMIC_CONFIG}"
  echo "Targets file: ${installed_targets}"
  echo "Install this indexes.conf on the Splunk indexer, then restart Splunk:"
  echo "  ${INDEXES_CONFIG}"
  echo "HEC token must be allowed to write to ${SPLUNK_HEC_EVENTS_INDEX}, ${SPLUNK_HEC_METRICS_INDEX}, and ${SPLUNK_HEC_OPS_INDEX}."
  if [[ "$collect" == true ]]; then
    echo "gnmic is subscribed to the devices in ${installed_targets}."
  else
    echo "No device address is uncommented yet. Edit ${installed_targets} and run this script again."
  fi
}

stop_hec_logger() {
  local status=${1:-$?}
  trap - EXIT
  if [[ -n "${HEC_LOGGER_STARTED:-}" ]]; then
    if [[ "$status" -eq 0 ]]; then
      echo "Install finished."
    else
      echo "Install failed with exit code ${status}."
    fi
  fi
  # Drop the tee pipe first. hec_log.py finishes only after that writer closes.
  exec >&3 2>&4 || true
  wait "${LOGGER_PID:-}" 2>/dev/null || true
  rm -f "${HEC_LOG_FIFO:-}"
}

start_hec_logger() {
  [[ -n "${HEC_LOGGER_STARTED:-}" ]] && return 0
  HEC_LOGGER_STARTED=1
  HEC_LOG_FIFO=$(mktemp -u "${TMPDIR:-/tmp}/gnmi-hec.XXXXXX")
  mkfifo "$HEC_LOG_FIFO"
  GNMI_INSTALL_RUN=$(date -u +%Y%m%dT%H%M%SZ)-$$
  export HEC_LOG_FIFO GNMI_INSTALL_RUN
  export SPLUNK_HEC_URL SPLUNK_HEC_TOKEN SPLUNK_HEC_OPS_INDEX
  export SPLUNK_HEC_SETUP_SOURCETYPE SPLUNK_HEC_TLS_SKIP_VERIFY SPLUNK_HEC_CA_FILE
  python3 -u "${SCRIPT_DIR}/hec_log.py" &
  LOGGER_PID=$!
  exec 3>&1 4>&2
  # tee copies every install line to the console, a local log, and hec_log.py.
  exec > >(tee -a "${TMPDIR:-/tmp}/gnmi-install.log" "$HEC_LOG_FIFO") 2>&1
  trap stop_hec_logger EXIT
}

main "$@"
