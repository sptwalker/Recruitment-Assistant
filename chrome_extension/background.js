const WS_URL = "ws://127.0.0.1:8765";
let ws = null;
let reconnectDelay = 1000;
let collectState = "idle";
let activeRunId = "";
let lastDownloadIntent = null;
const pendingDownloads = new Map();

function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    reconnectDelay = 1000;
    ws.send(JSON.stringify({ type: "extension_connected", data: { version: "1.2.0", downloads_api: true } }));
    updateBadge("on");
  };

  ws.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    handleServerCommand(msg);
  };

  ws.onclose = () => {
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
  }
}

function sendToContentScript(msg) {
  chrome.tabs.query({ url: "https://www.zhipin.com/web/*chat*" }, (tabs) => {
    for (const tab of tabs) {
      chrome.tabs.sendMessage(tab.id, msg);
    }
  });
}

function sendToServer(event) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(event));
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
