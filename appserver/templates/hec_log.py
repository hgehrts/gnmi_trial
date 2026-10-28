#!/usr/bin/env python3
"""Read installer lines from a fifo and post each one to Splunk HEC.

Stdout is left unused so a shell tee can own the console transcript.
Failures are counted and reported once on stderr, without the HEC token.
"""

import json
import os
import ssl
import sys
import time
import urllib.request


def context():
    skip = os.environ.get("SPLUNK_HEC_TLS_SKIP_VERIFY", "true") == "true"
    ca_file = os.environ.get("SPLUNK_HEC_CA_FILE") or ""
    if skip:
        ctx = ssl._create_unverified_context()
        return ctx
    if ca_file:
        return ssl.create_default_context(cafile=ca_file)
    return ssl.create_default_context()


def main():
    fifo_path = os.environ["HEC_LOG_FIFO"]
    url = os.environ.get("SPLUNK_HEC_URL", "")
    token = os.environ.get("SPLUNK_HEC_TOKEN", "")
    index = os.environ.get("SPLUNK_HEC_OPS_INDEX", "gnmi_ops")
    sourcetype = os.environ.get("SPLUNK_HEC_SETUP_SOURCETYPE", "gnmi:setup")
    run = os.environ.get("GNMI_INSTALL_RUN") or time.strftime("%Y%m%dT%H%M%S")
    ctx = context()
    failed = 0
    seq = 0
    with open(fifo_path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            message = line.rstrip("\n")
            if not url or not token:
                continue
            seq += 1
            payload = json.dumps(
                {
                    "index": index,
                    "sourcetype": sourcetype,
                    "source": "install.sh",
                    "time": round(time.time(), 3),
                    "event": {"message": message, "stage": "setup", "run": run, "seq": seq},
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=payload,
                headers={"Authorization": "Splunk " + token},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, context=ctx, timeout=5) as response:
                    response.read()
            except Exception:
                failed += 1
    if failed:
        sys.stderr.write(
            "Could not send %d installer line(s) to Splunk HEC. "
            "The lines are still on the console.\n" % failed
        )


if __name__ == "__main__":
    main()
