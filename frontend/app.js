/* CARES UI: an observability layer over the backend's EngineOutput. */

(() => {
  "use strict";

  const state = {
    user: null,
    current: null,
    history: [],
    daily: [],
    adaptation: [],
    incidents: [],
    guardians: [],
    notificationStatus: null,
    location: null,
    actions: [],
    demo: { active: false, session: null },
    page: "dashboard",
    eventSource: null,
    realtimeConnected: false,
    telemetry: null,
    telemetryLocalAt: null,
  };
  // A telemetry reading older than this is shown as stale (device may be offline).
  const TELEMETRY_STALE_SECONDS = 30;
  const DEMO_SOURCES = ["DEMO_WESAD", "DEMO_SYNTHETIC"];
  const SESSION_ID_FIELD = "session_id";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function escapeHTML(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function number(value, digits = 1) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toFixed(digits) : "—";
  }

  function signedNumber(value, digits = 1) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return "—";
    return `${parsed >= 0 ? "+" : ""}${parsed.toFixed(digits)}`;
  }

  function percentage(value, digits = 1) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toFixed(digits) : "—";
  }

  function timestamp(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "number" || (typeof value === "string" && /^-?\d+(\.\d+)?$/.test(value))) {
      return `T+${number(value, 1)} s`;
    }
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  function initials(name) {
    return String(name || "—").split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "—";
  }

  function actionLabel(value) {
    return String(value || "UNKNOWN_ACTION");
  }

  // Display simulated SMS separately from real delivery statuses.
  function actionStatusLabel(value) {
    const status = String(value || "UNKNOWN").toUpperCase();
    const labels = {
      GENERATED: "Action created",
      PENDING: "Provider accepted · awaiting delivery",
      SENT: "Sent to provider · delivery unconfirmed",
      DELIVERED: "Delivered successfully",
      MOCK_SENT: "Demo only · no real SMS sent",
      FAILED: "Failed to send",
      UNAVAILABLE: "Notification service unavailable",
    };
    return labels[status] || status.replaceAll("_", " ");
  }

  function actionStatusSymbol(value) {
    const status = String(value || "").toUpperCase();
    if (status === "DELIVERED") return "✓";
    if (status === "MOCK_SENT") return "◌";
    if (status === "FAILED") return "!";
    return "·";
  }

  function riskDescriptor(level) {
    const descriptors = {
      LOW: { label: "LOW RISK", summary: "Within personal baseline", className: "risk-low" },
      MEDIUM: { label: "MEDIUM RISK", summary: "Abnormal physiological variation detected", className: "risk-medium" },
      HIGH: { label: "HIGH RISK", summary: "Persistent significant deviation detected", className: "risk-high" },
    };
    return descriptors[String(level)] || { label: "RISK UNAVAILABLE", summary: "Waiting for an engine decision.", className: "risk-low" };
  }

  async function request(path, options = {}) {
    const config = { credentials: "same-origin", ...options, headers: { ...(options.headers || {}) } };
    if (config.body && typeof config.body !== "string") {
      config.headers["Content-Type"] = "application/json";
      config.body = JSON.stringify(config.body);
    }
    const response = await fetch(path, config);
    let payload = {};
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) {
      const error = new Error(payload.error || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function setMessage(selector, message = "") {
    const element = $(selector);
    if (element) element.textContent = message;
  }

  function showAuth(tab = "login") {
    state.eventSource?.close();
    state.eventSource = null;
    setTelemetry(null);
    renderTelemetry();
    $("#auth-view").classList.remove("is-hidden");
    $("#app-view").classList.add("is-hidden");
    $$("[data-auth-tab]").forEach((button) => {
      const active = button.dataset.authTab === tab;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", String(active));
    });
    $("#login-form").classList.toggle("is-hidden", tab !== "login");
    $("#register-form").classList.toggle("is-hidden", tab !== "register");
    setMessage('[data-form-message="login"]');
    setMessage('[data-form-message="register"]');
  }

  function showApp(user) {
    state.user = user;
    $("#auth-view").classList.add("is-hidden");
    $("#app-view").classList.remove("is-hidden");
    $("#topbar-avatar").textContent = initials(user.name);
    $("#settings-name").textContent = user.name || "—";
    $("#settings-email").textContent = user.email || "—";
    $("#settings-created").textContent = timestamp(user.created_at);
    loadData();
    connectRealtime();
  }
  
async function loadNotificationStatus() {
  try {
    state.notificationStatus = await request(
      "/api/notification-status"
    );
  } catch (error) {
    state.notificationStatus = null;
  }

  renderGuardians();
}


  function notify(message) {
    const alert = $("#app-alert");
    alert.textContent = message;
    alert.classList.remove("is-hidden");
    window.clearTimeout(notify.timeout);
    notify.timeout = window.setTimeout(() => alert.classList.add("is-hidden"), 6500);
  }

  async function loadData() {
    try {
      const results = await Promise.all([
        request("/api/dashboard/current"),
        request("/api/dashboard/history?limit=100"),
        request("/api/baseline/current"),
        request("/api/baseline/daily?limit=100"),
        request("/api/baseline/adaptation?limit=100"),
        request("/api/incidents?limit=100"),
        request("/api/guardian"),
        request("/api/location/latest"),
        request("/api/actions?limit=100"),
        request("/api/demo/status"),
      ]);
      state.current = results[0];
      setTelemetry(results[0].hardware_telemetry);
      state.demo = results[9].demo || results[0].demo || { active: false, session: null };
      state.history = results[1].events || [];
      state.baseline = results[2].baseline || null;
      state.daily = results[3].records || [];
      state.adaptation = results[4].events || [];
      state.incidents = results[5].incidents || [];
      state.guardians = results[6].guardians || [];
      loadNotificationStatus();
      state.location = results[7].location || null;
      state.actions = results[8].actions || [];
      renderAll();
    } catch (error) {
      if (error.status === 401) {
        showAuth("login");
      } else {
        notify(`CARES could not load this workspace: ${error.message}`);
      }
    }
  }

  function connectRealtime() {
    state.eventSource?.close();

    const source = new EventSource("/api/events/stream");
    state.eventSource = source;

    source.onopen = () => {
      state.realtimeConnected = true;
      updateConnection(true);
    };

    source.onerror = () => {
      state.realtimeConnected = false;
      updateConnection(false);
    };

    const handleStreamMessage = (message) => {
      try {
        handleRealtime(JSON.parse(message.data));
      } catch (_) {
        // Ignore malformed stream frames.
      }
    };

    source.addEventListener("cares", handleStreamMessage);
    source.onmessage = handleStreamMessage;
  }

  // ---- ESP32 raw BPM telemetry (separate from risk-engine output) ----
  function setTelemetry(reading) {
    state.telemetry = reading || null;
    state.telemetryLocalAt = reading ? Date.now() - (Number(reading.age_seconds) || 0) * 1000 : null;
  }

  function ageText(seconds) {
    if (seconds < 60) return `${Math.max(0, Math.round(seconds))} s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    return `${Math.floor(seconds / 3600)} h ago`;
  }

  function renderTelemetry() {
    const value = $("#esp32-bpm");
    const caption = $("#esp32-caption");
    const card = $("#esp32-card");
    if (!value || !caption || !card) return;
    const reading = state.telemetry;
    if (!reading || !Number.isFinite(Number(reading.heart_rate))) {
      value.textContent = "—";
      caption.textContent = "No ESP32 reading received yet";
      card.classList.remove("is-stale");
      return;
    }
    const age = Math.max(0, (Date.now() - state.telemetryLocalAt) / 1000);
    const stale = age > TELEMETRY_STALE_SECONDS;
    value.textContent = number(reading.heart_rate, 1);
    const gps = reading.latitude != null && reading.longitude != null
      ? ` · GPS ${Number(reading.latitude).toFixed(4)}, ${Number(reading.longitude).toFixed(4)}`
      : "";
    caption.textContent = `${reading.device_id} · ${ageText(age)}${stale ? " · no recent reading, device may be offline" : ""}${gps}`;
    card.classList.toggle("is-stale", stale);
  }

  function handleRealtime(event) {
    if (!event || !event.type) return;
    if (event.type === "hardware_telemetry" && event.data) {
      // Display-only: update just the telemetry card, never the engine views.
      setTelemetry(event.data);
      renderTelemetry();
      return;
    }
    if (event.type === "engine_output" && event.data) {
      state.current = { ...(state.current || {}), engine_event: event.data };
      state.history = [event.data, ...state.history.filter((item) => item.id !== event.data.id)].slice(0, 100);
      refreshSecondaryData();
    } else if (event.type === "location" && event.data) {
      state.location = event.data;
      renderLocation();
      refreshSecondaryData();
    
} else if (event.type === "guardian_action" && event.data) {
  const action = event.data;

  state.actions = [
    action,
    ...state.actions.filter((item) => item.id !== action.id)
  ].slice(0, 100);

  const actionStatus = String(action.status || "").toUpperCase();
  const metadata = action.metadata || {};

  if (action.action_type === "GUARDIAN_NOTIFICATION") {
    const recipients = Array.isArray(metadata.recipients)
      ? metadata.recipients
      : [];

    const successfulRecipients = recipients.filter(
      (recipient) => recipient.success
    );

    const recipientNames = successfulRecipients
      .map((recipient) => recipient.guardian_name)
      .filter(Boolean);

    const recipientText = recipientNames.length
      ? ` to ${recipientNames.join(", ")}`
      : "";

    if (actionStatus === "DELIVERED") {
      notify(`✓ SMS delivered successfully${recipientText}.`);
    } else if (actionStatus === "PENDING") {
      notify(`SMS accepted by TextBee${recipientText}. Delivery is not confirmed yet.`);
    } else if (actionStatus === "SENT") {
      notify(`SMS sent to the provider${recipientText}. Delivery is not confirmed yet.`);
    } else if (actionStatus === "MOCK_SENT") {
      notify("Demo notification simulated. No real SMS was sent.");
    } else if (actionStatus === "FAILED") {
      notify("Guardian SMS failed. Please check the provider configuration.");
    } else if (actionStatus === "UNAVAILABLE") {
      notify("Guardian notification unavailable. Check guardian contacts and SMS configuration.");
    }
  }

  renderActions();
} else if (event.type === "incident" && event.data) {

    } else if (event.type === "incident" && event.data) {
      state.incidents = [event.data, ...state.incidents.filter((item) => item.id !== event.data.id)].slice(0, 100);
      renderIncidents();
    } else if (event.type === "demo_status" && event.data) {
      state.demo = event.data;
      renderDemo();
    }
    renderCurrent();
    renderCharts();
    renderTables();
  }

  async function refreshSecondaryData() {
    try {
      const [current, incidents, actions, location] = await Promise.all([
        request("/api/dashboard/current"), request("/api/incidents?limit=100"), request("/api/actions?limit=100"), request("/api/location/latest"),
      ]);
      state.current = current;
      setTelemetry(current.hardware_telemetry);
      renderTelemetry();
      state.demo = current.demo || state.demo;
      state.incidents = incidents.incidents || [];
      state.actions = actions.actions || [];
      state.location = location.location || null;
      renderAll();
    } catch (_) { /* The current event remains visible while the API recovers. */ }
  }

  function updateConnection(connected) {
    const labels = $$("#connection-status, #stream-label, #settings-stream");
    labels.forEach((element) => { element.textContent = connected ? "Live updates connected" : "Realtime unavailable"; });
    $("#connection-status .status-dot")?.classList.toggle("status-dot-muted", !connected);
    $("#stream-dot")?.classList.toggle("status-dot-muted", !connected);
    $("#stream-dot")?.classList.toggle("status-dot-alert", !connected);
    $("#monitoring-mode").textContent = connected ? "Live backend stream" : "Awaiting backend stream";
  }

  function navigate(page) {
    if (!$(`[data-page-view="${page}"]`)) return;
    state.page = page;
    $$("[data-page-view]").forEach((view) => view.classList.toggle("is-active", view.dataset.pageView === page));
    $$("[data-page]").forEach((item) => item.classList.toggle("is-active", item.dataset.page === page));
    $("#page-title").textContent = page.replaceAll("-", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
    $("#main-content").focus({ preventScroll: true });
    $(".sidebar")?.classList.remove("is-open");
  }

  function renderAll() {
    renderTelemetry();
    renderCurrent();
    renderActions();
    renderLocation();
    renderBaseline();
    renderTables();
    renderCharts();
    renderIncidents();
    renderGuardians();
    renderSettings();
    renderDemo();
  }

  function currentEvent() {
    return state.current?.engine_event || null;
  }

  function setFields(event) {
    const values = event ? {
      current_value: number(event.current_value, 1),
      baseline: number(event.baseline, 1),
      deviation: signedNumber(event.deviation, 1),
      percentage_deviation: signedNumber(event.percentage_deviation, 1),
      risk_score: number(event.risk_score, 1),
      confidence: percentage(Number(event.confidence) * 100, 0),
      confidence_fraction: percentage(Number(event.confidence) * 100, 0) + "%",
      trend: signedNumber(event.trend, 2),
      persistence: number(event.persistence, 1),
      recovery_state: event.recovery_state || "—",
      timestamp: timestamp(event.timestamp),
    } : {};
    $$('[data-field]').forEach((element) => { element.textContent = values[element.dataset.field] ?? "—"; });
  }

  function renderCurrent() {
    const event = currentEvent();
    const empty = !event;
    $("#dashboard-empty").classList.toggle("is-hidden", !empty);
    $("#dashboard-content").classList.toggle("is-hidden", empty);
    if (empty) return;
    setFields(event);
    const descriptor = riskDescriptor(event.risk_level);
    const riskCard = $("#risk-card");
    riskCard.classList.remove("risk-low", "risk-medium", "risk-high");
    riskCard.classList.add(descriptor.className);
    $("#risk-label").textContent = descriptor.label;
    $("#risk-summary").textContent = descriptor.summary;
    $("#risk-score-value").textContent = number(event.risk_score, 1);
    $("#why-deviation").textContent = `${signedNumber(event.deviation, 1)} BPM · ${signedNumber(event.percentage_deviation, 1)}%`;
    $("#engine-explanation").textContent = event.explanation || "No explanation was returned by the engine.";
    const codes = event.reason_codes || [];
    $("#reason-codes").innerHTML = codes.length ? codes.map((code) => `<span class="tag tag-teal">${escapeHTML(code)}</span>`).join("") : '<span class="tag tag-muted">No reason codes returned</span>';
    const session = state.current?.session || null;
    const source = event.source || session?.source || "REAL_HARDWARE";
    const status = session?.status || (state.realtimeConnected ? "ACTIVE" : "STOPPED");
    $("#monitoring-mode").textContent = `${source} · ${status}`;
    document.body.dataset.risk = String(event.risk_level || "");
  }

  function renderDemo() {
    const demo = state.demo || { active: false };
    const active = Boolean(demo.active);
    const start = $("#demo-start");
    const stop = $("#demo-stop");
    if (!start || !stop) return;
    start.disabled = active;
    // Keep Live Hardware clickable so users can always leave demo mode.
    stop.disabled = false;
    start.classList.toggle("mode-selected", active);
    stop.classList.toggle("mode-selected", !active);
    start.setAttribute("aria-pressed", String(active));
    stop.setAttribute("aria-pressed", String(!active));
    $("#demo-status").textContent = active
      ? `${demo.source || "SYNTHETIC DEMO"} · ${demo.scenario || "ACTIVE"} · ${demo.sample_count || 0} samples`
      : (demo.error ? `Error: ${demo.error}` : "Live hardware mode selected · waiting for actual sensor data");
    $("#demo-status").classList.toggle("is-active", active);
    $("#demo-status").classList.toggle("is-error", Boolean(demo.error));
    const sessionId = demo.session?.id || state.current?.session?.id || "—";
    const realSmsRequested = Boolean(demo.session?.metadata?.real_sms_requested);
    const smsMode = realSmsRequested ? "REAL SMS REQUESTED (simulated alert text)" : "MOCK (no real SMS)";
    $("#demo-source").textContent = `Data source: ${demo.source || "—"} · Monitoring status: ${active ? "ACTIVE" : "STOPPED"} · Session: ${sessionId} · Guardian SMS: ${smsMode}`;
    const description = demo.input_description || "Choose a scenario. The demo produces physiological samples only; the existing CARES engine decides every output.";
    $("#demo-description").textContent = description;
    $("#monitoring-status-dot")?.classList.toggle("status-dot-muted", !active);
  }

  function renderActions() {
    const eventActions = currentEvent()?.recommended_actions || [];
    const actions = (state.actions && state.actions.length) ? state.actions : eventActions.map((action) => ({ action_type: action, status: "GENERATED", timestamp: currentEvent()?.timestamp, engine_event_id: currentEvent()?.id }));

    const html = actions.slice(0, 8).map((action) => {
      const status = String(action.status || "GENERATED").toUpperCase();
      const statusClass = `status-${escapeHTML(status.toLowerCase())}`;
      const statusLabel = actionStatusLabel(status);
      return `<div class="action-row"><span class="action-name"><span class="action-symbol">${actionStatusSymbol(status)}</span><span>${escapeHTML(actionLabel(action.action_type))}</span></span><span class="action-status ${statusClass}" title="${status === "MOCK_SENT" ? "Simulated notification only. No real SMS was sent." : escapeHTML(status)}">${escapeHTML(statusLabel)}</span></div>`;
    }).join("");

    const content = html || '<div class="empty-inline">No guardian actions recorded yet.</div>';
    $("#dashboard-actions").innerHTML = content;

    $("#guardian-actions-table").innerHTML = actions.length
      ? `<div class="table-scroll"><table><thead><tr><th>Action</th><th>Status</th><th>Generated</th><th>Engine event</th></tr></thead><tbody>${actions.map((action) => {
          const status = String(action.status || "").toUpperCase();
          const statusClass = `status-${escapeHTML(status.toLowerCase())}`;
          const statusLabel = action.status ? actionStatusLabel(status) : "—";
          return `<tr><td><strong>${escapeHTML(action.action_type)}</strong></td><td><span class="action-status ${statusClass}" title="${status === "MOCK_SENT" ? "Simulated notification only. No real SMS was sent." : escapeHTML(statusLabel)}">${escapeHTML(statusLabel)}</span></td><td>${escapeHTML(timestamp(action.timestamp))}</td><td>#${escapeHTML(action.engine_event_id)}</td></tr>`;
        }).join("")}</tbody></table></div>`
      : '<div class="table-empty">No guardian action events yet.</div>';
  }

  function renderLocation() {
    const location = state.location;
    const username = state.user?.name || "there";
    const isDemo = Boolean(state.demo?.active) ||
      String(currentEvent()?.source || "").startsWith("DEMO_");

    $("#settings-gps").textContent = location ? "Connected" : "Not connected";

    // Keep the user's name visible even while the hardware GPS is unavailable.
    if (!location) {
      const message = isDemo
        ? "Hardware location unavailable in demonstration mode"
        : "Waiting for actual GPS coordinates from your CARES hardware.";

      $("#dashboard-location").innerHTML = `
        <div class="location-card">
          <h3>Current Location</h3>
          <p>Hello, ${escapeHTML(username)}! Your location will appear here.</p>
          <p class="location-status-text">${escapeHTML(message)}</p>
        </div>
      `;

      $("#location-details").innerHTML = `
        <div class="location-card">
          <h3>Hello, ${escapeHTML(username)}!</h3>
          <p>Your location is not available yet.</p>
          <p class="location-status-text">${escapeHTML(message)}</p>
          <p>CARES will display the location only after receiving valid coordinates from the connected GPS hardware.</p>
        </div>
      `;

      $("#map-container").innerHTML = `
        <div class="map-empty">
          <span>⌖</span>
          <p>Waiting for actual hardware GPS coordinates.</p>
        </div>
      `;

      const disclaimer = $("#map-container").nextElementSibling;
      if (disclaimer) {
        disclaimer.textContent =
          "The map will use real GPS coordinates received from CARES hardware. No location is estimated or generated.";
      }

      $("#location-notice").innerHTML = `
        <span class="notice-icon">⌖</span>
        <div>
          <strong>${escapeHTML(message)}</strong>
          <p>Hello, ${escapeHTML(username)}. Only actual hardware coordinates will be shown here.</p>
        </div>
      `;
      return;
    }

    const lat = Number(location.latitude);
    const lon = Number(location.longitude);

    if (!Number.isFinite(lat) || !Number.isFinite(lon) ||
        lat < -90 || lat > 90 || lon < -180 || lon > 180) {
      $("#dashboard-location").innerHTML = `
        <div class="location-card">
          <h3>Current Location</h3>
          <p>Hello, ${escapeHTML(username)}! GPS data was received, but the coordinates are invalid.</p>
        </div>
      `;
      $("#location-details").innerHTML =
        '<div class="empty-inline">Invalid GPS coordinates received from hardware.</div>';
      $("#map-container").innerHTML = `
        <div class="map-empty"><span>⌖</span><p>Invalid GPS coordinates.</p></div>
      `;
      return;
    }

    const address = location.formatted_address || location.address ||
      `${lat.toFixed(6)}, ${lon.toFixed(6)}`;

    $("#dashboard-location").innerHTML = `
      <div class="location-card">
        <h3>Current Location</h3>
        <p>Hello, ${escapeHTML(username)}! Your location is available.</p>
        <p>${escapeHTML(address)}</p>
        <p class="location-status-text">GPS coordinates received from hardware.</p>
      </div>
    `;

    const details = [
      ["Address", address],
      ["Latitude", lat.toFixed(6)],
      ["Longitude", lon.toFixed(6)],
      ["Source", location.source || location.device_id || "Hardware GPS"],
      ["Accuracy", location.accuracy_m == null ? "Not provided by hardware" : `${number(location.accuracy_m, 1)} m`],
      ["Updated", timestamp(location.timestamp || location.created_at)],
    ];
    $("#location-details").innerHTML = `
      <div class="location-card">
        <h3>Hello, ${escapeHTML(username)}! Your location is here.</h3>
        <p class="location-status-text">These details come from the actual hardware GPS event.</p>
        <dl class="location-data-list">
          ${details.map(([label, value]) => `<div><dt>${escapeHTML(label)}</dt><dd>${escapeHTML(value)}</dd></div>`).join("")}
        </dl>
      </div>
    `;

    const delta = 0.01;
    const bbox = `${lon - delta},${lat - delta},${lon + delta},${lat + delta}`;
    const mapSrc =
      `https://www.openstreetmap.org/export/embed.html?bbox=${encodeURIComponent(bbox)}&layer=mapnik&marker=${encodeURIComponent(`${lat},${lon}`)}`;
    const mapLink =
      `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${lat},${lon}`)}`;

    $("#map-container").innerHTML = `
      <iframe title="Map of current hardware location" loading="lazy" src="${mapSrc}"></iframe>
    `;

    const disclaimer = $("#map-container").nextElementSibling;
    if (disclaimer) {
      disclaimer.innerHTML = `
        <a href="${mapLink}" target="_blank" rel="noopener noreferrer">
          Open actual hardware coordinates in Google Maps ↗
        </a>
      `;
    }

    $("#location-notice").innerHTML = `
      <span class="notice-icon">⌖</span>
      <div>
        <strong>Hello, ${escapeHTML(username)}! Your GPS location is available.</strong>
        <p>Map and address are based on coordinates received from the hardware.</p>
      </div>
    `;
  }

  function renderBaseline() {
    const event = currentEvent();
    const baseline = state.baseline;
    $("#baseline-large").textContent = baseline ? number(baseline.baseline, 1) : (event ? number(event.baseline, 1) : "—");
    const calibrating = event?.reason_codes?.includes("BASELINE_CALIBRATING");
    $("#baseline-status").textContent = calibrating ? "Calibrating" : (baseline ? "Ready" : "Not available");
    $("#baseline-status").className = `tag ${calibrating ? "tag-medium" : baseline ? "tag-low" : "tag-muted"}`;
    $("#baseline-kicker").textContent = String(event?.source || "").startsWith("DEMO_") ? "DEMO BASELINE" : "PERSONAL BASELINE — REAL HARDWARE";
    $("#calibration-label").textContent = baseline?.status ? `${baseline.status}${baseline.calibration_progress === null ? "" : ` · ${number(baseline.calibration_progress, 0)}%`}` : (calibrating ? "Calibration in progress" : "Awaiting engine history");
    $("#calibration-bar").style.width = baseline?.calibration_progress === null || baseline?.calibration_progress === undefined ? "0%" : `${Math.max(0, Math.min(100, Number(baseline.calibration_progress)))}%`;
    $("#trusted-samples").textContent = baseline?.trusted_samples ?? (state.daily[0]?.trusted_samples ?? "—");
    $("#baseline-deviation").textContent = event ? `${signedNumber(event.deviation, 1)} BPM` : "—";
  }

  function tableEmpty(message) { return `<div class="table-empty">${escapeHTML(message)}</div>`; }

  function renderTables() {
    const events = state.history || [];
    const eventRows = events.slice(0, 30).map((event) => `<tr><td>${escapeHTML(timestamp(event.timestamp))}</td><td>${escapeHTML(event.source || "—")}</td><td><span class="table-risk table-risk-${escapeHTML(String(event.risk_level || "").toLowerCase())}">${escapeHTML(event.risk_level || "—")}</span></td><td>${number(event.current_value, 1)} BPM</td><td>${number(event.baseline, 1)} BPM</td><td>${signedNumber(event.deviation, 1)} BPM</td><td>${number(event.risk_score, 1)}</td><td class="table-explanation" title="${escapeHTML(event.explanation || "")}">${escapeHTML(event.explanation || "—")}</td></tr>`).join("");
    const eventTable = eventRows ? `<div class="table-scroll"><table><thead><tr><th>Timestamp</th><th>Source</th><th>Risk</th><th>HR</th><th>Baseline</th><th>Deviation</th><th>Score</th><th>Explanation</th></tr></thead><tbody>${eventRows}</tbody></table></div>` : tableEmpty("No engine events have been recorded yet.");
    $("#live-events-table").innerHTML = eventTable;
    $("#history-table").innerHTML = eventTable;

    const dailyRows = (state.daily || []).map((item) => `<tr><td>${escapeHTML(item.date)}</td><td>${number(item.mean_bpm, 1)}</td><td>${number(item.median_bpm, 1)}</td><td>${number(item.std_bpm, 1)}</td><td>${number(item.minimum_bpm, 1)}</td><td>${number(item.maximum_bpm, 1)}</td><td>${escapeHTML(item.trusted_samples)}</td><td>${escapeHTML(item.adaptation_updates)}</td><td>${escapeHTML(item.adaptation_holds)}</td></tr>`).join("");
    $("#daily-table").innerHTML = dailyRows ? `<div class="table-scroll"><table><thead><tr><th>Date</th><th>Mean</th><th>Median</th><th>Std dev</th><th>Minimum</th><th>Maximum</th><th>Trusted samples</th><th>Updates</th><th>Holds</th></tr></thead><tbody>${dailyRows}</tbody></table></div>` : tableEmpty("Daily baseline records will appear after the backend finalizes a day.");

    const adaptationRows = (state.adaptation || []).map((item) => `<tr><td>${escapeHTML(timestamp(item.timestamp))}</td><td>${number(item.previous_baseline, 1)}</td><td>${number(item.observation_mean, 1)}</td><td>${number(item.observation_std, 1)}</td><td>${signedNumber(item.deviation, 1)}</td><td>${escapeHTML(item.risk_level || "—")}</td><td>${item.signal_quality === null ? "—" : number(item.signal_quality, 2)}</td><td><span class="tag ${item.decision === "UPDATED" ? "tag-low" : "tag-muted"}">${escapeHTML(item.decision || "—")}</span></td><td>${number(item.new_baseline, 1)}</td><td class="table-explanation" title="${escapeHTML(item.reason || "")}">${escapeHTML(item.reason || "—")}</td></tr>`).join("");
    $("#adaptation-table").innerHTML = adaptationRows ? `<div class="table-scroll"><table><thead><tr><th>Timestamp</th><th>Previous</th><th>Observation</th><th>Std dev</th><th>Deviation</th><th>Risk</th><th>Signal quality</th><th>Decision</th><th>New baseline</th><th>Reason</th></tr></thead><tbody>${adaptationRows}</tbody></table></div>` : tableEmpty("No baseline adaptation audit events yet.");
  }

  function chartValues(events, field) {
    return events.slice().reverse().map((event) => {
      const value = Number(event[field]);
      return Number.isFinite(value) ? value : null;
    });
  }

  function makeChart(selector, series, colors, ariaLabel) {
    const container = $(selector);
    if (!container) return;
    const length = Math.max(...series.map((item) => item.values.length), 0);
    const points = series.flatMap((item) => item.values).filter((value) => value !== null);
    if (!length || !points.length) {
      container.innerHTML = '<div class="chart-empty">Waiting for backend events.</div>';
      return;
    }
    let min = Math.min(...points);
    let max = Math.max(...points);
    if (min === max) { min -= 1; max += 1; }
    const width = 640;
    const height = 205;
    const pad = { top: 12, right: 8, bottom: 20, left: 8 };
    const x = (index) => pad.left + (length === 1 ? 0 : index / (length - 1)) * (width - pad.left - pad.right);
    const y = (value) => height - pad.bottom - ((value - min) / (max - min)) * (height - pad.top - pad.bottom);
    const paths = series.map((item, seriesIndex) => {
      let path = "";
      let hasPoint = false;
      item.values.forEach((value, index) => {
        if (value === null) return;
        const command = hasPoint ? "L" : "M";
        path += `${command} ${x(index).toFixed(2)} ${y(value).toFixed(2)} `;
        hasPoint = true;
      });
      return path ? `<path d="${path}" fill="none" stroke="${colors[seriesIndex]}" stroke-linecap="round" stroke-linejoin="round" stroke-width="3"/>` : "";
    }).join("");
    const grid = [0, .5, 1].map((ratio) => `<line x1="${pad.left}" y1="${(pad.top + ratio * (height - pad.top - pad.bottom)).toFixed(2)}" x2="${width - pad.right}" y2="${(pad.top + ratio * (height - pad.top - pad.bottom)).toFixed(2)}" stroke="#e3eceb" stroke-width="1"/>`).join("");
    container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHTML(ariaLabel)}"><title>${escapeHTML(ariaLabel)}</title>${grid}<text x="8" y="14" fill="#82979d" font-size="11">${escapeHTML(number(max, 1))}</text><text x="8" y="196" fill="#82979d" font-size="11">${escapeHTML(number(min, 1))}</text>${paths}</svg>`;
  }

  function renderCharts() {
    const events = state.history || [];
    makeChart("#chart-hr", [{ values: chartValues(events, "current_value") }, { values: chartValues(events, "baseline") }], ["#0b6671", "#e7b875"], "Heart rate and personal baseline");
    makeChart("#chart-risk", [{ values: chartValues(events, "risk_score") }], ["#b44246"], "Risk score");
    makeChart("#chart-deviation", [{ values: chartValues(events, "deviation") }], ["#0b6671"], "Deviation from personal baseline");
    makeChart("#chart-confidence", [{ values: chartValues(events, "confidence") }], ["#197a64"], "Evidence confidence");
    makeChart("#history-chart-hr", [{ values: chartValues(events, "current_value") }, { values: chartValues(events, "baseline") }], ["#0b6671", "#e7b875"], "Historical heart rate and personal baseline");
    makeChart("#history-chart-risk", [{ values: chartValues(events, "risk_score") }], ["#b44246"], "Historical risk score");
  }

  function renderIncidents() {
    const incidents = state.incidents || [];
    if (!incidents.length) {
      $("#incidents-list").innerHTML = '<div class="empty-state"><div class="empty-icon">✓</div><h2>No incidents recorded</h2><p>When the backend records an actual high-risk engine event, its explanation and available location will appear here.</p></div>';
      return;
    }
    $("#incidents-list").innerHTML = incidents.map((incident) => {
      const event = incident.engine_event || {};
      const location = incident.location || null;
      const actionText = (incident.actions || []).map((action) => `${action.action_type} · ${actionStatusLabel(action.status)}`).join("; ") || "No actions recorded";
      return `<article class="incident-card"><div class="incident-card-head"><div><p class="eyebrow eyebrow-light">Recorded CARES incident · ${escapeHTML(incident.source || event.source || "—")}</p><h2>${escapeHTML(incident.risk_level || "HIGH")} · Engine event #${escapeHTML(incident.engine_event_id)}</h2></div><span class="incident-time">${escapeHTML(timestamp(incident.timestamp))}</span></div><p class="incident-explanation">${escapeHTML(incident.explanation || event.explanation || "No explanation returned.")}</p><div class="incident-meta"><div><span>HR</span><strong>${number(event.current_value, 1)} BPM</strong></div><div><span>Personal baseline</span><strong>${number(event.baseline, 1)} BPM</strong></div><div><span>Deviation</span><strong>${signedNumber(event.deviation, 1)} BPM</strong></div><div><span>Location</span><strong>${location ? escapeHTML(location.formatted_address || `${location.latitude}, ${location.longitude}`) : "Hardware location unavailable"}</strong></div><div><span>Actions</span><strong>${escapeHTML(actionText)}</strong></div><div><span>Status</span><strong>${escapeHTML(incident.status || "OPEN")}</strong></div></div></article>`;
    }).join("");
  }

  function renderGuardians() {
    const guardians = state.guardians || [];
    $("#guardians-list").innerHTML = guardians.length ? guardians.map((guardian) => `<div class="guardian-card"><span class="guardian-avatar">${escapeHTML(initials(guardian.name))}</span><div class="guardian-card-main"><strong>${escapeHTML(guardian.name)}</strong><span>${escapeHTML(guardian.relationship)} · ${escapeHTML(guardian.phone_number)}</span><span class="tag tag-low">Guardian contact saved</span>${state.notificationStatus?.configured
  ? '<span class="tag tag-low">TextBee configured</span>'
  : state.notificationStatus?.provider === "mock"
    ? '<span class="tag tag-muted">Mock mode · simulated only</span>'
    : '<span class="tag tag-muted">Notification status unavailable</span>'}</div><div class="guardian-actions"><button class="mini-button" data-edit-guardian="${guardian.id}" type="button">Edit</button><button class="mini-button danger" data-delete-guardian="${guardian.id}" type="button">Remove</button></div></div>`).join("") : '<div class="empty-inline">No guardian contacts saved yet.</div>';
  }

  function renderSettings() {
    $("#settings-name").textContent = state.user?.name || "—";
    $("#settings-email").textContent = state.user?.email || "—";
    $("#settings-created").textContent = timestamp(state.user?.created_at);
    $("#settings-gps").textContent = state.location ? "Connected" : "Not connected";
  }

  function resetGuardianForm() {
    $("#guardian-id").value = "";
    $("#guardian-form").reset();
    $("#guardian-form-heading").textContent = "Add a guardian";
    $("#guardian-cancel").classList.add("is-hidden");
    setMessage("#guardian-form-message");
  }

  function editGuardian(id) {
    const guardian = state.guardians.find((item) => String(item.id) === String(id));
    if (!guardian) return;
    $("#guardian-id").value = guardian.id;
    $("#guardian-name").value = guardian.name;
    $("#guardian-relationship").value = guardian.relationship;
    $("#guardian-phone").value = guardian.phone_number;
    $("#guardian-form-heading").textContent = "Edit guardian";
    $("#guardian-cancel").classList.remove("is-hidden");
    navigate("guardian");
  }

  async function submitGuardian(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const id = $("#guardian-id").value;
    try {
      const payload = { name: $("#guardian-name").value.trim(), relationship: $("#guardian-relationship").value.trim(), phone_number: $("#guardian-phone").value.trim() };
      if (id) await request(`/api/guardian/${encodeURIComponent(id)}`, { method: "PUT", body: payload });
      else await request("/api/guardian", { method: "POST", body: payload });
      resetGuardianForm();
      await refreshSecondaryData();
      state.guardians = (await request("/api/guardian")).guardians || [];
      renderGuardians();
      notify("Guardian contact saved. No notification was sent.");
    } catch (error) { setMessage("#guardian-form-message", error.message); }
  }

  async function deleteGuardian(id) {
    if (!window.confirm("Remove this guardian contact?")) return;
    try {
      await request(`/api/guardian/${encodeURIComponent(id)}`, { method: "DELETE" });
      state.guardians = (await request("/api/guardian")).guardians || [];
      renderGuardians();
    } catch (error) { notify(error.message); }
  }

  async function login(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    setMessage('[data-form-message="login"]', "Signing in…");
    try {
      const payload = await request("/api/auth/login", { method: "POST", body: { email: $("#login-email").value.trim(), password: $("#login-password").value } });
      form.reset();
      showApp(payload.user);
    } catch (error) { setMessage('[data-form-message="login"]', error.message); }
  }

  async function register(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    if ($("#register-password").value !== $("#register-confirm").value) {
      setMessage('[data-form-message="register"]', "Passwords do not match.");
      return;
    }
    setMessage('[data-form-message="register"]', "Creating your account…");
    try {
      await request("/api/auth/register", { method: "POST", body: { name: $("#register-name").value.trim(), email: $("#register-email").value.trim(), password: $("#register-password").value } });
      showAuth("login");
      setMessage('[data-form-message="login"]', "Account created. You can now log in.");
    } catch (error) { setMessage('[data-form-message="register"]', error.message); }
  }

  async function logout() {
    try { await request("/api/auth/logout", { method: "POST" }); } catch (_) { /* A local logout still clears the view. */ }
    state.user = null;
    showAuth("login");
  }

  async function startDemo() {
    const button = $("#demo-start");
    button.disabled = true;
    const sendRealSms = Boolean($("#demo-real-sms")?.checked);
    if (sendRealSms && !window.confirm("This demo will send a REAL SMS to your registered guardian(s) when the simulated HIGH-risk alert fires. The message will be labelled as a simulated demonstration. Continue?")) {
      button.disabled = false;
      return;
    }
    setMessage("#demo-status", "Starting…");
    try {
      const payload = await request("/api/demo/start", { method: "POST", body: { scenario: $("#demo-scenario").value, send_real_sms: sendRealSms } });
      state.demo = payload.demo;
      renderDemo();
      notify(`Demo started: ${payload.demo.source}. Every decision is produced by CARES.`);
    } catch (error) {
      state.demo = { active: false, error: error.message };
      renderDemo();
      notify(error.message);
    }
  }

  async function stopDemo() {
    const stopButton = $("#demo-stop");
    stopButton.disabled = true;

    // Live hardware is the default stream. Only call the stop endpoint when
    // a synthetic/demo session is actually active.
    if (!state.demo?.active) {
      state.demo = { ...state.demo, active: false };
      renderDemo();
      try { await loadData(); } catch (_) {}
      notify("Live hardware mode selected. CARES is waiting for real ESP32/GPS readings.");
      return;
    }

    try {
      const payload = await request("/api/demo/stop", { method: "POST" });
      state.demo = payload.demo || { active: false };
      renderDemo();
      await loadData();
      notify("Synthetic demo stopped. Live hardware mode selected; waiting for actual sensor readings.");
    } catch (error) {
      renderDemo();
      notify(`Could not switch to live mode: ${error.message}`);
    }
  }

  function bindEvents() {
    $$("[data-auth-tab]").forEach((button) => button.addEventListener("click", () => showAuth(button.dataset.authTab)));
    $("#login-form").addEventListener("submit", login);
    $("#register-form").addEventListener("submit", register);
    $("#guardian-form").addEventListener("submit", submitGuardian);
    $("#guardian-cancel").addEventListener("click", resetGuardianForm);
    $("#logout-button").addEventListener("click", logout);
    $("#demo-start").addEventListener("click", startDemo);
    $("#demo-stop").addEventListener("click", stopDemo);
    document.addEventListener("click", (event) => {
      const pageButton = event.target.closest("[data-page]");
      if (pageButton) { navigate(pageButton.dataset.page); $(".sidebar")?.classList.remove("is-open"); }
      const editButton = event.target.closest("[data-edit-guardian]");
      if (editButton) editGuardian(editButton.dataset.editGuardian);
      const deleteButton = event.target.closest("[data-delete-guardian]");
      if (deleteButton) deleteGuardian(deleteButton.dataset.deleteGuardian);
    });
    $("#mobile-menu").addEventListener("click", () => {
      const sidebar = $(".sidebar");
      const open = sidebar.classList.toggle("is-open");
      $("#mobile-menu").setAttribute("aria-expanded", String(open));
    });
    $$("[data-toggle]").forEach((button) => button.addEventListener("click", () => {
      const target = $(`#${button.dataset.toggle}`);
      const expanded = target.classList.toggle("is-hidden") === false;
      button.setAttribute("aria-expanded", String(expanded));
      button.textContent = expanded ? "Collapse" : "Expand";
    }));
  }

  async function boot() {
    bindEvents();
    // Keep the "N s ago" / stale indicator current between readings.
    window.setInterval(renderTelemetry, 1000);
    try {
      const payload = await request("/api/auth/me");
      showApp(payload.user);
    } catch (_) {
      showAuth("login");
    }
  }

  window.addEventListener("DOMContentLoaded", boot);
})();