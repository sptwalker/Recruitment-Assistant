// 平台注册表：与 content.js 同步维护。
// 每个平台一个 WS 连接 + 一组 tab URL 模式，互不干扰。
const PLATFORM_REGISTRY = {
  boss: {
    code: "boss",
    name: "BOSS直聘",
    ws_url: "ws://127.0.0.1:8765",
    tab_url_pattern: "https://www.zhipin.com/*",
    tab_url_regex: /^https:\/\/www\.zhipin\.com\//,
    extra_path_regex: /chat|friend|geek|boss|web/,
    download_dir_name: "BOSS直聘",
  },
  qiancheng: {
    code: "qiancheng",
    name: "51前程无忧",
    ws_url: "ws://127.0.0.1:8766",
    tab_url_pattern: "https://ehire.51job.com/*",
    tab_url_regex: /^https:\/\/ehire\.51job\.com\//,
    extra_path_regex: /./, // 阶段 1 宽匹配，阶段 2 学习后收窄
    download_dir_name: "51前程无忧",
  },
  zhilian: {
    code: "zhilian",
    name: "智联招聘",
    ws_url: "ws://127.0.0.1:8767",
    tab_url_pattern: "https://*.zhaopin.com/*",
    tab_url_regex: /^https:\/\/rd[0-9]+\.zhaopin\.com\//,
    extra_path_regex: /./,
    download_dir_name: "智联招聘",
  },
};

const EXTENSION_VERSION = (typeof chrome !== "undefined" && chrome.runtime && typeof chrome.runtime.getManifest === "function")
  ? (chrome.runtime.getManifest().version || "0.0.0")
  : "0.0.0";
const HEARTBEAT_INTERVAL_MS = 15000;

// 每个平台一份连接状态。状态隔离：BOSS 在采集时不影响 51job 闲置标签。
const platforms = {};
for (const key of Object.keys(PLATFORM_REGISTRY)) {
  platforms[key] = {
    cfg: PLATFORM_REGISTRY[key],
    ws: null,
    heartbeatTimer: null,
    reconnectDelay: 1000,
    collectState: "idle",
    activeRunId: "",
    lastDownloadIntent: null,
    downloadIntentQueue: [],
  };
}
const pendingDownloads = new Map(); // download_id -> { ...intent, platform_code }

function platformForUrl(url = "") {
  for (const p of Object.values(platforms)) {
    if (p.cfg.tab_url_regex.test(url) && p.cfg.extra_path_regex.test(url)) return p;
  }
  return null;
}

function connect(platform) {
  if (platform.ws && (platform.ws.readyState === WebSocket.OPEN || platform.ws.readyState === WebSocket.CONNECTING)) return;

  platform.ws = new WebSocket(platform.cfg.ws_url);

  platform.ws.onopen = () => {
    platform.reconnectDelay = 1000;
    sendToServer(platform, { type: "extension_connected", data: { version: EXTENSION_VERSION, downloads_api: true, platform_code: platform.cfg.code } });
    startHeartbeat(platform);
    updateBadge();
    setTimeout(() => sendToContentScript(platform, { type: "probe_page", run_id: platform.activeRunId }), 300);
  };

  platform.ws.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    handleServerCommand(platform, msg);
  };

  platform.ws.onclose = () => {
    stopHeartbeat(platform);
    platform.ws = null;
    updateBadge();
    setTimeout(() => connect(platform), platform.reconnectDelay);
    platform.reconnectDelay = Math.min(platform.reconnectDelay * 2, 30000);
  };

  platform.ws.onerror = () => { platform.ws?.close(); };
}

function handleServerCommand(platform, msg) {
  const { type, config, run_id } = msg;
  if (run_id) platform.activeRunId = run_id;
  switch (type) {
    case "start_collect":
      platform.collectState = "collecting";
      sendToContentScript(platform, { type: "start_collect", config, run_id: platform.activeRunId });
      break;
    case "pause_collect":
      platform.collectState = "paused";
      sendToContentScript(platform, { type: "pause_collect", run_id: platform.activeRunId });
      break;
    case "resume_collect":
      platform.collectState = "collecting";
      sendToContentScript(platform, { type: "resume_collect", run_id: platform.activeRunId });
      break;
    case "stop_collect":
      platform.collectState = "idle";
      sendToContentScript(platform, { type: "stop_collect", run_id: platform.activeRunId });
      break;
    case "reset_content_script":
      sendToContentScript(platform, { type: "reset_content_script", run_id: platform.activeRunId });
      break;
    case "probe_page":
      sendToContentScript(platform, { type: "probe_page", run_id: platform.activeRunId });
      break;
    case "resume_persist_ack":
      sendToContentScript(platform, { type: "resume_persist_ack", data: msg.data || {}, run_id: platform.activeRunId });
      break;
    case "skip_current_candidate":
      sendToContentScript(platform, { type: "skip_current_candidate", data: msg.data || {}, run_id: platform.activeRunId });
      break;
  }
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
  return ["start_collect", "pause_collect", "resume_collect", "stop_collect", "reset_content_script", "resume_persist_ack", "skip_current_candidate"].includes(msg?.type);
}

function pickActiveTab(candidateTabs) {
  return candidateTabs.find((tab) => tab.active && tab.highlighted) || candidateTabs.find((tab) => tab.active) || candidateTabs[0] || null;
}

function sendMessageToTab(platform, tab, msg) {
  chrome.tabs.sendMessage(tab.id, msg, async () => {
    if (!chrome.runtime.lastError) return;
    const injected = await ensureContentScript(tab.id);
    if (!injected) {
      sendToServer(platform, { type: "content_script_inject_failed", data: { tab_id: tab.id, url: tab.url || "" } });
      return;
    }
    chrome.tabs.sendMessage(tab.id, msg, () => {
      if (chrome.runtime.lastError) {
        sendToServer(platform, { type: "content_script_message_failed", data: { tab_id: tab.id, url: tab.url || "", error: chrome.runtime.lastError.message } });
      }
    });
  });
}

function sendToContentScript(platform, msg) {
  chrome.tabs.query({ url: platform.cfg.tab_url_pattern }, async (tabs) => {
    const candidateTabs = tabs.filter((tab) => platform.cfg.tab_url_regex.test(tab.url || "") && platform.cfg.extra_path_regex.test(tab.url || ""));
    if (shouldTargetSingleTab(msg)) {
      const tab = pickActiveTab(candidateTabs);
      if (tab) sendMessageToTab(platform, tab, msg);
      return;
    }
    for (const tab of candidateTabs) {
      sendMessageToTab(platform, tab, msg);
    }
  });
}

function sendToServer(platform, event) {
  if (platform.ws && platform.ws.readyState === WebSocket.OPEN) {
    platform.ws.send(JSON.stringify(event));
  }
}

function startHeartbeat(platform) {
  stopHeartbeat(platform);
  const beat = () => sendToServer(platform, { type: "heartbeat", data: { version: EXTENSION_VERSION, collect_state: platform.collectState, platform_code: platform.cfg.code, at: Date.now() } });
  beat();
  platform.heartbeatTimer = setInterval(beat, HEARTBEAT_INTERVAL_MS);
}

function stopHeartbeat(platform) {
  if (platform.heartbeatTimer) {
    clearInterval(platform.heartbeatTimer);
    platform.heartbeatTimer = null;
  }
}

function updateBadge() {
  const anyConnected = Object.values(platforms).some((p) => p.ws && p.ws.readyState === WebSocket.OPEN);
  chrome.action.setBadgeText({ text: anyConnected ? "" : "!" });
  chrome.action.setBadgeBackgroundColor({ color: anyConnected ? "#4A90E2" : "#999" });
}

function pruneDownloadIntentQueue(platform) {
  const now = Date.now();
  while (platform.downloadIntentQueue.length) {
    const maxAge = platform.downloadIntentQueue[0].click_strategy === "manual_user_click" ? 90000 : 15000;
    if (now - platform.downloadIntentQueue[0].at <= maxAge) break;
    const expired = platform.downloadIntentQueue.shift();
    sendToServer(platform, { type: "download_intent_expired", data: { ...expired, reason: "intent_queue_timeout" } });
  }
}

function rememberDownloadIntent(platform, data) {
  const intent = { ...data, run_id: data.run_id || platform.activeRunId, at: Date.now(), platform_code: platform.cfg.code };
  platform.lastDownloadIntent = intent;
  platform.downloadIntentQueue.push(intent);
  pruneDownloadIntentQueue(platform);
  while (platform.downloadIntentQueue.length > 8) platform.downloadIntentQueue.shift();
  sendToServer(platform, { type: "download_intent_registered", data: intent });
}

function takeQueuedDownloadIntent(platform, item = {}) {
  pruneDownloadIntentQueue(platform);
  if (pendingDownloads.has(item.id)) return null;
  if (!platform.downloadIntentQueue.length) return null;
  const url = item.url || "";
  const exactIndex = platform.downloadIntentQueue.findIndex((intent) => intent.direct_url && url && normalizeDirectDownloadUrl(intent.direct_url) === normalizeDirectDownloadUrl(url));
  const manualIndex = platform.downloadIntentQueue.findIndex((intent) => intent.click_strategy === "manual_user_click" && Date.now() - intent.at <= 90000);
  const index = exactIndex >= 0 ? exactIndex : (manualIndex >= 0 ? manualIndex : 0);
  const [intent] = platform.downloadIntentQueue.splice(index, 1);
  return intent || null;
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
  const platformCode = data.platform_code || "boss";
  const cfg = PLATFORM_REGISTRY[platformCode] || PLATFORM_REGISTRY.boss;
  const dir = cfg.download_dir_name;
  return `${dir}/${name}-${age}-${education}-${dir}-${stamp}${ext}`;
}

function getFreshDownloadIntent(platform, allowManual = false) {
  pruneDownloadIntentQueue(platform);
  if (!platform.lastDownloadIntent) return null;
  const maxAge = allowManual || platform.lastDownloadIntent.click_strategy === "manual_user_click" ? 90000 : 15000;
  if (Date.now() - platform.lastDownloadIntent.at > maxAge) return null;
  return platform.lastDownloadIntent;
}

function notifyDownloadResult(platform, type, data) {
  chrome.tabs.query({ url: platform.cfg.tab_url_pattern }, (tabs) => {
    for (const tab of tabs.filter((tab) => platform.cfg.tab_url_regex.test(tab.url || "") && platform.cfg.extra_path_regex.test(tab.url || ""))) {
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

function downloadDirectUrl(platform, data = {}, sendResponse = () => {}) {
  const rawUrl = data.url || data.direct_url || data.iframe_src || data.src || "";
  const url = normalizeDirectDownloadUrl(rawUrl);
  const baseIntent = { ...data, url, run_id: data.run_id || platform.activeRunId, at: Date.now(), direct_url: url, raw_direct_url: rawUrl, platform_code: platform.cfg.code };
  sendToServer(platform, { type: "direct_download_request_received", data: { ...baseIntent, url_valid: Boolean(url && /^https:\/\//i.test(url)) } });

  let responded = false;
  const respondOnce = (response) => {
    if (responded) return;
    responded = true;
    clearTimeout(responseTimer);
    sendToServer(platform, { type: "direct_download_response_sent", data: { ...baseIntent, ...response } });
    try { sendResponse(response); } catch (error) {
      sendToServer(platform, { type: "direct_download_response_error", data: { ...baseIntent, reason: String(error) } });
    }
  };
  const responseTimer = setTimeout(() => {
    const reason = "chrome_download_callback_timeout";
    sendToServer(platform, { type: "direct_download_callback_timeout", data: { ...baseIntent, reason } });
    notifyDownloadResult(platform, "download_failed", { ...baseIntent, reason });
    respondOnce({ ok: false, reason });
  }, 8000);

  if (!url || !/^https:\/\//i.test(url)) {
    respondOnce({ ok: false, reason: "invalid_download_url", raw_url: rawUrl });
    return;
  }

  const intent = { ...baseIntent, download_source: "direct_iframe", direct_bound: true };
  platform.lastDownloadIntent = intent;
  sendToServer(platform, { type: "direct_download_starting", data: intent });
  try {
    chrome.downloads.download({ url, filename: makeSafeDownloadFilename(intent), conflictAction: "uniquify", saveAs: false }, (downloadId) => {
      if (chrome.runtime.lastError || !downloadId) {
        const reason = chrome.runtime.lastError?.message || "download_start_failed";
        sendToServer(platform, { type: "direct_download_failed", data: { ...intent, reason } });
        notifyDownloadResult(platform, "download_failed", { ...intent, reason });
        respondOnce({ ok: false, reason });
        return;
      }
      pendingDownloads.set(downloadId, { ...intent, download_id: downloadId, started_at: Date.now(), bound_by: "direct_download_callback" });
      sendToServer(platform, { type: "download_created", data: { ...intent, download_id: downloadId, filename: url, bound_by: "direct_download_callback" } });
      respondOnce({ ok: true, download_id: downloadId, download_request_id: intent.download_request_id || "" });
    });
  } catch (error) {
    const reason = String(error);
    sendToServer(platform, { type: "direct_download_failed", data: { ...intent, reason } });
    notifyDownloadResult(platform, "download_failed", { ...intent, reason });
    respondOnce({ ok: false, reason });
  }
}

chrome.downloads.onCreated.addListener((item) => {
  if (pendingDownloads.has(item.id)) {
    const pending = pendingDownloads.get(item.id);
    const platform = platforms[pending.platform_code] || platforms.boss;
    sendToServer(platform, {
      type: "download_created_seen_bound",
      data: { ...pending, download_id: item.id, filename: item.filename || item.url || "", bound_by: pending.bound_by || "existing" },
    });
    return;
  }
  // 未绑定下载：扫描所有平台的 intent 队列找匹配
  for (const platform of Object.values(platforms)) {
    const intent = takeQueuedDownloadIntent(platform, item);
    if (!intent) continue;
    pendingDownloads.set(item.id, { ...intent, download_id: item.id, started_at: Date.now(), bound_by: "downloads_on_created_queue" });
    sendToServer(platform, {
      type: "download_created",
      data: { ...intent, download_id: item.id, filename: item.filename || item.url || "", bound_by: "downloads_on_created_queue" },
    });
    return;
  }
  // 仍未匹配：通告给所有连接的平台（便于排查）
  for (const platform of Object.values(platforms)) {
    if (platform.ws && platform.ws.readyState === WebSocket.OPEN) {
      sendToServer(platform, { type: "download_created_unbound", data: { download_id: item.id, filename: item.filename || item.url || "", reason: "no_matching_intent" } });
    }
  }
});

chrome.downloads.onChanged.addListener((delta) => {
  const pending = pendingDownloads.get(delta.id);
  if (!pending) return;
  const platform = platforms[pending.platform_code] || platforms.boss;

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
      sendToServer(platform, { type: "resume_downloaded", data });
      notifyDownloadResult(platform, "download_completed", data);
    });
  } else if (delta.error?.current) {
    pendingDownloads.delete(delta.id);
    const data = { ...pending, reason: `download_error:${delta.error.current}` };
    sendToServer(platform, { type: "candidate_skipped", data });
    notifyDownloadResult(platform, "download_failed", data);
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // 根据消息来源 tab 的 URL 路由到对应平台。sender.tab.url 在 content.js 消息中可用。
  const senderUrl = sender?.tab?.url || "";
  const platform = platformForUrl(senderUrl) || platforms.boss;

  if (msg.target === "background") {
    if (msg.event?.type === "download_intent") {
      rememberDownloadIntent(platform, msg.event.data || {});
    } else {
      sendToServer(platform, msg.event);
    }
    sendResponse({ ok: true });
  } else if (msg.type === "download_direct_url") {
    downloadDirectUrl(platform, msg.data || {}, sendResponse);
    return true;
  } else if (msg.type === "get_state") {
    sendResponse({
      connected: Object.values(platforms).some((p) => p.ws?.readyState === WebSocket.OPEN),
      platform_states: Object.fromEntries(Object.entries(platforms).map(([code, p]) => [code, { connected: p.ws?.readyState === WebSocket.OPEN, collectState: p.collectState }])),
    });
  }
  return true;
});

// 启动所有平台的 WS 连接
for (const platform of Object.values(platforms)) {
  connect(platform);
}
