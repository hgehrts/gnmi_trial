# gNMI Trial Setup

Version **1.5.1**. Lab helper for learning gNMI with Splunk Enterprise. It is not a production collector.

gnmic dials network devices with gNMI Subscribe over gRPC. Metrics go to the Splunk OpenTelemetry Collector with OTLP. Events are JSON lines that the collector tails. The collector posts both to Splunk HEC over HTTPS.

OpenConfig paths are the starting point for Cisco IOS XE, IOS XR, NX-OS, Junos, Arista EOS, Nokia SR OS, and Huawei gNMI. Nokia SR Linux uses its native YANG model. Its target block subscribes to the `srl-` names: `/interface`, `/platform/control/cpu`, `/platform/control/memory`, `/system/lldp`, and `/network-instance/protocols/bgp`. SR Linux rejects the OpenConfig paths, including `/system/memory/state`.

## Install the app

Download `gnmi_trial-1.5.1.spl` from the [latest release](https://github.com/hgehrts/gnmi_trial/releases/latest). In Splunk Web, open **Apps → Manage Apps → Install app from file** and upload that file.

The same file is in [`dist/gnmi_trial-1.5.1.spl`](dist/gnmi_trial-1.5.1.spl). `dist/gnmi_trial-1.5.1.spl.sha256` is its SHA-256 checksum. A `.spl` file is a tar.gz whose top directory is `gnmi_trial`.

To build it yourself:

```bash
./package.sh
```

That writes `dist/gnmi_trial-<version>.spl`. The version is read from `default/app.conf`. Tag the GitHub release `v<version>` and attach that `.spl` file. The newest release is what [latest](https://github.com/hgehrts/gnmi_trial/releases/latest) points at.

Restart Splunk after installing the app. On a standalone lab server, open **gNMI Trial Setup**.

The form stays off on a dedicated search head, a search head cluster member, an indexer cluster peer or manager, and a heavy forwarder. Installing the app does not create indexes by itself.

## Setup pages

1. **Splunk.** HEC URL, token, indexes, sourcetypes, and the checkboxes that create the indexes and the HEC token.
2. **OpenTelemetry.** OTLP listen addresses and the collector version.
3. **gNMI.** Device account, sample interval, TLS, and the gnmic version.
4. **Devices.** How to enable gNMI, and `targets.yaml`.
5. **Build.** Download the bundle, or install it on this server. The searches on this page show installer lines, events, and metrics.

The HEC token field starts as `00000000-0000-0000-0000-000000000000`. With **Enable HEC** checked, Splunk replaces that sample with the token it creates.

## Two ways to run the collector

**Download configuration** writes `gnmi-trial-bundle.zip`. Unpack it on a host that can reach the devices and run `sudo ./install.sh`. That path uses root. Linux uses systemd. macOS uses launchd.

**Install on this server** runs `install.sh --container` as the Splunk user. It does not need root or systemd. Programs and configs go under `$SPLUNK_HOME/var/gnmi-collector`, which keeps the token and device password out of the app directory. In the lab container this is the `splunk` user. Restarting Splunk stops those processes until you install again. After you press the button, the Build page shows a live table of the latest installer run that refreshes every 3 seconds. The installer starts with Splunk's library variables removed, so system tools such as curl do not load Splunk's libcurl and OpenSSL.

After that install, the Build page shows editors for `gnmic.yaml`, `otel-agent.yaml`, and `targets.yaml`. **Save** writes those three files on this server. **Restart gnmic** and **Restart OpenTelemetry** stop that process and start it again so it reads the saved file. gnmic does not apply config changes until it is restarted. The device password and HEC token stay in those collector files, not in the app directory.

**gNMI Operation** shows whether gnmic is connected and why a target fails. gnmic writes JSON logs (`log: true`, `log-format: json`) to the runtime log, which lands in the operations index as `gnmi:runtime`. Splunk adds `grpc_code` and a plain `failure_cause`. gnmic's local API on `127.0.0.1:7890` serves `gnmic_target_up`, `gnmic_target_connection_state`, and the subscribe counters. The collector scrapes those, plus its own export counters on `127.0.0.1:8888`, into the metrics index. The page also lists the debugging commands (port check, `gnmic capabilities`, `--debug`, `/api/v1/targets`).

The container, or the Splunk server, has to be able to reach the device addresses. An address of `127.0.0.1` is this Splunk server, not a simulator on another machine.

## Dark theme

The app supports Splunk’s light and dark themes. Choose the theme in **User Profile, Preferences**.

## License

Apache License 2.0, the same license as [TA_unifi_ng](https://github.com/hgehrts/TA_unifi_ng) and [unifi_app_for_splunk](https://github.com/hgehrts/unifi_app_for_splunk). See [LICENSE](LICENSE).
