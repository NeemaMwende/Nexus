// Nexus Monitor popup.js

let sseSource = null;
let activityLog = [];

// ── Tabs ─────────────────────────────────────────────────────────────────────

document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`tab-${tab.dataset.tab}`).classList.add("active");

    if (tab.dataset.tab === "activity") {
      connectSSE();
    }
  });
});

// ── Load status on open ───────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  await loadSettings();
  await refreshStatus();
  setupEventListeners();
});

async function refreshStatus() {
  const data = await new Promise(resolve =>
    chrome.runtime.sendMessage({ type: "get_status" }, resolve)
  );

  const dot    = document.getElementById("statusDot");
  const status = data?.nexusStatus || "offline";

  dot.className = `status-dot ${status}`;
  document.getElementById("agentStatus").textContent =
    status === "online" ? "Online ✓" : "Offline ✗";
  document.getElementById("agentStatus").className =
    `stat-value ${status === "online" ? "green" : ""}`;

  document.getElementById("vectorCount").textContent =
    data?.vectorCount?.toLocaleString() || "—";
  document.getElementById("llmProvider").textContent =
    data?.llmProvider || "—";
  document.getElementById("pollInterval").textContent =
    data?.pollInterval ? `${data.pollInterval}s` : "—";

  if (data?.lastChecked) {
    const ago = Math.round((Date.now() - data.lastChecked) / 1000);
    document.getElementById("lastChecked").textContent =
      `Last checked: ${ago}s ago`;
  }
}

// ── Settings ─────────────────────────────────────────────────────────────────

async function loadSettings() {
  const s = await chrome.storage.sync.get(["nexusUrl", "nexusSecret", "notificationsEnabled"]);
  document.getElementById("nexusUrl").value    = s.nexusUrl    || "http://localhost:8000";
  document.getElementById("nexusSecret").value = s.nexusSecret || "";
  setToggle(s.notificationsEnabled ?? true);
}

function setToggle(on) {
  const sw = document.getElementById("notifSwitch");
  sw.className = `toggle-switch ${on ? "on" : ""}`;
  sw._state = on;
}

document.getElementById("notifToggle").addEventListener("click", () => {
  const sw  = document.getElementById("notifSwitch");
  const cur = sw._state ?? false;
  setToggle(!cur);
});

document.getElementById("saveSettings").addEventListener("click", async () => {
  const sw = document.getElementById("notifSwitch");
  await chrome.storage.sync.set({
    nexusUrl:             document.getElementById("nexusUrl").value.trim(),
    nexusSecret:          document.getElementById("nexusSecret").value.trim(),
    notificationsEnabled: sw._state ?? true,
  });
  const btn = document.getElementById("saveSettings");
  btn.textContent = "Saved ✓";
  setTimeout(() => btn.textContent = "Save settings", 1500);

  // Reconnect SSE with new URL
  if (sseSource) { sseSource.close(); sseSource = null; }
});

// ── Trigger buttons ───────────────────────────────────────────────────────────

function setupEventListeners() {
  document.getElementById("triggerPoll").addEventListener("click", async () => {
    await apiPost("/trigger/poll");
    const btn = document.getElementById("triggerPoll");
    btn.textContent = "Triggered ✓";
    setTimeout(() => btn.textContent = "⟳ Poll now", 2000);
  });

  document.getElementById("triggerIngest").addEventListener("click", async () => {
    await apiPost("/trigger/ingest");
    const btn = document.getElementById("triggerIngest");
    btn.textContent = "Ingesting...";
    setTimeout(() => btn.textContent = "↑ Re-ingest Salesmate", 3000);
  });
}

async function apiPost(path) {
  const s = await chrome.storage.sync.get(["nexusUrl", "nexusSecret"]);
  const url    = s.nexusUrl    || "http://localhost:8000";
  const secret = s.nexusSecret || "";

  try {
    await fetch(`${url}${path}`, {
      method:  "POST",
      headers: {
        "Content-Type":   "application/json",
        "X-Nexus-Secret": secret,
      },
    });
  } catch (e) {
    console.error("[Nexus popup] API call failed:", e);
  }
}

// ── SSE connection ────────────────────────────────────────────────────────────

async function connectSSE() {
  if (sseSource) return; // already connected

  const s = await chrome.storage.sync.get(["nexusUrl", "nexusSecret"]);
  const url = s.nexusUrl || "http://localhost:8000";

  const feed = document.getElementById("activityFeed");
  feed.innerHTML = '<div class="connecting">Connecting...</div>';

  sseSource = new EventSource(`${url}/stream/status`);

  sseSource.addEventListener("connected", () => {
    feed.innerHTML = "";
    addFeedItem({ type: "connected", message: "Connected to Nexus live stream" }, "started");
  });

  sseSource.addEventListener("processing_started", e => {
    const data = JSON.parse(e.data);
    addFeedItem(data, "started");
    // Forward to background for notifications
    chrome.runtime.sendMessage({ type: "nexus_event", event: data });
  });

  sseSource.addEventListener("processing_done", e => {
    const data = JSON.parse(e.data);
    addFeedItem(data, data.reply_sent ? "done" : "spam");
    chrome.runtime.sendMessage({ type: "nexus_event", event: data });
    refreshStatus();
  });

  sseSource.addEventListener("processing_error", e => {
    const data = JSON.parse(e.data);
    addFeedItem(data, "error");
    chrome.runtime.sendMessage({ type: "nexus_event", event: data });
  });

  sseSource.onerror = () => {
    addFeedItem({ message: "Connection lost — retrying..." }, "error");
    sseSource.close();
    sseSource = null;
    setTimeout(connectSSE, 5000);
  };
}

function addFeedItem(data, cssClass = "") {
  const feed = document.getElementById("activityFeed");

  // Remove placeholder
  const placeholder = feed.querySelector(".connecting");
  if (placeholder) placeholder.remove();

  const item = document.createElement("div");
  item.className = `feed-item ${cssClass}`;

  const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const icon = {
    done:    "✓",
    spam:    "⚠",
    error:   "✗",
    started: "→",
  }[cssClass] || "•";

  item.innerHTML = `
    <strong>${icon} ${data.type?.replace(/_/g, " ") || "event"}</strong><br>
    ${data.message || ""}
    ${data.intent ? `<br><span style="color:#F4801A">intent: ${data.intent}</span>` : ""}
    <div class="feed-time">${time}</div>
  `;

  feed.insertBefore(item, feed.firstChild);

  // Keep only last 20 items
  while (feed.children.length > 20) {
    feed.removeChild(feed.lastChild);
  }
}
