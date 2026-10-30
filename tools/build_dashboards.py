#!/usr/bin/env python3
"""Generate the Dashboard Studio views gnmi_data.xml and gnmi_operation.xml.

Run from the app directory after changing a panel:

    python3 tools/build_dashboards.py

The views read index names through the macros in default/macros.conf, which
the Build step rewrites when a user picks other index names.
"""
import json
import os

APP = "gnmi_trial"
VIEWS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "default", "data", "ui", "views")

GREEN = "#118832"
YELLOW = "#cba700"
RED = "#d41f1f"
GREY = "#5c6773"
BLUE = "#006eaa"

STATIC_ALL = [["All"], ["*"]]


def search(query, earliest=None, latest=None, refresh="30s", name=None):
    options = {"query": query.strip(), "refresh": refresh, "refreshType": "delay"}
    if earliest:
        options["queryParameters"] = {"earliest": earliest, "latest": latest or "now"}
    source = {"type": "ds.search", "options": options}
    if name:
        source["name"] = name
    return source


def dropdown(title, token, source, statics=True):
    if not statics:
        return {
            "type": "input.dropdown",
            "title": title,
            "dataSources": {"primary": source},
            "options": {"token": token, "defaultValue": "*", "items": ">frame(label, value) | objects()"},
            "context": {
                "formattedConfig": {"number": {"prefix": ""}},
                "label": ">primary | seriesByName(\"label\") | renameSeries(\"label\") | formatByType(formattedConfig)",
                "value": ">primary | seriesByName(\"value\") | renameSeries(\"value\") | formatByType(formattedConfig)",
            },
        }
    return {
        "type": "input.dropdown",
        "title": title,
        "dataSources": {"primary": source},
        "options": {
            "token": token,
            "defaultValue": "*",
            "items": ">frame(label, value) | prepend(formattedStatics) | objects()",
        },
        "context": {
            "formattedConfig": {"number": {"prefix": ""}},
            "formattedStatics": ">statics | formatByType(formattedConfig)",
            "statics": STATIC_ALL,
            "label": ">primary | seriesByName(\"label\") | renameSeries(\"label\") | formatByType(formattedConfig)",
            "value": ">primary | seriesByName(\"value\") | renameSeries(\"value\") | formatByType(formattedConfig)",
        },
    }


def timerange(default="-60m,now"):
    return {"type": "input.timerange", "title": "Time range", "options": {"token": "tr", "defaultValue": default}}


def single(title, source, unit=None, ranges=None, description=None, handlers=None, under=None):
    options = {"majorFontSize": 44, "sparklineDisplay": "off", "trendDisplay": "off"}
    if unit:
        options["unit"] = unit
        options["unitPosition"] = "after"
    if under:
        options["underLabel"] = under
    viz = {"type": "splunk.singlevalue", "title": title, "dataSources": {"primary": source}, "options": options}
    if ranges:
        options["backgroundColor"] = "> majorValue | rangeValue(backgroundColorEditorConfig)"
        viz["context"] = {"backgroundColorEditorConfig": ranges}
    if description:
        viz["description"] = description
    if handlers:
        viz["eventHandlers"] = handlers
    return viz


def table(title, source, colors=None, handlers=None, description=None, count=10, widths=None):
    options = {"count": count, "headerVisibility": "fixed", "showInternalFields": False}
    viz = {"type": "splunk.table", "title": title, "dataSources": {"primary": source}, "options": options}
    if widths:
        options["columnFormat"] = {column: {"width": width} for column, width in widths.items()}
    if colors:
        options.setdefault("columnFormat", {})
        viz["context"] = {}
        for column, mapping in colors.items():
            key = column.replace(" ", "_") + "Colors"
            options["columnFormat"].setdefault(column, {})["rowBackgroundColors"] = (
                "> table | seriesByName(\"" + column + "\") | matchValue(" + key + ")"
            )
            viz["context"][key] = [{"match": value, "value": color} for value, color in mapping] + [{"value": GREY}]
    if description:
        viz["description"] = description
    if handlers:
        viz["eventHandlers"] = handlers
    return viz


def chart(kind, title, source, options=None, handlers=None, description=None):
    viz = {"type": kind, "title": title, "dataSources": {"primary": source}, "options": options or {}}
    if description:
        viz["description"] = description
    if handlers:
        viz["eventHandlers"] = handlers
    return viz


def markdown(text):
    return {"type": "splunk.markdown", "options": {"markdown": text.strip(), "fontSize": "default"}}


def set_tokens(*pairs):
    return {"type": "drilldown.setToken", "options": {"tokens": [{"token": token, "key": key} for token, key in pairs]}}


def to_dashboard(dashboard, *pairs):
    return {
        "type": "drilldown.linkToDashboard",
        "options": {
            "app": APP,
            "dashboard": dashboard,
            "newTab": False,
            "tokens": [{"token": token, "value": value} for token, value in pairs],
        },
    }


def to_search(query, earliest="$tr.earliest$", latest="$tr.latest$"):
    return {
        "type": "drilldown.linkToSearch",
        "options": {"type": "custom", "query": query.strip(), "earliest": earliest, "latest": latest, "newTab": True},
    }


def grid(rows):
    structure = []
    y = 0
    for height, items in rows:
        x = 0
        for item, width in items:
            structure.append({"item": item, "type": "block", "position": {"x": x, "y": y, "w": width, "h": height}})
            x += width
        if x != 1440:
            raise ValueError("row at y=%d is %d wide" % (y, x))
        y += height
    return {"type": "grid", "options": {"width": 1440, "height": y}, "structure": structure}


def write(name, label, description, definition):
    for key in ("visualizations", "dataSources", "inputs"):
        definition.setdefault(key, {})
    body = json.dumps(definition, indent=2, ensure_ascii=False)
    if "]]>" in body:
        raise ValueError("definition must not contain ]]>")
    xml = (
        '<dashboard version="2" theme="dark">\n'
        "  <label>" + label + "</label>\n"
        "  <description>" + description + "</description>\n"
        "  <definition><![CDATA[\n" + body + "\n]]></definition>\n"
        '  <meta type="hiddenElements"><![CDATA[\n'
        '{"hideEdit": false, "hideOpenInSearch": false, "hideExport": false}\n'
        "]]></meta>\n"
        "</dashboard>\n"
    )
    path = os.path.join(VIEWS, name + ".xml")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(xml)
    print("wrote", path)


# Device metrics carry the gnmic subscription as the subscription-name
# dimension. gnmic's own counters (gnmic_*) and collector metrics do not.
METRIC_SHORT = 'replace(metric_name, "(srl_nokia|openconfig|Cisco_IOS_XE|cisco|junos|arista|huawei)_[A-Za-z0-9_]*?:", "")'
SERIES_LABEL = (
    '| eval label=_timeseries\n'
    '| rex field=label mode=sed "s/\\"(host|metric_name|metric_type|service\\.name|source|sourcetype|telemetry\\.source|subscription-name)\\":\\"[^\\"]*\\",?//g"\n'
    '| rex field=label mode=sed "s/[{}\\"]//g"\n'
    '| rex field=label mode=sed "s/,$//"\n'
    '| rex field=label mode=sed "s/:/=/g"\n'
    '| rex field=label mode=sed "s/,/ /g"\n'
)
CPU_METRICS = '(metric_name="*cpu_total_instant" OR metric_name="*cpu_utilization_state_instant" OR metric_name="*cpu_utilization_instant")'
UP_DOWN = [("up", GREEN), ("UP", GREEN), ("enable", GREEN), ("ENABLED", GREEN), ("down", RED), ("DOWN", RED),
           ("down (disabled)", GREY), ("disable", GREY), ("DISABLED", GREY)]


def gnmi_data():
    ds = {}
    ds["ds_targets"] = search("""
| tstats count WHERE `gnmi_events_index` BY tags.source
| rename tags.source AS value
| eval label=value
| table label value""", refresh="5m")
    ds["ds_subscriptions"] = search("""
| tstats count WHERE `gnmi_events_index` tags.source="$target$" BY name
| eval label=name, value=name
| table label value""", refresh="5m")
    ds["ds_interfaces"] = search("""
| tstats count WHERE `gnmi_events_index` tags.source="$target$" tags.interface_name=* BY tags.interface_name
| rename tags.interface_name AS value
| eval label=value
| table label value""", refresh="5m")

    ds["ds_kpi_targets"] = search("""
| tstats dc(tags.source) AS targets WHERE `gnmi_events_index` tags.source="$target$" name="$subscription$"
| eval targets=coalesce(targets, 0)""")
    ds["ds_kpi_updates"] = search("""
| tstats count AS updates WHERE `gnmi_events_index` tags.source="$target$" name="$subscription$"
| eval updates=coalesce(updates, 0)""")
    ds["ds_kpi_samples"] = search("""
| mstats count(_value) AS samples WHERE `gnmi_metrics_index` metric_name="*" "subscription-name"="$subscription$" extracted_source="$target$"
| append [| makeresults | eval samples=0]
| stats sum(samples) AS samples""")
    ds["ds_kpi_interfaces"] = search("""
| mstats latest_time(_value) AS t WHERE `gnmi_metrics_index` metric_name="*oper_stat*" extracted_source="$target$" interface_name="$interface$" BY extracted_source interface_name value
| sort 0 - t
| dedup extracted_source interface_name
| stats count(eval(lower(value)="up")) AS up count AS total
| eval interfaces=coalesce(up, 0)." of ".coalesce(total, 0)
| fields interfaces""")
    ds["ds_kpi_cpu"] = search("""
| mstats latest(_value) AS cpu WHERE `gnmi_metrics_index` """ + CPU_METRICS + """ extracted_source="$target$" BY extracted_source
| stats max(cpu) AS cpu
| eval cpu=round(cpu, 0)""")
    ds["ds_kpi_memory"] = search("""
| mstats latest(_value) AS memory WHERE `gnmi_metrics_index` metric_name="*memory_utilization" extracted_source="$target$" BY extracted_source
| stats max(memory) AS memory
| eval memory=round(memory, 0)""")

    ds["ds_updates_time"] = search("""
| tstats count WHERE `gnmi_events_index` tags.source="$target$" name="$subscription$" BY _time name span=1m
| timechart span=1m sum(count) AS updates BY name""")
    ds["ds_streams"] = search("""
| tstats count AS updates latest(_time) AS last_update WHERE `gnmi_events_index` tags.source="$target$" name="$subscription$" BY tags.source name
| rename tags.source AS target, name AS subscription
| eval age_s=now()-last_update, state=if(age_s<=180, "streaming", "stale"), last_update=strftime(last_update, "%Y-%m-%d %H:%M:%S")
| table target subscription state updates last_update
| sort target subscription""")

    ds["ds_interfaces_table"] = search("""
| mstats latest_time(_value) AS t WHERE `gnmi_metrics_index` extracted_source="$target$" interface_name="$interface$" (metric_name="*admin_stat*" OR metric_name="*oper_stat*") BY extracted_source interface_name metric_name value
| eval field=if(like(metric_name, "%admin_stat%"), "admin", "oper")
| sort 0 - t
| dedup extracted_source interface_name field
| eval {field}=value
| stats values(admin) AS admin values(oper) AS oper BY extracted_source interface_name
| join type=left extracted_source interface_name
    [| mstats earliest(_value) AS first latest(_value) AS last earliest_time(_value) AS t0 latest_time(_value) AS t1 WHERE `gnmi_metrics_index` extracted_source="$target$" interface_name="$interface$" (metric_name="*_in_octets" OR metric_name="*_out_octets" OR metric_name="*_in_error*" OR metric_name="*_out_error*" OR metric_name="*_in_discard*" OR metric_name="*_out_discard*") BY extracted_source interface_name metric_name
    | eval k=case(like(metric_name, "%in_octets"), "in_kbps", like(metric_name, "%out_octets"), "out_kbps", like(metric_name, "%in_error%"), "in_errors", like(metric_name, "%out_error%"), "out_errors", like(metric_name, "%in_discard%"), "in_discards", like(metric_name, "%out_discard%"), "out_discards")
    | eval d=last-first, v=case(d<0, null(), like(k, "%kbps") AND t1>t0, round(d*8/(t1-t0)/1000, 2), like(k, "%kbps"), null(), true(), round(d, 0))
    | eval {k}=v
    | stats values(in_kbps) AS in_kbps values(out_kbps) AS out_kbps values(in_errors) AS in_errors values(out_errors) AS out_errors values(in_discards) AS in_discards values(out_discards) AS out_discards BY extracted_source interface_name]
| rename extracted_source AS target, interface_name AS interface
| eval oper=if(match(lower(admin), "^disable") AND lower(oper)="down", "down (disabled)", oper),
       sort_key=if(lower(oper)="up", 0, 1)
| sort 0 sort_key target interface
| eval errors=if(isnull(in_errors) AND isnull(out_errors), null(), coalesce(in_errors, 0)+coalesce(out_errors, 0)),
       discards=if(isnull(in_discards) AND isnull(out_discards), null(), coalesce(in_discards, 0)+coalesce(out_discards, 0))
| table target interface admin oper in_kbps out_kbps errors discards""")
    ds["ds_traffic"] = search("""
| mstats max(_value) AS v WHERE `gnmi_metrics_index` extracted_source="$target$" interface_name="$interface$" (metric_name="*_in_octets" OR metric_name="*_out_octets") span=1m BY extracted_source interface_name metric_name
| sort 0 _time
| streamstats current=f global=f window=1 last(v) AS prev last(_time) AS pt BY extracted_source interface_name metric_name
| where isnotnull(prev) AND v>=prev AND _time>pt
| eval series=extracted_source." ".interface_name.if(like(metric_name, "%in_octets"), " in", " out"),
       kbps=round((v-prev)*8/(_time-pt)/1000, 2)
| timechart span=1m limit=20 useother=f max(kbps) BY series""")
    ds["ds_cpu"] = search("""
| mstats avg(_value) AS cpu WHERE `gnmi_metrics_index` """ + CPU_METRICS + """ extracted_source="$target$" span=1m BY extracted_source
| timechart span=1m limit=20 useother=f avg(cpu) BY extracted_source""")
    ds["ds_memory"] = search("""
| mstats avg(_value) AS memory WHERE `gnmi_metrics_index` metric_name="*memory_utilization" extracted_source="$target$" span=1m BY extracted_source
| timechart span=1m limit=20 useother=f avg(memory) BY extracted_source""")

    ds["ds_metric_catalog"] = search("""
| mstats count(_value) AS samples latest(_value) AS latest min(_value) AS min max(_value) AS max WHERE `gnmi_metrics_index` metric_name="*" "subscription-name"="$subscription$" extracted_source="$target$" BY metric_name "subscription-name" extracted_source
| eval metric=""" + METRIC_SHORT + """, latest=round(latest, 2), min=round(min, 2), max=round(max, 2)
| rename "subscription-name" AS subscription, extracted_source AS target
| rename metric_name AS _metric_name
| table metric subscription target samples latest min max _metric_name
| sort subscription metric""")
    ds["ds_metric_trend"] = search("""
| mstats max(_value) AS v WHERE `gnmi_metrics_index` metric_name="$metric$" extracted_source="$target$" span=1m BY _timeseries
| eval kind=if(match(_timeseries, "\\"metric_type\\":\\"Gauge\\""), "gauge", "counter")
""" + SERIES_LABEL + """| sort 0 _time
| streamstats current=f global=f window=1 last(v) AS prev last(_time) AS pt BY label
| eval value=if(kind="counter", if(v>=prev AND _time>pt, round((v-prev)/(_time-pt), 3), null()), v)
| timechart span=1m limit=20 useother=f max(value) BY label""")

    ds["ds_events"] = search("""
`gnmi_events_index` tags.source="$target$" name="$subscription$"
| where "$interface$"="*" OR 'tags.interface_name'=="$interface$"
| head 200
| eval target='tags.source', interface='tags.interface_name'
| foreach values.* [ eval update=mvappend(update, replace(replace("<<MATCHSTR>>", "[A-Za-z0-9_]+-[A-Za-z0-9-]+:", ""), "^.*/([^/]+/[^/]+)$", "\\1")."=".'<<FIELD>>') ]
| table _time target name interface update
| rename name AS subscription""", refresh="30s")

    viz = {}
    viz["viz_intro"] = markdown("""
Values that the devices stream over gNMI. **Events** (index macro `gnmi_events_index`) hold every update as JSON. **Metrics** (macro `gnmi_metrics_index`) hold the same numbers as time series. Click a row or a chart series to filter the rest of the page. Use **Open in Search** on any panel for the full query.
""")
    viz["viz_kpi_targets"] = single("Targets sending data", "ds_kpi_targets")
    viz["viz_kpi_updates"] = single("gNMI updates (events)", "ds_kpi_updates", handlers=[to_search(
        '`gnmi_events_index` tags.source="$target$" name="$subscription$"')])
    viz["viz_kpi_samples"] = single("Metric samples", "ds_kpi_samples", handlers=[to_search(
        '| mstats count(_value) AS samples WHERE `gnmi_metrics_index` metric_name="*" "subscription-name"="$subscription$" extracted_source="$target$" BY metric_name')])
    viz["viz_kpi_interfaces"] = single("Interfaces oper up", "ds_kpi_interfaces")
    viz["viz_kpi_cpu"] = single("CPU (latest, busiest CPU)", "ds_kpi_cpu", unit="%", ranges=[
        {"to": 70, "value": GREEN}, {"from": 70, "to": 90, "value": YELLOW}, {"from": 90, "value": RED}])
    viz["viz_kpi_memory"] = single("Memory used (latest)", "ds_kpi_memory", unit="%", ranges=[
        {"to": 75, "value": GREEN}, {"from": 75, "to": 90, "value": YELLOW}, {"from": 90, "value": RED}])

    viz["viz_updates_time"] = chart("splunk.column", "Updates per minute by subscription (click a series to filter)", "ds_updates_time", {
        "stackMode": "stacked", "legendDisplay": "bottom", "yAxisTitleText": "updates", "xAxisTitleVisibility": "hide",
    }, handlers=[set_tokens(("subscription", "name"))])
    viz["viz_streams"] = table("Streams by target and subscription (click to filter)", "ds_streams",
                               colors={"state": [("streaming", GREEN), ("stale", YELLOW)]},
                               handlers=[set_tokens(("target", "row.target.value"), ("subscription", "row.subscription.value"))])

    viz["viz_interfaces"] = table("Interfaces: state, average kbit/s, errors and discards in the time range (click to focus)", "ds_interfaces_table",
                                  colors={"oper": UP_DOWN, "admin": UP_DOWN},
                                  handlers=[set_tokens(("interface", "row.interface.value"))], count=12,
                                  widths={"target": 110, "interface": 130})
    viz["viz_traffic"] = chart("splunk.line", "Interface traffic in kbit/s (interface: $interface$)", "ds_traffic", {
        "legendDisplay": "bottom", "yAxisTitleText": "kbit/s", "xAxisTitleVisibility": "hide", "nullValueDisplay": "connect",
    })
    viz["viz_cpu"] = chart("splunk.line", "CPU utilization %", "ds_cpu", {
        "legendDisplay": "bottom", "yAxisMin": 0, "yAxisMax": 100, "xAxisTitleVisibility": "hide", "yAxisTitleText": "%", "nullValueDisplay": "connect",
    })
    viz["viz_memory"] = chart("splunk.area", "Memory utilization %", "ds_memory", {
        "legendDisplay": "bottom", "yAxisMin": 0, "yAxisMax": 100, "xAxisTitleVisibility": "hide", "yAxisTitleText": "%", "nullValueDisplay": "connect",
    })
    viz["viz_metric_catalog"] = table("Device metrics (click a metric to plot it)", "ds_metric_catalog",
                                      handlers=[set_tokens(("metric", "row._metric_name.value"))], count=12,
                                      widths={"metric": 380, "subscription": 150, "target": 110})
    viz["viz_metric_trend"] = chart("splunk.line", "Selected metric: gauges as value, counters as rate per second", "ds_metric_trend", {
        "legendDisplay": "bottom", "xAxisTitleVisibility": "hide", "yAxisTitleVisibility": "hide", "nullValueDisplay": "connect",
    }, description="$metric$")
    viz["viz_events"] = table("Latest device updates (events, click to open the raw events)", "ds_events", handlers=[to_search(
        '`gnmi_events_index` tags.source="$row.target.value$" name="$row.subscription.value$"')], count=15)

    inputs = {
        "input_tr": timerange(),
        "input_target": dropdown("Target", "target", "ds_targets"),
        "input_subscription": dropdown("Subscription", "subscription", "ds_subscriptions"),
        "input_interface": dropdown("Interface", "interface", "ds_interfaces"),
    }
    layout = grid([
        (70, [("viz_intro", 1440)]),
        (130, [("viz_kpi_targets", 240), ("viz_kpi_updates", 240), ("viz_kpi_samples", 240),
               ("viz_kpi_interfaces", 240), ("viz_kpi_cpu", 240), ("viz_kpi_memory", 240)]),
        (330, [("viz_updates_time", 840), ("viz_streams", 600)]),
        (420, [("viz_interfaces", 840), ("viz_traffic", 600)]),
        (300, [("viz_cpu", 720), ("viz_memory", 720)]),
        (440, [("viz_metric_catalog", 840), ("viz_metric_trend", 600)]),
        (480, [("viz_events", 1440)]),
    ])
    layout["globalInputs"] = ["input_tr", "input_target", "input_subscription", "input_interface"]
    definition = {
        "title": "gNMI Data",
        "description": "Events and metrics that the devices stream over gNMI.",
        "inputs": inputs,
        "defaults": {
            "dataSources": {"ds.search": {"options": {"queryParameters": {"earliest": "$tr.earliest$", "latest": "$tr.latest$"}}}},
            "tokens": {"default": {"metric": {"value": "*cpu_total_instant"}}},
        },
        "visualizations": viz,
        "dataSources": ds,
        "layout": layout,
    }
    write("gnmi_data", "gNMI Data", "Events and metrics that the devices stream over gNMI.", definition)


OPS_GUIDE = """
### How gnmic works

For every target, gnmic opens one gRPC channel (TLS unless `insecure` is true), sends the username and password as gRPC metadata, and starts one gNMI Subscribe RPC per subscription. The device then streams updates. When a channel or an RPC fails, gnmic logs the error and tries again after the retry timer (30s in this lab). It keeps running while the device is down.

### Three signals in Splunk

- **Runtime log** (macro `gnmi_ops_index`, sourcetype `gnmi:runtime`). gnmic writes JSON lines with `level`, `msg`, `target`, `subscription`, and `err`. A connection problem appears as `level=ERROR msg="subscription receive error"`. The `err` text holds the gRPC status code, for example `code = Unavailable ... dial tcp 10.0.0.1:9339: i/o timeout`. This app adds `grpc_code` and a plain `failure_cause`.
- **Target metrics** (macro `gnmi_metrics_index`). `gnmic_target_up` is 1 when the gNMI connection is established. `gnmic_target_connection_state` is the gRPC channel state: 0 UNKNOWN, 1 IDLE, 2 CONNECTING, 3 READY, 4 TRANSIENT_FAILURE, 5 SHUTDOWN.
- **Subscribe counters**. `gnmic_subscribe_number_of_failed_subscribe_request_messages_total` counts Subscribe RPCs that could not start. `gnmic_subscribe_number_of_received_subscribe_response_messages_total` counts updates received. That counter only exists once data flows, so a missing counter also means no data.

The collector scrapes those metrics from gnmic's local API at `127.0.0.1:7890/metrics`. It also scrapes its own counters from `127.0.0.1:8888`, so failed sends to HEC are visible too.

### Reading the error

- `i/o timeout`, `context deadline exceeded`: no answer on the gNMI port. Check the IP address, routing, ACL, or firewall between the collector and the device.
- `connection refused`: the device answers, but nothing listens on that port. gNMI is not enabled, or the port is wrong (see Devices).
- `first record does not look like a TLS handshake`, `x509`, `certificate`: TLS settings differ between gnmic and the device. Check insecure and skip-verify on the gNMI page, and the device certificate.
- `Unauthenticated`, `PermissionDenied`: login rejected or the user lacks gNMI rights. Check username, password, and AAA authorization on the device.
- `Unimplemented`, `NotFound`, `InvalidArgument`: the device refuses this path, mode, or encoding. Only that subscription fails. Remove the path or use one the device supports. Nokia SR Linux accepts only its native YANG paths, which is why the template has `srl-` subscriptions.

### Debug from the collector host

1. Port reachable: `timeout 3 bash -c 'echo >/dev/tcp/DEVICE/PORT' && echo open`
2. gNMI, TLS, and login in one call: `gnmic -a DEVICE:PORT -u USER --skip-verify capabilities`. Use `--insecure` for a device without TLS. gnmic asks for the password when `-p` is left out.
3. One read: `gnmic -a DEVICE:PORT -u USER --skip-verify get --path /system/state`
4. Add `--debug` to print each RPC and the gRPC channel state changes.
5. Running state of this gnmic: `curl -s http://127.0.0.1:7890/api/v1/targets`. Passwords appear as `****`.
6. Raw counters: `curl -s http://127.0.0.1:7890/metrics | grep gnmic_`

Docs: [global flags](https://gnmic.openconfig.net/global_flags/), [capabilities](https://gnmic.openconfig.net/cmd/capabilities/), [get](https://gnmic.openconfig.net/cmd/get/), [targets](https://gnmic.openconfig.net/user_guide/targets/targets/), [subscriptions](https://gnmic.openconfig.net/user_guide/subscriptions/), [API server and metrics](https://gnmic.openconfig.net/user_guide/api/api_intro/).
"""


def gnmi_operation():
    ds = {}
    ds["ds_causes"] = search("""
`gnmi_ops_index` `gnmi_runtime_sourcetype` level=ERROR failure_cause=*
| stats count BY failure_cause
| eval label=failure_cause." (".count.")", value=failure_cause
| append [| makeresults | eval label="All", value="*", count=-1]
| sort count
| table label value""", refresh="1m")
    ds["ds_kpi_connected"] = search("""
| mstats latest(_value) AS up WHERE `gnmi_metrics_index` metric_name="gnmic_target_up" BY name
| stats sum(up) AS connected count AS total
| eval value=coalesce(connected, 0)." of ".coalesce(total, 0)
| fields value""", earliest="-5m", refresh="15s")
    ds["ds_kpi_failing"] = search("""
| mstats latest(_value) AS state WHERE `gnmi_metrics_index` metric_name="gnmic_target_connection_state" BY name
| where state!=3
| stats count""", earliest="-5m", refresh="15s")
    ds["ds_kpi_errors"] = search("""
`gnmi_ops_index` `gnmi_runtime_sourcetype` level=ERROR | stats count""", refresh="15s")
    ds["ds_kpi_updates"] = search("""
| mstats max(_value) AS hi min(_value) AS lo WHERE `gnmi_metrics_index` metric_name="gnmic_subscribe_number_of_received_subscribe_response_messages*" BY extracted_source subscription
| eval received=hi-lo
| append [| makeresults | eval received=0]
| stats sum(received) AS received
| eval received=round(received, 0)""", refresh="15s")
    ds["ds_kpi_hec"] = search("""
| mstats max(_value) AS hi min(_value) AS lo WHERE `gnmi_metrics_index` metric_name="otelcol_exporter_send_failed*" BY metric_name exporter
| eval failed=hi-lo
| append [| makeresults | eval failed=0]
| stats sum(failed) AS failed
| eval failed=round(failed, 0)""", refresh="15s")
    ds["ds_target_status"] = search("""
| mstats latest(_value) AS value WHERE `gnmi_metrics_index` (metric_name="gnmic_target_up" OR metric_name="gnmic_target_connection_state") BY name metric_name
| eval metric=if(metric_name="gnmic_target_up", "up", "state")
| xyseries name metric value
| join type=left name
    [ search `gnmi_ops_index` `gnmi_runtime_sourcetype` level=ERROR grpc_code=* earliest=-24h
    | stats latest(_time) AS last_error latest(failure_cause) AS last_cause latest(grpc_code) AS grpc_code BY target
    | rename target AS name ]
| eval status=if(up=1, "connected", "not connected"),
       channel=case(state=0, "UNKNOWN", state=1, "IDLE", state=2, "CONNECTING", state=3, "READY", state=4, "TRANSIENT_FAILURE (gnmic retries)", state=5, "SHUTDOWN"),
       last_error=strftime(last_error, "%Y-%m-%d %H:%M:%S")
| rename name AS target
| table target status channel last_error grpc_code last_cause""", earliest="-5m", refresh="15s")
    ds["ds_channel"] = search("""
| mstats max(_value) AS state WHERE `gnmi_metrics_index` metric_name="gnmic_target_connection_state" span=1m BY name
| xyseries _time name state""")
    ds["ds_causes_time"] = search("""
`gnmi_ops_index` `gnmi_runtime_sourcetype` level=ERROR failure_cause=*
| timechart span=1m count BY failure_cause""")
    ds["ds_subscriptions"] = search("""
| mstats latest(_value) AS value WHERE `gnmi_metrics_index` metric_name="gnmic_subscribe_number_of_*" BY metric_name extracted_source subscription
| eval counter=case(like(metric_name, "%failed_subscribe_request%"), "failed_starts", like(metric_name, "%received_subscribe_response%"), "updates_received", true(), metric_name),
       key=extracted_source."|".subscription
| xyseries key counter value
| eval target=mvindex(split(key, "|"), 0), subscription=mvindex(split(key, "|"), 1),
       updates_received=round(coalesce(updates_received, 0), 0), failed_starts=round(coalesce(failed_starts, 0), 0),
       health=case(updates_received>0, "streaming", failed_starts>0, "not started: see errors", true(), "waiting")
| table target subscription health updates_received failed_starts
| sort target subscription""")
    ds["ds_exports"] = search("""
| mstats max(_value) AS hi min(_value) AS lo WHERE `gnmi_metrics_index` metric_name="otelcol_exporter_*" BY metric_name exporter
| eval count=hi-lo,
       kind=case(like(metric_name, "%send_failed%"), "failed", like(metric_name, "%sent%"), "sent", true(), null())
| where isnotnull(kind)
| eval signal=case(like(metric_name, "%metric_points%"), "metric points", like(metric_name, "%log_records%"), "log records", true(), "other"),
       row=exporter." (".signal.")"
| xyseries row kind count
| eval sent=round(coalesce(sent, 0), 0), failed=round(coalesce(failed, 0), 0)
| rename row AS exporter
| table exporter sent failed""")
    ds["ds_errors"] = search("""
`gnmi_ops_index` `gnmi_runtime_sourcetype` (level=ERROR OR level=WARN)
| where "$cause$"="*" OR failure_cause=="$cause$"
| table _time level target subscription grpc_code failure_cause err
| head 200""", refresh="15s")
    ds["ds_log"] = search("""
`gnmi_ops_index` `gnmi_runtime_sourcetype` NOT "GET /metrics"
| eval message=coalesce(msg, _raw)
| table _time level message target subscription
| head 300""", refresh="15s")

    viz = {}
    viz["viz_intro"] = markdown("""
The page starts at the last 15 minutes so a corrected configuration is not hidden by old errors. Click a **subscription** or a **target** to open its data on **gNMI Data**. Click a failure cause to filter the error table. The **Troubleshooting guide** tab explains each error.
""")
    viz["viz_kpi_connected"] = single("Targets connected", "ds_kpi_connected", under="last 5 minutes",
                                      handlers=[to_dashboard("gnmi_data", ("tr.earliest", "$tr.earliest$"), ("tr.latest", "$tr.latest$"))])
    viz["viz_kpi_failing"] = single("Targets in failure", "ds_kpi_failing", under="channel not READY", ranges=[
        {"to": 1, "value": GREEN}, {"from": 1, "value": RED}])
    viz["viz_kpi_errors"] = single("gnmic errors", "ds_kpi_errors", ranges=[
        {"to": 1, "value": GREEN}, {"from": 1, "value": YELLOW}], handlers=[to_search(
            '`gnmi_ops_index` `gnmi_runtime_sourcetype` level=ERROR | table _time target subscription grpc_code failure_cause err')])
    viz["viz_kpi_updates"] = single("gNMI updates received", "ds_kpi_updates",
                                    handlers=[to_dashboard("gnmi_data", ("tr.earliest", "$tr.earliest$"), ("tr.latest", "$tr.latest$"))])
    viz["viz_kpi_hec"] = single("Collector send failures to HEC", "ds_kpi_hec", ranges=[
        {"to": 1, "value": GREEN}, {"from": 1, "value": RED}])
    viz["viz_target_status"] = table("Target status (click a target to open its data)", "ds_target_status",
                                     colors={"status": [("connected", GREEN), ("not connected", RED)]},
                                     handlers=[to_dashboard("gnmi_data", ("target", "$row.target.value$"),
                                                            ("tr.earliest", "$tr.earliest$"), ("tr.latest", "$tr.latest$"))])
    viz["viz_channel"] = chart("splunk.line", "gRPC channel state per target (3 = READY, 4 = TRANSIENT_FAILURE)", "ds_channel", {
        "yAxisMin": 0, "yAxisMax": 5, "legendDisplay": "bottom", "xAxisTitleVisibility": "hide", "yAxisTitleText": "state",
    })
    viz["viz_causes_time"] = chart("splunk.column", "Failures by cause (click a cause to filter the errors below)", "ds_causes_time", {
        "stackMode": "stacked", "legendDisplay": "bottom", "xAxisTitleVisibility": "hide", "yAxisTitleText": "errors",
    }, handlers=[set_tokens(("cause", "name"))])
    viz["viz_subscriptions"] = table("Subscriptions per target (click to open the data)", "ds_subscriptions",
                                     colors={"health": [("streaming", GREEN), ("not started: see errors", RED), ("waiting", YELLOW)]},
                                     handlers=[to_dashboard("gnmi_data", ("target", "$row.target.value$"),
                                                            ("subscription", "$row.subscription.value$"),
                                                            ("tr.earliest", "$tr.earliest$"), ("tr.latest", "$tr.latest$"))],
                                     count=12)
    viz["viz_exports"] = table("Collector exports to Splunk HEC", "ds_exports", count=12)
    viz["viz_errors"] = table("gnmic errors and warnings (cause: $cause$, click to open in Search)", "ds_errors", handlers=[to_search(
        '`gnmi_ops_index` `gnmi_runtime_sourcetype` (level=ERROR OR level=WARN) target="$row.target.value$"')], count=12)
    viz["viz_log"] = table("gnmic log (without the collector's /metrics scrapes)", "ds_log", count=15, handlers=[to_search(
        '`gnmi_ops_index` `gnmi_runtime_sourcetype`')])
    viz["viz_guide"] = markdown(OPS_GUIDE)

    inputs = {
        "input_tr": timerange("-15m,now"),
        "input_cause": dropdown("Failure cause", "cause", "ds_causes", statics=False),
    }
    status = grid([
        (70, [("viz_intro", 1440)]),
        (130, [("viz_kpi_connected", 288), ("viz_kpi_failing", 288), ("viz_kpi_errors", 288),
               ("viz_kpi_updates", 288), ("viz_kpi_hec", 288)]),
        (220, [("viz_target_status", 1440)]),
        (300, [("viz_channel", 720), ("viz_causes_time", 720)]),
        (420, [("viz_subscriptions", 840), ("viz_exports", 600)]),
        (420, [("viz_errors", 1440)]),
        (480, [("viz_log", 1440)]),
    ])
    guide = grid([(780, [("viz_guide", 1440)])])
    layout = {
        "globalInputs": ["input_tr", "input_cause"],
        "tabs": {"items": [
            {"layoutId": "layout_status", "label": "Status"},
            {"layoutId": "layout_guide", "label": "Troubleshooting guide"},
        ]},
        "layoutDefinitions": {"layout_status": status, "layout_guide": guide},
    }
    definition = {
        "title": "gNMI Operation",
        "description": "Is gnmic connected, which subscriptions stream, and why a target fails.",
        "inputs": inputs,
        "defaults": {
            "dataSources": {"ds.search": {"options": {"queryParameters": {"earliest": "$tr.earliest$", "latest": "$tr.latest$"}}}},
        },
        "visualizations": viz,
        "dataSources": ds,
        "layout": layout,
    }
    write("gnmi_operation", "gNMI Operation", "Is gnmic connected, which subscriptions stream, and why a target fails.", definition)


if __name__ == "__main__":
    gnmi_data()
    gnmi_operation()
