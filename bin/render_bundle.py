"""Build the gNMI trial bundle from the app templates.

The HEC token and device password are written only into install.sh defaults
and gnmic.yaml. Callers must not log the returned file bodies.
"""

import base64
import io
import os
import re
import zipfile

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "appserver", "templates")

SHELL_KEYS = (
    "SPLUNK_HEC_URL",
    "SPLUNK_HEC_TOKEN",
    "SPLUNK_HEC_EVENTS_INDEX",
    "SPLUNK_HEC_METRICS_INDEX",
    "SPLUNK_HEC_OPS_INDEX",
    "SPLUNK_HEC_EVENTS_SOURCETYPE",
    "SPLUNK_HEC_METRICS_SOURCETYPE",
    "SPLUNK_HEC_SETUP_SOURCETYPE",
    "SPLUNK_HEC_RUNTIME_SOURCETYPE",
    "SPLUNK_HEC_TLS_SKIP_VERIFY",
    "SPLUNK_HEC_CA_FILE",
    "OTLP_GRPC_LISTEN",
    "OTLP_HTTP_LISTEN",
    "OTLP_ENDPOINT",
    "GNMI_USERNAME",
    "GNMI_PASSWORD",
    "GNMI_SAMPLE_INTERVAL",
    "GNMI_INSECURE",
    "GNMI_SKIP_VERIFY",
    "GNMI_TLS_CA",
    "GNMIC_VERSION",
    "SPLUNK_OTEL_VERSION",
    "SPLUNK_OTEL_INSTALLER_URL",
)

INDEX_KEYS = (
    "SPLUNK_HEC_EVENTS_INDEX",
    "SPLUNK_HEC_METRICS_INDEX",
    "SPLUNK_HEC_OPS_INDEX",
)

BOOL_KEYS = (
    "SPLUNK_HEC_TLS_SKIP_VERIFY",
    "GNMI_INSECURE",
    "GNMI_SKIP_VERIFY",
)

PREVIEW_EVENTS = "/var/log/gnmic/events.json"
PREVIEW_RUNTIME = "/var/log/gnmi-collector/gnmic.log"


def _bool_string(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip().lower()
    if text not in ("true", "false"):
        raise ValueError("expected true or false")
    return text


def _reject(name, value):
    if any(ch in value for ch in ('\n', '\r', '\\', '"', '`', '$', '}')):
        raise ValueError(name + " contains a character the installer will not embed")


def normalize(raw):
    settings = {}
    for key in SHELL_KEYS:
        if key not in raw or raw[key] is None:
            raise ValueError("missing " + key)
        if key in BOOL_KEYS:
            settings[key] = _bool_string(raw[key])
        else:
            settings[key] = str(raw[key]).strip()
        _reject(key, settings[key])
    if not settings["SPLUNK_HEC_URL"].startswith(("https://", "http://")):
        raise ValueError("SPLUNK_HEC_URL must start with https:// or http://")
    if "services/collector" not in settings["SPLUNK_HEC_URL"] or "@" in settings["SPLUNK_HEC_URL"]:
        raise ValueError("SPLUNK_HEC_URL must be a HEC collector URL without credentials")
    if not settings["SPLUNK_HEC_TOKEN"]:
        raise ValueError("SPLUNK_HEC_TOKEN is required")
    indexes = [settings[key] for key in INDEX_KEYS]
    for name in indexes:
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", name):
            raise ValueError(name + " is not a Splunk index name")
    if len(set(indexes)) != 3:
        raise ValueError("events, metrics, and operations indexes must be three different names")
    if not re.match(r"^[0-9]+(ns|us|ms|s|m|h)$", settings["GNMI_SAMPLE_INTERVAL"]):
        raise ValueError("GNMI_SAMPLE_INTERVAL must look like 60s")
    for key in ("OTLP_ENDPOINT", "OTLP_GRPC_LISTEN", "OTLP_HTTP_LISTEN"):
        if not re.match(r"^[^:\s]+:[0-9]+$", settings[key]) and not re.match(r"^\[[^\]]+\]:[0-9]+$", settings[key]):
            if not re.match(r"^[^/\s]+:[0-9]+$", settings[key]):
                raise ValueError(key + " must look like 127.0.0.1:4317")
    targets = raw.get("targets_yaml")
    if not isinstance(targets, str) or "targets:" not in targets:
        raise ValueError("targets YAML must contain a targets: section")
    if len(targets) > 256000:
        raise ValueError("targets YAML is too large")
    settings["targets_yaml"] = targets if targets.endswith("\n") else targets + "\n"
    create = raw.get("create_indexes", False)
    if isinstance(create, str):
        create = create.strip().lower() in ("1", "true", "yes", "on")
    settings["create_indexes"] = bool(create)
    return settings


def _read(name):
    path = os.path.join(TEMPLATE_DIR, name)
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _fill_shell(text, settings):
    for key in SHELL_KEYS:
        pattern = re.compile(
            r"^(" + re.escape(key) + r'="\$\{' + re.escape(key) + r":-)[^}\"]*(\}\")",
            re.M,
        )
        if not pattern.search(text):
            raise ValueError("install.sh is missing the setting " + key)

        def repl(match, value=settings[key]):
            return match.group(1) + value + match.group(2)

        text = pattern.sub(repl, text, count=1)
    return text


def _fill_templates(settings, events_file, runtime_log):
    replacements = {
        "__GNMI_USERNAME__": settings["GNMI_USERNAME"].replace("\\", "\\\\").replace('"', '\\"'),
        "__GNMI_PASSWORD__": settings["GNMI_PASSWORD"].replace("\\", "\\\\").replace('"', '\\"'),
        "__GNMI_TLS_CA__": settings["GNMI_TLS_CA"].replace("\\", "\\\\").replace('"', '\\"'),
        "__GNMI_INSECURE__": settings["GNMI_INSECURE"],
        "__GNMI_SKIP_VERIFY__": settings["GNMI_SKIP_VERIFY"],
        "__GNMI_SAMPLE_INTERVAL__": settings["GNMI_SAMPLE_INTERVAL"],
        "__OTLP_ENDPOINT__": settings["OTLP_ENDPOINT"],
        "__OTLP_GRPC_LISTEN__": settings["OTLP_GRPC_LISTEN"],
        "__OTLP_HTTP_LISTEN__": settings["OTLP_HTTP_LISTEN"],
        "__GNMIC_EVENTS_FILE__": events_file,
        "__GNMIC_RUNTIME_LOG__": runtime_log,
        "__SPLUNK_HEC_EVENTS_INDEX__": settings["SPLUNK_HEC_EVENTS_INDEX"],
        "__SPLUNK_HEC_METRICS_INDEX__": settings["SPLUNK_HEC_METRICS_INDEX"],
        "__SPLUNK_HEC_OPS_INDEX__": settings["SPLUNK_HEC_OPS_INDEX"],
        "__SPLUNK_HEC_EVENTS_SOURCETYPE__": settings["SPLUNK_HEC_EVENTS_SOURCETYPE"],
        "__SPLUNK_HEC_METRICS_SOURCETYPE__": settings["SPLUNK_HEC_METRICS_SOURCETYPE"],
        "__SPLUNK_HEC_RUNTIME_SOURCETYPE__": settings["SPLUNK_HEC_RUNTIME_SOURCETYPE"],
        "__SPLUNK_HEC_TLS_SKIP_VERIFY__": settings["SPLUNK_HEC_TLS_SKIP_VERIFY"],
        "__SPLUNK_HEC_CA_FILE__": settings["SPLUNK_HEC_CA_FILE"].replace("\\", "\\\\").replace('"', '\\"'),
    }

    def fill(name):
        text = _read(name)
        for key, value in replacements.items():
            text = text.replace(key, value)
        if re.search(r"__[A-Z0-9_]+__", text):
            raise ValueError("unreplaced placeholder in " + name)
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
    gnmic += "\n" + settings["targets_yaml"]
    return {
        "gnmic.yaml": gnmic,
        "otel-agent.yaml": fill("otel-agent.yaml.template"),
        "indexes.conf": fill("indexes.conf"),
    }


def _instructions(settings):
    return """gNMI trial bundle

This is a lab bundle, not a production collector install.

1. Unpack this zip on the host that will dial the network devices.
2. Run: sudo ./install.sh
   On the Splunk server itself, as the Splunk user: ./install.sh --container
3. Watch the Build page in Splunk. Installer lines appear in the
   operations index ({ops}). gNMI updates appear in {events} and {metrics}.
4. To change a value, submit the form again, unpack the new zip, and run
   the install again.

The HEC token must be allowed to write to {events}, {metrics}, and {ops}.
Preview copies of the YAML use Linux paths. install.sh rewrites log paths
for the host it runs on.
""".format(
        ops=settings["SPLUNK_HEC_OPS_INDEX"],
        events=settings["SPLUNK_HEC_EVENTS_INDEX"],
        metrics=settings["SPLUNK_HEC_METRICS_INDEX"],
    )


def build(raw):
    settings = normalize(raw)
    preview = _fill_templates(settings, PREVIEW_EVENTS, PREVIEW_RUNTIME)
    install_sh = _fill_shell(_read("install.sh"), settings)
    files = {
        "install.sh": install_sh,
        "hec_log.py": _read("hec_log.py"),
        "gnmic.yaml": preview["gnmic.yaml"],
        "otel-agent.yaml": preview["otel-agent.yaml"],
        "targets.yaml": settings["targets_yaml"],
        "indexes.conf": preview["indexes.conf"],
        "INSTRUCTIONS.txt": _instructions(settings),
        "config/gnmic.yaml.template": _read("gnmic.yaml.template"),
        "config/otel-agent.yaml.template": _read("otel-agent.yaml.template"),
        "config/indexes.conf": _read("indexes.conf"),
        "config/targets.yaml": settings["targets_yaml"],
    }
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            info = zipfile.ZipInfo(name)
            mode = 0o100755 if name.endswith(".sh") or name.endswith(".py") else 0o100644
            info.external_attr = mode << 16
            archive.writestr(info, text.encode("utf-8"))
    public = {key: value for key, value in files.items() if key != "config/gnmic.yaml.template"}
    return {
        "files": public,
        "zip_base64": base64.b64encode(blob.getvalue()).decode("ascii"),
        "create_indexes": settings["create_indexes"],
        "indexes": {
            "events": settings["SPLUNK_HEC_EVENTS_INDEX"],
            "metrics": settings["SPLUNK_HEC_METRICS_INDEX"],
            "ops": settings["SPLUNK_HEC_OPS_INDEX"],
        },
    }
