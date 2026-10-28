"""REST endpoint that builds the trial bundle and, when asked, creates indexes and HEC."""

import base64
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_bundle

try:
    from urllib.parse import quote
except ImportError:
    from urllib import quote

try:
    from splunk.persistconn.application import PersistentServerConnectionApplication
except ImportError:  # local unit tests import render_bundle only
    PersistentServerConnectionApplication = object


CLUSTER_PEER_ROLES = {"cluster_peer", "cluster_slave"}
CLUSTER_MANAGER_ROLES = {"cluster_master", "cluster_manager"}
FORWARDER_ROLES = {"heavyweight_forwarder", "heavy_forwarder", "universal_forwarder"}
ACTIVE_CLUSTER_MODES = {"master", "slave", "manager", "peer", "searchhead", "search_head"}

HEC_ROOTS = (
    "/servicesNS/nobody/splunk_httpinput/data/inputs/http",
    "/services/data/inputs/http",
)
TOKEN_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
UUID_TEXT = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _http_status(exc):
    for attr in ("status", "statusCode", "code"):
        value = getattr(exc, attr, None)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    match = re.search(r"\b([45][0-9]{2})\b", str(exc))
    return int(match.group(1)) if match else 0


def _safe_detail(exc):
    text = UUID_TEXT.sub("[token]", str(exc))
    text = text.replace("\n", " ").replace("\r", " ")
    return text[:300]


def _token_from(data):
    entries = data.get("entry") or []
    if not entries:
        return ""
    content = entries[0].get("content") or {}
    token = content.get("token") or ""
    if not isinstance(token, str):
        return ""
    return token.strip()


def _token_name(raw):
    name = str(raw.get("hec_token_name") or "gnmi_trial").strip()
    if name.lower() == "http" or not TOKEN_NAME.match(name):
        raise ValueError("HEC token name must start with a letter and use only letters, numbers, _ and -")
    return name


def _create_index(session_key, name, datatype):
    import splunk.rest

    args = {"name": name}
    if datatype:
        args["datatype"] = datatype
    try:
        splunk.rest.simpleRequest(
            "/services/data/indexes",
            sessionKey=session_key,
            method="POST",
            postargs=args,
        )
        return {"name": name, "status": "created"}
    except Exception as exc:
        text = _safe_detail(exc)
        status = _http_status(exc)
        lowered = text.lower()
        if status == 409 or "already exists" in lowered:
            return {"name": name, "status": "exists"}
        return {"name": name, "status": "error", "detail": text}


def _create_indexes(session_key, indexes):
    specs = (
        (indexes["events"], None),
        (indexes["metrics"], "metric"),
        (indexes["ops"], None),
    )
    return [_create_index(session_key, name, datatype) for name, datatype in specs]


def _splunk_json(session_key, path, method, postargs=None):
    import splunk.rest

    kwargs = {
        "sessionKey": session_key,
        "method": method,
        "getargs": {"output_mode": "json"},
    }
    if postargs is not None:
        kwargs["postargs"] = postargs
    _response, content = splunk.rest.simpleRequest(path, **kwargs)
    if content is None:
        return {}
    if not isinstance(content, str):
        content = content.decode("utf-8", "replace")
    if not content.strip():
        return {}
    return json.loads(content)


def _hec_message(exc):
    status = _http_status(exc)
    if status in (401, 403):
        return "Splunk refused the HEC change. Sign in as a user who can edit HTTP Event Collector tokens."
    if status == 404:
        return "This Splunk instance did not expose the HTTP Event Collector endpoint."
    if status == 400:
        return (
            "Splunk rejected the HEC token. Create the three indexes first "
            "(leave that box checked), then submit again. " + _safe_detail(exc)
        )
    return "Could not configure HTTP Event Collector. " + _safe_detail(exc)


def _enable_hec(session_key):
    last = None
    for root in HEC_ROOTS:
        try:
            _splunk_json(
                session_key,
                root + "/http",
                "POST",
                {"disabled": "0", "enableSSL": "1"},
            )
            data = _splunk_json(session_key, root + "/http", "GET")
        except Exception as exc:
            last = exc
            if _http_status(exc) in (404, 0):
                continue
            raise ValueError(_hec_message(exc))
        entries = data.get("entry") or [{}]
        content = entries[0].get("content") or {}
        port = str(content.get("port") or "8088")
        if not port.isdigit():
            port = "8088"
        return root, port
    raise ValueError(_hec_message(last) if last else "Could not enable HTTP Event Collector.")


def _ensure_token(session_key, root, name, indexes, sourcetype):
    allowed = ",".join((indexes["events"], indexes["metrics"], indexes["ops"]))
    fields = {
        "index": indexes["events"],
        "indexes": allowed,
        "sourcetype": sourcetype,
        "disabled": "0",
        "useACK": "0",
        "description": "gNMI trial collector",
    }
    path = root + "/" + quote(name)
    try:
        data = _splunk_json(session_key, root, "POST", dict(fields, name=name))
        token = _token_from(data)
        if token:
            return token, "created"
    except Exception as exc:
        status = _http_status(exc)
        if status != 409 and "already exists" not in _safe_detail(exc).lower():
            raise ValueError(_hec_message(exc))
    try:
        data = _splunk_json(session_key, path, "POST", fields)
    except Exception as exc:
        raise ValueError(_hec_message(exc))
    token = _token_from(data)
    if not token:
        data = _splunk_json(session_key, path, "GET")
        token = _token_from(data)
    if not token:
        raise ValueError("The HEC token exists, but Splunk did not return its value.")
    return token, "updated"


def _apply_hec_port(body, port):
    url = str(body.get("SPLUNK_HEC_URL") or "")
    if port != "8088" and ":8088/" in url:
        body["SPLUNK_HEC_URL"] = url.replace(":8088/", ":" + port + "/", 1)


def _configure_hec(session_key, body, indexes):
    name = _token_name(body)
    root, port = _enable_hec(session_key)
    sourcetype = str(body.get("SPLUNK_HEC_EVENTS_SOURCETYPE") or "gnmi:event").strip()
    token, status = _ensure_token(session_key, root, name, indexes, sourcetype)
    _apply_hec_port(body, port)
    body["SPLUNK_HEC_TOKEN"] = token
    return {
        "status": status,
        "name": name,
        "port": port,
        "token": token,
        "listen": _hec_port_open(port),
    }


def _hec_port_open(port, attempts=4, pause=0.5):
    """Return whether localhost accepts TCP connections on the HEC port.

    This is a connection test only. It does not read the HEC certificate.
    """
    import socket
    import time

    try:
        target = int(port)
    except (TypeError, ValueError):
        return {"ok": False, "detail": "The HEC port was not a number, so it was not checked."}
    if target < 1 or target > 65535:
        return {"ok": False, "detail": "The HEC port was not a number, so it was not checked."}
    last = "the connection was refused"
    for _attempt in range(attempts):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        try:
            sock.connect(("127.0.0.1", target))
            return {
                "ok": True,
                "detail": "This server is accepting connections on 127.0.0.1:%s." % port,
            }
        except Exception as exc:
            text = str(exc).replace("\n", " ").strip()
            if text:
                last = text[:160]
        finally:
            sock.close()
        if _attempt + 1 < attempts and pause:
            time.sleep(pause)
    return {
        "ok": False,
        "detail": (
            "This server is not accepting connections on 127.0.0.1:%s yet (%s). "
            "Wait a few seconds and submit the form again. The collector reaches "
            "this Splunk only when that port is open."
        ) % (port, last),
    }


def _session_key(request):
    return ((request.get("session") or {}).get("authtoken")) or ""


def assess_lab(roles, cluster_mode="disabled", shc_enabled=False):
    """Decide whether this host may create indexes and HEC tokens.

    A lab all-in-one lists both indexer and search_head. Those stay allowed.
    Search heads that do not index, search-head cluster members, indexer
    cluster members, and forwarders stay blocked.
    """
    role_set = set()
    for role in roles or []:
        text = str(role).strip().lower()
        if text:
            role_set.add(text)
    mode = str(cluster_mode or "disabled").strip().lower()
    if mode not in ACTIVE_CLUSTER_MODES:
        mode = "disabled"

    if shc_enabled or role_set & {"shc_member", "shc_captain"}:
        return _lab_block(
            "search_head_cluster",
            "This Splunk is a search head cluster member. The form stays off so this app does not create indexes or HEC tokens on a distributed deployment.",
        )
    if "cluster_search_head" in role_set or mode in ("searchhead", "search_head"):
        return _lab_block(
            "cluster_search_head",
            "This Splunk is a search head for an indexer cluster. The form stays off so this app does not change a distributed deployment.",
        )
    if role_set & CLUSTER_MANAGER_ROLES or mode in ("master", "manager"):
        return _lab_block(
            "cluster_manager",
            "This Splunk is an indexer cluster manager. The form stays off. Create indexes and HEC tokens on a standalone lab server.",
        )
    if role_set & CLUSTER_PEER_ROLES or mode in ("slave", "peer"):
        return _lab_block(
            "cluster_peer",
            "This Splunk is an indexer cluster member. The form stays off so this app does not create local indexes or HEC tokens outside the cluster configuration.",
        )
    if role_set & FORWARDER_ROLES:
        return _lab_block(
            "heavy_forwarder",
            "This Splunk is a forwarder. The form stays off. It would send this trial's data on to other indexers instead of keeping it on a standalone lab server.",
        )
    if "indexer" not in role_set:
        if not role_set:
            return _lab_block(
                "unknown",
                "This server's role could not be confirmed. The form stays off. Setup runs only on a standalone lab Splunk that stores its own data.",
            )
        return _lab_block(
            "search_head",
            "This Splunk is a search head and does not store data on this instance. The form stays off so this app does not create indexes or HEC tokens here.",
        )
    return {
        "allowed": True,
        "kind": "standalone",
        "message": "This is a standalone lab server. The form can create indexes and an HEC token on this instance.",
    }


def _lab_block(kind, message):
    return {
        "allowed": False,
        "kind": kind,
        "message": message + " The notes and links above stay available.",
    }


def _entry_content(data):
    entries = data.get("entry") or []
    if not entries:
        return {}
    content = entries[0].get("content") or {}
    return content if isinstance(content, dict) else {}


def _role_list(content):
    roles = content.get("server_roles") or []
    if isinstance(roles, str):
        return [part.strip() for part in roles.split(",") if part.strip()]
    if isinstance(roles, list):
        return roles
    return []


def _read_lab(session_key):
    if not session_key:
        return assess_lab([], "disabled", False)
    try:
        info = _splunk_json(session_key, "/services/server/info", "GET")
    except Exception:
        return assess_lab([], "disabled", False)
    roles = _role_list(_entry_content(info))
    cluster_mode = "disabled"
    try:
        config = _splunk_json(session_key, "/services/cluster/config", "GET")
        cluster_mode = str(_entry_content(config).get("mode") or "disabled")
    except Exception:
        cluster_mode = "disabled"
    shc_enabled = False
    try:
        shcluster = _splunk_json(session_key, "/services/shcluster/config", "GET")
        disabled = _entry_content(shcluster).get("disabled")
        shc_enabled = disabled is not None and not _as_bool(disabled)
    except Exception:
        shc_enabled = False
    return assess_lab(roles, cluster_mode, shc_enabled)


def _container_root():
    home = os.environ.get("SPLUNK_HOME") or "/opt/splunk"
    return os.path.join(home, "var", "gnmi-collector")


def _pid_alive(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            pid = int(handle.read().strip())
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    # The installer is a child of this persistent handler. Reap it, or it stays a
    # zombie that still answers kill -0.
    try:
        reaped, _ = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            return False
    except (ChildProcessError, OSError):
        pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open("/proc/%d/stat" % pid, "r", encoding="utf-8") as handle:
            if handle.read().rsplit(")", 1)[-1].split()[0] == "Z":
                return False
    except (OSError, IndexError):
        pass
    return True


def _status_word(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            word = handle.readline().strip().lower()
    except OSError:
        return ""
    if word in ("running", "ok", "failed"):
        return word
    return ""


def _container_status():
    root = _container_root()
    status_path = os.path.join(root, "run", "install.status")
    pid_path = os.path.join(root, "run", "install.pid")
    state = _status_word(status_path)
    if state not in ("ok", "failed"):
        if _pid_alive(pid_path):
            state = "running"
        elif state == "running":
            state = "failed"
    if not state:
        state = "idle"
    run_dir = os.path.join(root, "run")
    return {
        "state": state,
        "otel_running": _pid_alive(os.path.join(run_dir, "otel.pid")),
        "gnmic_running": _pid_alive(os.path.join(run_dir, "gnmic.pid")),
    }


def _chmod_tree(path):
    for current, dirs, files in os.walk(path):
        os.chmod(current, 0o700)
        for name in files:
            file_path = os.path.join(current, name)
            mode = 0o700 if name.endswith(".sh") or name.endswith(".py") else 0o600
            os.chmod(file_path, mode)


def _extract_bundle(bundle, dest):
    raw = base64.b64decode(bundle["zip_base64"])
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for info in archive.infolist():
            name = info.filename
            if name.startswith("/") or ".." in name.split("/"):
                raise ValueError("The bundle contained an unsafe path.")
        archive.extractall(dest)
    _chmod_tree(dest)


# splunkd exports these for its own libraries. System tools such as curl break
# when they load Splunk's libcurl and OpenSSL, so only the python3 wrapper keeps them.
SPLUNK_LIB_ENV = (
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "OPENSSL_CONF",
    "OPENSSL_MODULES",
    "SSL_CERT_FILE",
    "PYTHONHTTPSVERIFY",
    "PYTHONPATH",
    "PYTHONHOME",
)


def _write_python_wrapper(bindir):
    home = os.environ.get("SPLUNK_HOME") or "/opt/splunk"
    python = None
    for name in ("python3", "python3.13", "python3.9"):
        candidate = os.path.join(home, "bin", name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            python = candidate
            break
    wrapper = os.path.join(bindir, "python3")
    if os.path.islink(wrapper) or os.path.exists(wrapper):
        os.remove(wrapper)
    if python is None:
        return
    lines = ["#!/bin/sh"]
    for key in ("LD_LIBRARY_PATH", "OPENSSL_CONF", "OPENSSL_MODULES", "SSL_CERT_FILE"):
        value = os.environ.get(key)
        if value:
            lines.append("export %s=%s" % (key, shlex.quote(value)))
    lines.append("exec %s \"$@\"" % shlex.quote(python))
    with open(wrapper, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    os.chmod(wrapper, stat.S_IRWXU)


def _installer_env(root):
    bindir = os.path.join(root, "bin")
    os.makedirs(bindir, mode=0o700, exist_ok=True)
    _write_python_wrapper(bindir)
    env = {key: value for key, value in os.environ.items() if key not in SPLUNK_LIB_ENV}
    env["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")
    env["GNMI_CONTAINER_PREFIX"] = root
    return env


def _start_container_install(bundle):
    root = _container_root()
    try:
        os.makedirs(root, mode=0o700, exist_ok=True)
    except OSError:
        return {
            "state": "failed",
            "detail": "The Splunk user cannot create the collector directory on this server.",
        }
    pid_path = os.path.join(root, "run", "install.pid")
    if _pid_alive(pid_path):
        return {
            "state": "running",
            "detail": "An install is already running on this server.",
        }
    stage = os.path.join(root, "bundle")
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(stage, mode=0o700)
    try:
        _extract_bundle(bundle, stage)
    except (OSError, ValueError, zipfile.BadZipFile):
        return {
            "state": "failed",
            "detail": "The bundle could not be unpacked on this server.",
        }
    log_dir = os.path.join(root, "logs")
    run_dir = os.path.join(root, "run")
    os.makedirs(log_dir, mode=0o700, exist_ok=True)
    os.makedirs(run_dir, mode=0o700, exist_ok=True)
    status_path = os.path.join(run_dir, "install.status")
    with open(status_path, "w", encoding="utf-8") as handle:
        handle.write("running\n")
    os.chmod(status_path, stat.S_IRUSR | stat.S_IWUSR)
    log_path = os.path.join(log_dir, "install.log")
    log_handle = open(log_path, "ab")
    env = _installer_env(root)
    try:
        bash = "/usr/bin/bash" if os.path.isfile("/usr/bin/bash") else "bash"
        proc = subprocess.Popen(
            [bash, os.path.join(stage, "install.sh"), "--container"],
            cwd=stage,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    except OSError:
        log_handle.close()
        with open(status_path, "w", encoding="utf-8") as handle:
            handle.write("failed\n")
        return {
            "state": "failed",
            "detail": "The installer could not be started on this server.",
        }
    log_handle.close()
    with open(pid_path, "w", encoding="utf-8") as handle:
        handle.write(str(proc.pid) + "\n")
    os.chmod(pid_path, stat.S_IRUSR | stat.S_IWUSR)
    return {
        "state": "started",
        "detail": "Installing gnmic and the OpenTelemetry Collector as the Splunk user. The live installer table below shows each line. The collector download is about 430 MB.",
    }


def _preview(bundle):
    return {
        "gnmic.yaml": bundle["files"]["gnmic.yaml"],
        "otel-agent.yaml": bundle["files"]["otel-agent.yaml"],
        "targets.yaml": bundle["files"]["targets.yaml"],
        "indexes.conf": bundle["files"]["indexes.conf"],
        "INSTRUCTIONS.txt": bundle["files"]["INSTRUCTIONS.txt"],
    }


class Handler(PersistentServerConnectionApplication):
    def __init__(self, command_line=None, command_arg=None):
        # appserver.py constructs the handler with the script path and an extra argument.
        PersistentServerConnectionApplication.__init__(self)

    def handle(self, in_string):
        try:
            request = json.loads(in_string)
        except Exception:
            return _response(400, {"error": "The request was not JSON."})
        method = request.get("method", "POST").upper()
        if method == "GET":
            lab = _read_lab(_session_key(request))
            if lab.get("allowed"):
                lab["container"] = _container_status()
            return _response(200, lab)
        if method != "POST":
            return _response(405, {"error": "Use POST."})
        lab = _read_lab(_session_key(request))
        if not lab["allowed"]:
            return _response(403, {"error": lab["message"], "lab": lab})
        raw_payload = request.get("payload") or "{}"
        try:
            body = raw_payload if isinstance(raw_payload, dict) else json.loads(raw_payload)
        except Exception:
            return _response(400, {"error": "The form body was not JSON."})

        create_indexes = _as_bool(body.get("create_indexes"))
        create_hec = _as_bool(body.get("create_hec"))
        probe = dict(body)
        if create_hec and not str(probe.get("SPLUNK_HEC_TOKEN") or "").strip():
            probe["SPLUNK_HEC_TOKEN"] = "pending-hec-token"
        try:
            checked = render_bundle.normalize(probe)
        except ValueError as exc:
            return _response(400, {"error": str(exc)})

        index_results = []
        hec_result = None
        indexes = {
            "events": checked["SPLUNK_HEC_EVENTS_INDEX"],
            "metrics": checked["SPLUNK_HEC_METRICS_INDEX"],
            "ops": checked["SPLUNK_HEC_OPS_INDEX"],
        }
        session_key = _session_key(request) if (create_indexes or create_hec) else ""
        if create_hec:
            try:
                _token_name(body)
            except ValueError as exc:
                return _response(400, {"error": str(exc)})
            if not session_key:
                return _response(400, {
                    "error": "Splunk did not pass a session, so the HEC token was not created.",
                })
        if create_indexes:
            if session_key:
                index_results = _create_indexes(session_key, indexes)
            else:
                index_results = [{
                    "name": "",
                    "status": "error",
                    "detail": "Splunk did not pass a session, so indexes were not created. Install indexes.conf from the zip on the indexer.",
                }]
            failed = [item for item in index_results if item.get("status") == "error"]
            if failed and create_hec:
                return _response(400, {
                    "error": "Indexes were not ready, so the HEC token was not created.",
                    "index_results": index_results,
                })
        if create_hec:
            try:
                hec_result = _configure_hec(session_key, body, indexes)
            except ValueError as exc:
                return _response(400, {"error": str(exc), "index_results": index_results})

        try:
            bundle = render_bundle.build(body)
        except ValueError as exc:
            return _response(400, {"error": str(exc), "index_results": index_results})
        except Exception:
            return _response(500, {"error": "Could not build the configuration files."})

        payload = {
            "files": _preview(bundle),
            "zip_base64": bundle["zip_base64"],
            "indexes": bundle["indexes"],
            "index_results": index_results,
        }
        if hec_result:
            payload["hec"] = hec_result
        if _as_bool(body.get("install_container")):
            payload["container"] = _start_container_install(bundle)
        return _response(200, payload)


def _response(status, payload):
    return {
        "status": status,
        "headers": {"Content-Type": "application/json"},
        "payload": json.dumps(payload),
    }


def handle(in_string):
    return Handler().handle(in_string)
