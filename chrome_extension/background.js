const WS_URL = "ws://127.0.0.1:8765";
const EXTENSION_VERSION = "1.53.0";
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
    case "reset_content_script":
      sendToContentScript({ type: "reset_content_script", run_id: activeRunId });
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

function shouldTargetSingleTab(msg) {
  return ["start_collect", "pause_collect", "resume_collect", "stop_collect", "reset_content_script"].includes(msg?.type);
}

function pickBossTab(candidateTabs) {
  return candidateTabs.find((tab) => tab.active && tab.highlighted) || candidateTabs.find((tab) => tab.active) || candidateTabs[0] || null;
}

function sendMessageToTab(tab, msg) {
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

function sendToContentScript(msg) {
  chrome.tabs.query({ url: bossChatUrlPatterns() }, async (tabs) => {
    const candidateTabs = tabs.filter((tab) => isBossCandidatePage(tab.url || ""));
    if (shouldTargetSingleTab(msg)) {
      const tab = pickBossTab(candidateTabs);
      if (tab) sendMessageToTab(tab, msg);
      return;
    }

    for (const tab of candidateTabs) {
      sendMessageToTab(tab, msg);
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

function makeSafeDownloadFilename(data = {}, fallbackExt = ".pdf") {
  const candidateInfo = data.candidate_info || {};
  const clean = (value, fallback) => {
    const text = String(value || fallback || "")
      .replace(/[\\/:*?"<>|\r\n\t]+/g, "")
      .replace(/\s+/g, "")
      .replace(/[，,。.;；()（）\[\]【】]+/g, "")
      .slice(0, 24);
    return text || fallback;
  };
  const name = clean(candidateInfo.name, "未知姓名");
  const age = clean(candidateInfo.age, "未知年龄");
  const education = clean(candidateInfo.education, "未知学历");
  const stamp = new Date().toISOString().replace(/[-:T.Z]/g, "").slice(0, 14);
  const ext = /^\.[a-z0-9]{1,8}$/i.test(fallbackExt) ? fallbackExt : ".pdf";
  return `Boss直聘/${name}-${age}-${education}-BOSS直聘-${stamp}${ext}`;
}

function getFreshDownloadIntent() {
  if (!lastDownloadIntent) return null;
  if (Date.now() - lastDownloadIntent.at > 120000) return null;
  return lastDownloadIntent;
}

function notifyDownloadResult(type, data) {
  chrome.tabs.query({ url: bossChatUrlPatterns() }, (tabs) => {
    for (const tab of tabs.filter((tab) => isBossCandidatePage(tab.url || ""))) {
      chrome.tabs.sendMessage(tab.id, { type, data }, () => {});
    }
  });
}

function normalizeDirectDownloadUrl(url = "") {
  if (!url) return "";
  try {
    return new URL(url, "https://www.zhipin.com").href;
  } catch {
    return "";
  }
}

function downloadDirectUrl(data = {}, sendResponse = () => {}) {
  const rawUrl = data.url || data.direct_url || data.iframe_src || data.src || "";
  const url = normalizeDirectDownloadUrl(rawUrl);
  const baseIntent = { ...data, url, run_id: data.run_id || activeRunId, at: Date.now(), direct_url: url, raw_direct_url: rawUrl };
  sendToServer({ type: "direct_download_request_received", data: { ...baseIntent, url_valid: Boolean(url && /^https:\/\//i.test(url)) } });

  let responded = false;
  const respondOnce = (response) => {
    if (responded) return;
    responded = true;
    clearTimeout(responseTimer);
    sendToServer({ type: "direct_download_response_sent", data: { ...baseIntent, ...response } });
    try { sendResponse(response); } catch (error) {
      sendToServer({ type: "direct_download_response_error", data: { ...baseIntent, reason: String(error) } });
    }
  };
  const responseTimer = setTimeout(() => {
    const reason = "chrome_download_callback_timeout";
    sendToServer({ type: "direct_download_callback_timeout", data: { ...baseIntent, reason } });
    notifyDownloadResult("download_failed", { ...baseIntent, reason });
    respondOnce({ ok: false, reason });
  }, 8000);

  if (!url || !/^https:\/\//i.test(url)) {
    respondOnce({ ok: false, reason: "invalid_download_url", raw_url: rawUrl });
    return;
  }

  const intent = baseIntent;
  lastDownloadIntent = intent;
  sendToServer({ type: "direct_download_starting", data: intent });
  try {
    chrome.downloads.download({ url, filename: makeSafeDownloadFilename(intent), conflictAction: "uniquify", saveAs: false }, (downloadId) => {
      if (chrome.runtime.lastError || !downloadId) {
        const reason = chrome.runtime.lastError?.message || "download_start_failed";
        sendToServer({ type: "direct_download_failed", data: { ...intent, reason } });
        notifyDownloadResult("download_failed", { ...intent, reason });
        respondOnce({ ok: false, reason });
        return;
      }
      pendingDownloads.set(downloadId, { ...intent, download_id: downloadId, started_at: Date.now() });
      sendToServer({ type: "download_created", data: { ...intent, download_id: downloadId, filename: url } });
      respondOnce({ ok: true, download_id: downloadId });
    });
  } catch (error) {
    const reason = String(error);
    sendToServer({ type: "direct_download_failed", data: { ...intent, reason } });
    notifyDownloadResult("download_failed", { ...intent, reason });
    respondOnce({ ok: false, reason });
  }
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
      const data = {
        ...pending,
        download_id: delta.id,
        filename: item.filename || pending.filename || "",
        download_path: item.filename || "",
        url: item.url || "",
        mime: item.mime || "",
        file_size: item.fileSize || 0,
      };
      sendToServer({ type: "resume_downloaded", data });
      notifyDownloadResult("download_completed", data);
    });
  } else if (delta.error?.current) {
    pendingDownloads.delete(delta.id);
    const data = { ...pending, reason: `download_error:${delta.error.current}` };
    sendToServer({ type: "candidate_skipped", data });
    notifyDownloadResult("download_failed", data);
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
  } else if (msg.type === "download_direct_url") {
    downloadDirectUrl(msg.data || {}, sendResponse);
    return true;
  } else if (msg.type === "get_state") {
    sendResponse({ connected: ws?.readyState === WebSocket.OPEN, collectState });
  }
  return true;
});

connect();
