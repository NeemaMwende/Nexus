// Nexus Monitor — background service worker
// Polls Nexus /health every 60s and fires Chrome notifications on new activity

const DEFAULT_NEXUS_URL = "http://localhost:8000";
let lastKnownVectorCount = 0;

// ── Alarm-based polling (service workers can't use setInterval) ──────────────

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("nexus_poll", { periodInMinutes: 1 });
  console.log("[Nexus] Background worker installed");
});

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "nexus_poll") {
    await pollNexusStatus();
  }
});

async function pollNexusStatus() {
  const { nexusUrl, nexusSecret, notificationsEnabled } =
    await chrome.storage.sync.get(["nexusUrl", "nexusSecret", "notificationsEnabled"]);

  const url    = nexusUrl    || DEFAULT_NEXUS_URL;
  const secret = nexusSecret || "";

  try {
    const resp = await fetch(`${url}/health`, {
      headers: secret ? { "X-Nexus-Secret": secret } : {},
    });

    if (!resp.ok) {
      await setIcon("offline");
      await chrome.storage.local.set({ nexusStatus: "offline", lastChecked: Date.now() });
      return;
    }

    const data = await resp.json();
    await setIcon("online");
    await chrome.storage.local.set({
      nexusStatus:     "online",
      vectorCount:     data.qdrant_vectors,
      llmProvider:     data.llm_provider,
      pollInterval:    data.poll_interval,
      lastChecked:     Date.now(),
    });

  } catch (e) {
    await setIcon("offline");
    await chrome.storage.local.set({ nexusStatus: "offline", lastChecked: Date.now() });
  }
}

// Receive events forwarded from popup's SSE connection
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "nexus_event") {
    handleNexusEvent(message.event);
  }
  if (message.type === "get_status") {
    chrome.storage.local.get(
      ["nexusStatus", "vectorCount", "llmProvider", "pollInterval", "lastChecked"],
      sendResponse
    );
    return true; // async response
  }
});

async function handleNexusEvent(event) {
  const { notificationsEnabled } = await chrome.storage.sync.get("notificationsEnabled");
  if (!notificationsEnabled) return;

  if (event.type === "processing_done" && event.reply_sent) {
    chrome.notifications.create({
      type:    "basic",
      iconUrl: "icons/icon48.png",
      title:   "Nexus — Reply Sent ✓",
      message: `Intent: ${event.intent || "unknown"}\n${event.message || ""}`,
    });
  }

  if (event.type === "processing_error") {
    chrome.notifications.create({
      type:    "basic",
      iconUrl: "icons/icon48.png",
      title:   "Nexus — Error ✗",
      message: event.message || "Processing failed",
    });
  }
}

async function setIcon(state) {
  // Sets badge colour: green = online, red = offline
  chrome.action.setBadgeText({ text: state === "online" ? "ON" : "OFF" });
  chrome.action.setBadgeBackgroundColor({
    color: state === "online" ? "#22c55e" : "#ef4444",
  });
}
