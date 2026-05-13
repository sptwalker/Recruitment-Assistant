const WS_URL = "ws://127.0.0.1:8765";
const EXTENSION_VERSION = "1.8.0";
const HEARTBEAT_INTERVAL_MS = 15000;
let ws = null;
let heartbeatTimer = null;
let reconnectDelay = 1000;
let collectState = "idle";
let activeRunId = "";
let lastDownloadIntent = null;
const pendingDownloads = new Map();

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    reconnectDelay = 1000;
    sendToServer({ type: "extension_connected", data: { version: EXTENSION_VERSION, downloads_api: true } });
    startHeartbeat();
    updateBadge("on");
    setTimeout(() => sendToContentScript({ type: "probe_page", run_id: activeRunId }), 300);
  };

  ws.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    handleServerCommand(msg);
  };

  ws.onclose = (event) => {
    stopHeartbeat();
    ws = null;
    updateBadge("off");
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 30000);
  };

  ws.onerror = () => { ws?.close(); };
}

function handleServerCommand(msg) {
  const { type, config, run_id } = msg;
  if (run_id) activeRunId = run_id;
  switch (type) {
    case "start_collect":
      collectState = "collecting";
      sendToContentScript({ type: "start_collect", config, run_id: activeRunId });
      break;
    case "pause_collect":
      collectState = "paused";
      sendToContentScript({ type: "pause_collect", run_id: activeRunId });
      break;
    case "resume_collect":
      collectState = "collecting";
      sendToContentScript({ type: "resume_collect", run_id: activeRunId });
      break;
    case "stop_collect":
      collectState = "idle";
      sendToContentScript({ type: "stop_collect", run_id: activeRunId });
      break;
    case "probe_page":
      sendToContentScript({ type: "probe_page", run_id: activeRunId });
      break;
  }
}

function bossChatUrlPatterns() {
  return ["https://www.zhipin.com/*"];
}

function isBossCandidatePage(url = "") {
  return /^https:\/\/www\.zhipin\.com\//.test(url) && /chat|friend|geek|boss|web/.test(url);
}

async function ensureContentScript(tabId) {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
    return true;
  } catch (error) {
    return false;
  }
}

function sendToContentScript(msg) {
  chrome.tabs.query({ url: bossChatUrlPatterns() }, async (tabs) => {
    const candidateTabs = tabs.filter((tab) => isBossCandidatePage(tab.url || ""));
    sendToServer({ type: "boss_tabs_scanned", data: { count: candidateTabs.length, urls: candidateTabs.map((tab) => tab.url || "") } });

    for (const tab of candidateTabs) {
      chrome.tabs.sendMessage(tab.id, msg, async () => {
        if (!chrome.runtime.lastError) return;
        const injected = await ensureContentScript(tab.id);
        if (!injected) {
          sendToServer({ type: "content_script_inject_failed", data: { tab_id: tab.id, url: tab.url || "" } });
          return;
        }
        chrome.tabs.sendMessage(tab.id, msg, () => {
          if (chrome.runtime.lastError) {
            sendToServer({ type: "content_script_message_failed", data: { tab_id: tab.id, url: tab.url || "", error: chrome.runtime.lastError.message } });
          }
        });
      });
    }
  });
}

function sendToServer(event) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(event));
  }
}

function startHeartbeat() {
  stopHeartbeat();
  sendToServer({ type: "heartbeat", data: { version: EXTENSION_VERSION, collect_state: collectState, at: Date.now() } });
  heartbeatTimer = setInterval(() => {
    sendToServer({ type: "heartbeat", data: { version: EXTENSION_VERSION, collect_state: collectState, at: Date.now() } });
  }, HEARTBEAT_INTERVAL_MS);
}

function stopHeartbeat() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

function updateBadge(state) {
  const text = state === "on" ? "" : "!";
  const color = state === "on" ? "#4A90E2" : "#999";
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
}

function rememberDownloadIntent(data) {
  lastDownloadIntent = { ...data, run_id: data.run_id || activeRunId, at: Date.now() };
  sendToServer({ type: "download_intent_registered", data: lastDownloadIntent });
}

function getFreshDownloadIntent() {
  if (!lastDownloadIntent) return null;
  if (Date.now() - lastDownloadIntent.at > 30000) return null;
  return lastDownloadIntent;
}

chrome.downloads.onCreated.addListener((item) => {
  const intent = getFreshDownloadIntent();
  if (!intent) return;
  pendingDownloads.set(item.id, { ...intent, download_id: item.id, started_at: Date.now() });
  sendToServer({
    type: "download_created",
    data: { ...intent, download_id: item.id, filename: item.filename || item.url || "" },
  });
});

chrome.downloads.onChanged.addListener((delta) => {
  const pending = pendingDownloads.get(delta.id);
  if (!pending) return;

  if (delta.state?.current === "complete") {
    chrome.downloads.search({ id: delta.id }, (items) => {
      const item = items?.[0] || {};
      pendingDownloads.delete(delta.id);
      sendToServer({
        type: "resume_downloaded",
        data: {
          ...pending,
          download_id: delta.id,
          filename: item.filename || pending.filename || "",
          download_path: item.filename || "",
          url: item.url || "",
          mime: item.mime || "",
          file_size: item.fileSize || 0,
        },
      });
    });
  } else if (delta.error?.current) {
    pendingDownloads.delete(delta.id);
    sendToServer({
      type: "candidate_skipped",
      data: { ...pending, reason: `download_error:${delta.error.current}` },
    });
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.target === "background") {
    if (msg.event?.type === "download_intent") {
      rememberDownloadIntent(msg.event.data || {});
    } else {
      sendToServer(msg.event);
    }
    sendResponse({ ok: true });
  } else if (msg.type === "get_state") {
    sendResponse({ connected: ws?.readyState === WebSocket.OPEN, collectState });
  }
  return true;
});

connect();
