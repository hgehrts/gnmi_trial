require([
    "jquery",
    "splunkjs/mvc"
], function($, mvc) {
    var STORE_KEY = "gnmi_trial_setup";
    var SAMPLE_TOKEN = "00000000-0000-0000-0000-000000000000";
    var FIELDS = [
        {id: "SPLUNK_HEC_URL", step: "splunk", label: "HEC URL", value: "https://127.0.0.1:8088/services/collector", help: "Splunk HTTP Event Collector endpoint, including /services/collector. The collector host must be able to reach this address."},
        {id: "SPLUNK_HEC_TOKEN", step: "splunk", label: "HEC token", type: "password", value: SAMPLE_TOKEN, help: "Sample token of zeros. Leave it when Enable HEC is checked. Splunk then creates a real token, writes that secret into install.sh, and this field shows the real token. Type your own token only when you turn the checkbox off."},
        {id: "SPLUNK_HEC_EVENTS_INDEX", step: "splunk", label: "Events index", value: "gnmi", help: "Event index for gNMI JSON updates. Create it as a normal event index."},
        {id: "SPLUNK_HEC_METRICS_INDEX", step: "splunk", label: "Metrics index", value: "gnmi_metrics", help: "Index for OpenTelemetry metrics. It must be a metrics index (datatype=metric)."},
        {id: "SPLUNK_HEC_OPS_INDEX", step: "splunk", label: "Operations index", value: "gnmi_ops", help: "Event index for installer lines and later gnmic process logs. The searches on the Build page read it."},
        {id: "SPLUNK_HEC_EVENTS_SOURCETYPE", step: "splunk", label: "Events sourcetype", value: "gnmi:event", help: "Sourcetype stamped on gNMI JSON events."},
        {id: "SPLUNK_HEC_METRICS_SOURCETYPE", step: "splunk", label: "Metrics sourcetype", value: "gnmi:metric", help: "Sourcetype stamped on metric points. Metric searches use the metrics index more than this name."},
        {id: "SPLUNK_HEC_SETUP_SOURCETYPE", step: "splunk", label: "Setup sourcetype", value: "gnmi:setup", help: "Sourcetype for each line install.sh prints while it installs gnmic and the collector."},
        {id: "SPLUNK_HEC_RUNTIME_SOURCETYPE", step: "splunk", label: "Runtime sourcetype", value: "gnmi:runtime", help: "Sourcetype for gnmic's own log after the collector is running."},
        {id: "SPLUNK_HEC_TLS_SKIP_VERIFY", step: "splunk", label: "Skip HEC certificate check", type: "bool", value: "true", help: "true accepts a self-signed HEC certificate. For a lab that is the usual choice. Set false and fill the CA path when you want the certificate checked."},
        {id: "SPLUNK_HEC_CA_FILE", step: "splunk", label: "HEC CA file", value: "", help: "Path on the collector host to a PEM CA bundle. Leave empty when certificate checks are skipped. This app does not upload the certificate."},
        {id: "OTLP_GRPC_LISTEN", step: "otel", label: "OTLP gRPC listen", value: "127.0.0.1:4317", help: "Address the OpenTelemetry Collector binds for gnmic's metric export. Keep it on localhost when gnmic runs on the same host."},
        {id: "OTLP_HTTP_LISTEN", step: "otel", label: "OTLP HTTP listen", value: "127.0.0.1:4318", help: "HTTP OTLP port on the collector. gnmic uses the gRPC port. This one is there if you want to test with another OTLP client."},
        {id: "OTLP_ENDPOINT", step: "otel", label: "gnmic OTLP endpoint", value: "127.0.0.1:4317", help: "Where gnmic sends OpenTelemetry metrics. Use the same host and port as the gRPC listen when both programs are on one machine."},
        {id: "SPLUNK_OTEL_VERSION", step: "otel", label: "Collector version", value: "latest", help: "latest, or a number such as 0.163.1. Linux uses the Splunk collector installer. macOS downloads the matching release binary."},
        {id: "SPLUNK_OTEL_INSTALLER_URL", step: "otel", label: "Collector installer URL", value: "https://dl.observability.splunkcloud.com/splunk-otel-collector.sh", help: "Linux installer script. Leave the default unless your lab mirrors that file."},
        {id: "GNMI_USERNAME", step: "gnmi", label: "gNMI username", value: "", help: "Account used for every device in the targets list that does not set its own username."},
        {id: "GNMI_PASSWORD", step: "gnmi", label: "gNMI password", type: "password", value: "", help: "Password for that account. It is written into gnmic.yaml on the collector host. Use a lab account."},
        {id: "GNMI_SAMPLE_INTERVAL", step: "gnmi", label: "Sample interval", value: "60s", help: "How often gnmic asks for a sample. 60s is enough to see data. Shorter intervals send more events."},
        {id: "GNMI_INSECURE", step: "gnmi", label: "Plaintext gNMI", type: "bool", value: "false", help: "false uses TLS, which is what most devices expect. true turns TLS off for a device that only offers plaintext gNMI."},
        {id: "GNMI_SKIP_VERIFY", step: "gnmi", label: "Skip device certificate check", type: "bool", value: "true", help: "true accepts the device's own certificate without a CA. Typical for a lab. Set false and fill the CA path to check the device certificate."},
        {id: "GNMI_TLS_CA", step: "gnmi", label: "Device CA file", value: "", help: "Path on the collector host to the CA that signed the device certificates. Leave empty when device certificate checks are skipped."},
        {id: "GNMIC_VERSION", step: "gnmi", label: "gnmic version", value: "latest", help: "latest, or a number such as 0.49.1. The script downloads that release from the gnmic GitHub project."}
    ];
    var STEPS = {
        splunk: {title: "Splunk", prev: null, next: "otel", nextLabel: "OpenTelemetry"},
        otel: {title: "OpenTelemetry", prev: "splunk", prevLabel: "Splunk", next: "gnmi", nextLabel: "gNMI"},
        gnmi: {title: "gNMI", prev: "otel", prevLabel: "OpenTelemetry", next: "devices", nextLabel: "Devices"},
        devices: {title: "Devices", prev: "gnmi", prevLabel: "gNMI", next: "build", nextLabel: "Build"},
        build: {title: "Build", prev: "devices", prevLabel: "Devices", next: null}
    };

    function escapeAttr(value) {
        return String(value).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
    }

    function loadStore() {
        try {
            var parsed = JSON.parse(sessionStorage.getItem(STORE_KEY) || "{}");
            return parsed && typeof parsed === "object" ? parsed : {};
        } catch (ignore) {
            return {};
        }
    }

    function defaults() {
        var state = {
            create_indexes: true,
            create_hec: true,
            hec_token_name: "gnmi_trial",
            targets_yaml: ""
        };
        FIELDS.forEach(function(field) {
            state[field.id] = field.value;
        });
        return state;
    }

    function mergedState() {
        var state = defaults();
        var saved = loadStore();
        Object.keys(saved).forEach(function(key) {
            if (saved[key] !== undefined && saved[key] !== null) {
                state[key] = saved[key];
            }
        });
        return state;
    }

    function writeStore(state) {
        sessionStorage.setItem(STORE_KEY, JSON.stringify(state));
    }

    function saveFromDom() {
        var state = mergedState();
        FIELDS.forEach(function(field) {
            var el = document.getElementById(field.id);
            if (el) {
                state[field.id] = el.value;
            }
        });
        var tokenName = document.getElementById("hec_token_name");
        if (tokenName) {
            state.hec_token_name = tokenName.value;
        }
        var createHec = document.getElementById("create_hec");
        if (createHec) {
            state.create_hec = createHec.checked;
        }
        var createIndexes = document.getElementById("create_indexes");
        if (createIndexes) {
            state.create_indexes = createIndexes.checked;
        }
        var targets = document.getElementById("targets_yaml");
        if (targets) {
            state.targets_yaml = targets.value;
        }
        writeStore(state);
        return state;
    }

    function viewUrl(name) {
        var path = "/app/gnmi_trial/" + name;
        var url = (window.Splunk && Splunk.util && Splunk.util.make_url) ? Splunk.util.make_url(path) : path;
        if (pageIsDark()) {
            url += (url.indexOf("?") === -1 ? "?" : "&") + "theme=dark";
        }
        return url;
    }

    function fieldHtml(field, value) {
        var input;
        if (field.type === "bool") {
            input = '<select id="' + field.id + '">' +
                '<option value="true"' + (value === "true" ? " selected" : "") + '>true</option>' +
                '<option value="false"' + (value === "false" ? " selected" : "") + '>false</option>' +
                '</select>';
        } else {
            var type = field.type || "text";
            input = '<input id="' + field.id + '" type="' + type + '" value="' + escapeAttr(value) + '">';
        }
        return '<div class="gnmi-field"><label for="' + field.id + '">' + field.label + '</label>' +
            input + '<p class="gnmi-help">' + field.help + '</p></div>';
    }

    function stepperHtml(step) {
        var order = ["splunk", "otel", "gnmi", "devices", "build"];
        var current = order.indexOf(step);
        return '<ol class="gnmi-stepper">' + order.map(function(name, index) {
            var state = index < current ? "done" : (index === current ? "current" : "todo");
            return '<li class="gnmi-step-' + state + '"><a class="gnmi-step-link" data-view="' + name + '" href="' + viewUrl(name) + '">' +
                '<span class="gnmi-step-num">' + (index + 1) + '</span>' + STEPS[name].title + '</a></li>';
        }).join("") + '</ol>';
    }

    function pagerHtml(step) {
        var meta = STEPS[step];
        var html = '<div class="gnmi-pager">';
        if (meta.prev) {
            html += '<a class="gnmi-step-link" data-view="' + meta.prev + '" href="' + viewUrl(meta.prev) + '">Back: ' + meta.prevLabel + '</a>';
        }
        if (meta.next) {
            html += '<a class="gnmi-step-link" data-view="' + meta.next + '" href="' + viewUrl(meta.next) + '">Next: ' + meta.nextLabel + '</a>';
        }
        html += '</div>';
        return html;
    }

    function buildForm(step) {
        var state = mergedState();
        var meta = STEPS[step];
        var parts = ['<form id="gnmi-form" class="gnmi-form">', '<fieldset><legend>' + meta.title + '</legend>'];
        FIELDS.forEach(function(field) {
            if (field.step === step) {
                parts.push(fieldHtml(field, state[field.id]));
            }
        });
        if (step === "splunk") {
            parts.push(
                '<div class="gnmi-field"><label for="hec_token_name">HEC token name</label>' +
                '<input id="hec_token_name" type="text" value="' + escapeAttr(state.hec_token_name) + '">' +
                '<p class="gnmi-help">Name of the token created on this Splunk instance. A later submit with the same name reuses it and updates the indexes it may write to. The name http is reserved by Splunk.</p></div>' +
                '<div class="gnmi-field"><label><input id="create_hec" type="checkbox"' + (state.create_hec ? " checked" : "") + '> Enable HEC and create this token</label>' +
                '<p class="gnmi-help">Uses the Splunk REST API on this server. It turns HTTP Event Collector on with TLS, creates the token if the name is new, and allows that token to write to the three indexes. The zeros in the token field are only a sample. You need a role that can edit HEC tokens, and the indexes have to exist first, so leave the index box checked on a single-server lab. The collector host still has to reach the HEC URL.</p></div>' +
                '<div class="gnmi-field"><label><input id="create_indexes" type="checkbox"' + (state.create_indexes ? " checked" : "") + '> Create these indexes on this Splunk instance</label>' +
                '<p class="gnmi-help">Creates the three indexes on this standalone lab server. The metrics name is created with datatype=metric. You need permission to edit indexes. The downloaded indexes.conf is only a copy of those definitions. Existing indexes are left as they are.</p></div>'
            );
        }
        if (step === "devices") {
            parts.push(
                '<div class="gnmi-field"><label for="targets_yaml">targets.yaml</label>' +
                '<textarea id="targets_yaml">' + escapeAttr(state.targets_yaml) + '</textarea>' +
                '<p class="gnmi-help">Uncomment one block per device. The port in each comment is the usual gNMI port for that network OS. Nokia SR Linux uses the srl- subscription names. The other devices use the OpenConfig names. outputs otlp and events send metrics and JSON events.</p></div>'
            );
        }
        if (step === "build") {
            parts.push(
                '<div class="gnmi-actions">' +
                '<button type="submit" id="gnmi-submit">Download configuration</button>' +
                '<button type="button" id="gnmi-install">Install on this server</button>' +
                '</div>' +
                '<p class="gnmi-help">Download writes gnmi-trial-bundle.zip for a host that can reach the devices. Install on this server runs that same script here as the Splunk user, without root or systemd. Use the HEC URL https://127.0.0.1:8088/services/collector so the collector reaches this Splunk. In the lab container, the user is splunk.</p>' +
                '<p id="gnmi-container-status" class="gnmi-help"></p>' +
                editorHtml()
            );
        }
        parts.push('</fieldset>', pagerHtml(step), '</form>');
        $("#gnmi-form-host").html(parts.join(""));
        $("#gnmi-form").on("input change", function() {
            saveFromDom();
        });
        $("#gnmi-form").on("click", ".gnmi-step-link", function() {
            saveFromDom();
        });
        $("#gnmi-form").on("submit", onSubmit);
        $("#gnmi-install").on("click", function() {
            installRequested = true;
            $("#gnmi-form").trigger("submit");
        });
        $("#gnmi-save-files").on("click", saveCollectorFiles);
        $("#gnmi-restart-gnmic").on("click", function() {
            restartCollector("gnmic");
        });
        $("#gnmi-restart-otel").on("click", function() {
            restartCollector("otel");
        });
        if (step === "build") {
            loadEditor();
        }
    }

    function editorHtml() {
        function field(name) {
            return '<div class="gnmi-field"><label for="gnmi-file-' + name + '">' + name + '</label>' +
                '<textarea id="gnmi-file-' + name + '" data-file="' + name + '" spellcheck="false"></textarea></div>';
        }
        return '<div id="gnmi-editor" class="gnmi-editor" hidden>' +
            '<h3>Collector files on this server</h3>' +
            '<p class="gnmi-help">These are the files written by Install on this server. gnmic reads gnmic.yaml, including the device list at the bottom of that file. targets.yaml is the device list kept beside it. The OpenTelemetry Collector reads otel-agent.yaml. Save writes all three. Restart stops that process and starts it again so it reads the saved file. gnmic does not reload its configuration in place.</p>' +
            field("gnmic.yaml") +
            field("otel-agent.yaml") +
            field("targets.yaml") +
            '<div class="gnmi-actions">' +
            '<button type="button" id="gnmi-save-files">Save</button>' +
            '<button type="button" id="gnmi-restart-gnmic">Restart gnmic</button>' +
            '<button type="button" id="gnmi-restart-otel">Restart OpenTelemetry</button>' +
            '</div>' +
            '<p id="gnmi-editor-status" class="gnmi-help"></p>' +
            '</div>';
    }

    function parseServiceData(response) {
        var data = response && (response.data || response);
        if (typeof data === "string") {
            data = JSON.parse(data);
        }
        return data;
    }

    function postCollector(body, done) {
        var service = mvc.createService();
        service.request(
            "gnmi_setup",
            "POST",
            null,
            null,
            JSON.stringify(body),
            {"Content-Type": "application/json"},
            function(err, response) {
                if (err) {
                    done(err);
                    return;
                }
                var data;
                try {
                    data = parseServiceData(response);
                } catch (ignore) {
                    done({data: "The collector response was not JSON."});
                    return;
                }
                if (data && data.error) {
                    done({data: data});
                    return;
                }
                done(null, data);
            }
        );
    }

    function setEditorStatus(text, isError) {
        var node = $("#gnmi-editor-status");
        node.toggleClass("gnmi-error", !!isError);
        node.text(text || "");
    }

    function showEditor(data) {
        var host = $("#gnmi-editor");
        if (!host.length || !data || !data.installed) {
            return;
        }
        host.removeAttr("hidden");
        ["gnmic.yaml", "otel-agent.yaml", "targets.yaml"].forEach(function(name) {
            var box = host.find("textarea[data-file='" + name + "']");
            if (!box.length || document.activeElement === box[0]) {
                return;
            }
            if (data.files && Object.prototype.hasOwnProperty.call(data.files, name)) {
                box.val(data.files[name]);
            }
        });
    }

    function loadEditor() {
        if ($("#gnmi-page").attr("data-step") !== "build" || !$("#gnmi-editor").length) {
            return;
        }
        postCollector({action: "read_collector"}, function(err, data) {
            if (err) {
                return;
            }
            showEditor(data);
        });
    }

    function collectorFilesFromDom() {
        var files = {};
        $("#gnmi-editor textarea").each(function() {
            files[$(this).attr("data-file")] = $(this).val();
        });
        return files;
    }

    function saveCollectorFiles() {
        setEditorStatus("Saving the three files…");
        $("#gnmi-save-files, #gnmi-restart-gnmic, #gnmi-restart-otel").prop("disabled", true);
        postCollector({action: "save_collector", files: collectorFilesFromDom()}, function(err, data) {
            $("#gnmi-save-files, #gnmi-restart-gnmic, #gnmi-restart-otel").prop("disabled", false);
            if (err) {
                setEditorStatus(errorText(err), true);
                return;
            }
            setEditorStatus((data && data.detail) || "Saved.");
        });
    }

    function restartCollector(service) {
        var label = service === "otel" ? "OpenTelemetry Collector" : "gnmic";
        setEditorStatus("Restarting " + label + "…");
        $("#gnmi-save-files, #gnmi-restart-gnmic, #gnmi-restart-otel").prop("disabled", true);
        postCollector({action: "restart_collector", service: service}, function(err, data) {
            $("#gnmi-save-files, #gnmi-restart-gnmic, #gnmi-restart-otel").prop("disabled", false);
            if (err) {
                setEditorStatus(errorText(err), true);
                return;
            }
            setEditorStatus((data && data.detail) || ("Restarted " + label + "."));
            noteContainer(data);
        });
    }

    function readBody() {
        var state = saveFromDom();
        var body = {
            create_indexes: !!state.create_indexes,
            create_hec: !!state.create_hec,
            hec_token_name: state.hec_token_name,
            targets_yaml: state.targets_yaml || "targets:\n"
        };
        FIELDS.forEach(function(field) {
            body[field.id] = state[field.id];
        });
        return body;
    }

    function tokenModel() {
        return mvc.Components.getInstance("default") || mvc.Components.get("default");
    }

    function setSearchTokens(body) {
        var tokens = tokenModel();
        if (!tokens) {
            return;
        }
        tokens.set("ops_index", body.SPLUNK_HEC_OPS_INDEX);
        tokens.set("events_index", body.SPLUNK_HEC_EVENTS_INDEX);
        tokens.set("metrics_index", body.SPLUNK_HEC_METRICS_INDEX);
        tokens.set("setup_sourcetype", body.SPLUNK_HEC_SETUP_SOURCETYPE);
        tokens.set("runtime_sourcetype", body.SPLUNK_HEC_RUNTIME_SOURCETYPE);
    }

    function downloadZip(b64) {
        var binary = atob(b64);
        var bytes = new Uint8Array(binary.length);
        for (var i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        var blob = new Blob([bytes], {type: "application/zip"});
        var link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = "gnmi-trial-bundle.zip";
        link.click();
    }

    function downloadText(name, text) {
        var blob = new Blob([text], {type: "text/plain"});
        var link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = name;
        link.click();
    }

    function showResult(data) {
        var lines = (data.index_results || []).map(function(item) {
            return (item.name || "indexes") + ": " + item.status + (item.detail ? " (" + item.detail + ")" : "");
        });
        var indexNote = lines.length ? "<p>" + lines.join("<br>") + "</p>" : "<p>Indexes were not created from this form. Use indexes.conf in the zip if this Splunk is not the indexer.</p>";
        var hecNote = "";
        if (data.hec && data.hec.name) {
            var state = mergedState();
            if (data.hec.token) {
                state.SPLUNK_HEC_TOKEN = data.hec.token;
                var tokenInput = document.getElementById("SPLUNK_HEC_TOKEN");
                if (tokenInput) {
                    tokenInput.value = data.hec.token;
                }
            }
            if (data.hec.port && data.hec.port !== "8088" && state.SPLUNK_HEC_URL.indexOf(":8088/") !== -1) {
                state.SPLUNK_HEC_URL = state.SPLUNK_HEC_URL.replace(":8088/", ":" + data.hec.port + "/");
            }
            writeStore(state);
            hecNote = "<p>HTTP Event Collector is on, TLS stays enabled, and token <code>" +
                escapeAttr(data.hec.name) + "</code> was " + escapeAttr(data.hec.status) +
                " for port " + escapeAttr(data.hec.port || "8088") +
                ". The secret replaced the sample zeros. It is on the Splunk page and in install.sh.</p>";
            if (data.hec.listen && data.hec.listen.detail) {
                hecNote += "<p>" + escapeAttr(data.hec.listen.detail) + "</p>";
            }
        }
        var fileButtons = ["gnmic.yaml", "otel-agent.yaml", "targets.yaml", "indexes.conf", "INSTRUCTIONS.txt"].map(function(name) {
            return '<button type="button" class="gnmi-file" data-file="' + name + '">' + name + "</button>";
        }).join(" ");
        $("#gnmi-result").html(
            "<h3>Run the installer</h3>" +
            hecNote +
            indexNote +
            "<ol>" +
            "<li>Unpack <code>gnmi-trial-bundle.zip</code> on the host that will connect to the devices.</li>" +
            "<li>From that directory run <code>sudo ./install.sh</code>.</li>" +
            "<li>Each installer line is sent to index <code>" + escapeAttr(data.indexes.ops) + "</code>. The first table on this page refreshes every few seconds.</li>" +
            "<li>After gnmic is running, events land in <code>" + escapeAttr(data.indexes.events) + "</code> and metrics in <code>" + escapeAttr(data.indexes.metrics) + "</code>.</li>" +
            "<li>If the install fails, change an earlier step and press Build configuration again. Unpack the new zip and run <code>sudo ./install.sh</code> again.</li>" +
            "</ol>" +
            "<p><button type=\"button\" id=\"gnmi-download\">Download gnmi-trial-bundle.zip</button></p>" +
            "<p>" + fileButtons + "</p>" +
            "<p class=\"gnmi-help\">The zip also contains install.sh and hec_log.py. Preview YAML uses Linux paths. install.sh rewrites log paths for the machine you run it on. gnmic.yaml in the download contains the device password.</p>"
        );
        $("#gnmi-download").on("click", function() {
            downloadZip(data.zip_base64);
        });
        $("#gnmi-result").on("click", ".gnmi-file", function() {
            var name = $(this).attr("data-file");
            downloadText(name, data.files[name]);
        });
        if (data.container) {
            noteContainer(data.container);
        } else {
            downloadZip(data.zip_base64);
        }
    }

    function errorText(err) {
        var data = err && err.data;
        if (typeof data === "string") {
            try {
                data = JSON.parse(data);
            } catch (ignore) {
                return data;
            }
        }
        if (data && data.error) {
            var extra = (data.index_results || []).map(function(item) {
                return (item.name || "index") + ": " + item.status + (item.detail ? " (" + item.detail + ")" : "");
            });
            return extra.length ? data.error + " " + extra.join("; ") : data.error;
        }
        var message = (data && data.messages) || (err && err.error) || (err && err.status) || "Request failed";
        return typeof message === "string" ? message : JSON.stringify(message);
    }

    var installRequested = false;

    function containerText(box) {
        if (!box) {
            return "";
        }
        if (box.detail) {
            return box.detail;
        }
        if (box.state === "running" || box.state === "started") {
            return "Installing on this server. The live installer table below shows each line as it runs. The collector download is about 430 MB and can take several minutes.";
        }
        if (box.state === "ok") {
            var otel = box.otel_running ? "The OpenTelemetry Collector is running." : "The OpenTelemetry Collector is not running. Restarting Splunk stops it; press Install on this server again.";
            var gnmic = box.gnmic_running ? " gnmic is running and subscribed to the devices." : " gnmic is not running. It starts when targets.yaml has an uncommented device address.";
            return "Installed on this server. " + otel + gnmic;
        }
        if (box.state === "failed") {
            return "The install stopped with an error. The live installer table below shows the last lines.";
        }
        return "";
    }

    // Simple XML depends does not follow tokens set from this script on Splunk 10,
    // so the build rows are shown and hidden here.
    function panelRow(id) {
        return $("#" + id).closest(".dashboard-row");
    }

    function revealRow(id, tries) {
        var row = panelRow(id);
        if (row.length) {
            row.addClass("gnmi-show");
            return;
        }
        if ((tries || 0) < 40) {
            setTimeout(function() { revealRow(id, (tries || 0) + 1); }, 250);
        }
    }

    function showLabRows() {
        revealRow("gnmi_ops_panel");
        revealRow("gnmi_events_panel");
    }

    function showInstallLive(scroll) {
        revealRow("gnmi_install_live");
        if (scroll) {
            setTimeout(function() {
                var panel = document.getElementById("gnmi_install_live");
                if (panel) {
                    panel.scrollIntoView({behavior: "smooth", block: "start"});
                }
            }, 500);
        }
    }

    function noteContainer(box) {
        var node = $("#gnmi-container-status");
        if (!node.length) {
            return;
        }
        node.text(containerText(box));
        if (box && box.state && box.state !== "idle") {
            showInstallLive(box.state === "started");
        }
        if (box && (box.state === "running" || box.state === "started")) {
            setTimeout(pollContainer, 3000);
        }
        if (box && box.state === "ok") {
            loadEditor();
        }
    }

    function pollContainer() {
        var service = mvc.createService();
        service.request(
            "gnmi_setup",
            "GET",
            null,
            null,
            null,
            {"Content-Type": "application/json"},
            function(err, response) {
                if (err) {
                    return;
                }
                var data = response && (response.data || response);
                if (typeof data === "string") {
                    try {
                        data = JSON.parse(data);
                    } catch (ignore) {
                        return;
                    }
                }
                noteContainer(data && data.container);
            }
        );
    }

    function onSubmit(event) {
        event.preventDefault();
        var install = installRequested;
        installRequested = false;
        var body = readBody();
        body.install_container = install;
        $("#gnmi-submit, #gnmi-install").prop("disabled", true);
        $("#gnmi-result").html("<p>Building files…</p>");
        var service = mvc.createService();
        service.request(
            "gnmi_setup",
            "POST",
            null,
            null,
            JSON.stringify(body),
            {"Content-Type": "application/json"},
            function(err, response) {
                $("#gnmi-submit, #gnmi-install").prop("disabled", false);
                if (err) {
                    $("#gnmi-result").html("<p class=\"gnmi-error\">" + escapeAttr(errorText(err)) + "</p>");
                    return;
                }
                var data = response && (response.data || response);
                if (typeof data === "string") {
                    data = JSON.parse(data);
                }
                if (data && data.error) {
                    $("#gnmi-result").html("<p class=\"gnmi-error\">" + escapeAttr(data.error) + "</p>");
                    return;
                }
                setSearchTokens(body);
                showResult(data);
            }
        );
    }

    function showBlocked(message) {
        $("#gnmi-form-host").html("<p class=\"gnmi-blocked\">" + escapeAttr(message) + "</p>");
    }

    function pageIsDark() {
        var theme = [window.DASHBOARD_THEME, window.SPLUNK_THEME, window.__splunk_page_theme__].join(" ").toLowerCase();
        if (theme.indexOf("dark") !== -1) {
            return true;
        }
        return !!document.querySelector("[class*='layout-dark'], [class*='enterprise-dark'], [data-theme='dark'], [data-color-scheme='dark']");
    }

    function isDark() {
        return pageIsDark();
    }

    function userWantsDark(done) {
        var root = (window.Splunk && Splunk.util && Splunk.util.make_url)
            ? Splunk.util.make_url("/splunkd/__raw")
            : "/en-US/splunkd/__raw";
        fetch(root + "/services/authentication/current-context?output_mode=json", {credentials: "same-origin"})
            .then(function(response) { return response.json(); })
            .then(function(data) {
                var name = data.entry[0].content.username;
                return fetch(root + "/services/authentication/users/" + encodeURIComponent(name) + "?output_mode=json", {credentials: "same-origin"});
            })
            .then(function(response) { return response.json(); })
            .then(function(data) {
                done(String(data.entry[0].content.theme || "") === "dark");
            })
            .catch(function() { done(pageIsDark()); });
    }

    function followUserTheme(done) {
        userWantsDark(function(wantsDark) {
            var url = new URL(window.location.href);
            var queryDark = url.searchParams.get("theme") === "dark";
            if (wantsDark && !pageIsDark()) {
                url.searchParams.set("theme", "dark");
                window.location.replace(url.toString());
                return;
            }
            if (!wantsDark && queryDark) {
                url.searchParams.delete("theme");
                window.location.replace(url.toString());
                return;
            }
            done();
        });
    }

    function applyTheme() {
        $(document.body).toggleClass("gnmi-dark", isDark());
    }

    function watchTheme() {
        applyTheme();
        if (!window.MutationObserver) {
            return;
        }
        var observer = new MutationObserver(applyTheme);
        observer.observe(document.documentElement, {attributes: true, attributeFilter: ["class", "data-theme", "data-color-scheme"], subtree: true});
    }

    function targetsUrl() {
        if (window.Splunk && Splunk.util && Splunk.util.make_url) {
            return Splunk.util.make_url("/static/app/gnmi_trial/targets.example.yaml");
        }
        return "/static/app/gnmi_trial/targets.example.yaml";
    }

    function ensureTargets(done) {
        if (mergedState().targets_yaml) {
            done();
            return;
        }
        $.get(targetsUrl()).done(function(text) {
            var state = mergedState();
            if (!state.targets_yaml) {
                state.targets_yaml = text;
                writeStore(state);
            }
        }).always(done);
    }

    function checkLab() {
        var step = $("#gnmi-page").attr("data-step");
        if (!STEPS[step]) {
            return;
        }
        if (!$(".gnmi-stepper").length) {
            $(".gnmi-intro").first().before(stepperHtml(step));
        }
        var service = mvc.createService();
        service.request(
            "gnmi_setup",
            "GET",
            null,
            null,
            null,
            {"Content-Type": "application/json"},
            function(err, response) {
                var data = null;
                if (!err) {
                    data = response && (response.data || response);
                    if (typeof data === "string") {
                        try {
                            data = JSON.parse(data);
                        } catch (ignore) {
                            data = null;
                        }
                    }
                }
                if (!data || !data.allowed) {
                    showBlocked((data && data.message) || "Setup is off because this Splunk instance could not be confirmed as a standalone lab server.");
                    return;
                }
                showLabRows();
                ensureTargets(function() {
                    if (step === "build") {
                        setSearchTokens(mergedState());
                    }
                    buildForm(step);
                    if (step === "build") {
                        noteContainer(data.container);
                    }
                });
            }
        );
    }

    $(function() {
        watchTheme();
        $(document).on("click", ".gnmi-stepper .gnmi-step-link", function() {
            saveFromDom();
        });
        followUserTheme(checkLab);
    });
});
