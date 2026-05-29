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

// 采集开始前的浏览器设置预检：
// - 弹窗（popups）：rd5/rd6/ehire/zhipin 域必须为 allow，否则附件简历的弹出窗口会被吃掉。
//   chrome.contentSettings.popups.get 会真实读到 chrome://settings/content/popups 的状态。
// - 下载前询问：Chrome 没有给扩展开放 prompt_for_download 这个 pref 的读接口；这里只能放过，
//   留给"首次下载兑底探测"在采集开始后兜底报错（见 download_prompt_suspected）。
async function runStartCollectPrechecks(platform, _config) {
  const probeUrl = ({
    boss: "https://www.zhipin.com/",
    qiancheng: "https://ehire.51job.com/",
    zhilian: "https://rd5.zhaopin.com/",
  })[platform.cfg.code] || "https://www.zhipin.com/";

  if (!chrome.contentSettings || !chrome.contentSettings.popups) {
    return { ok: false, reason: "content_settings_api_unavailable", probe_url: probeUrl };
  }
  try {
    const result = await new Promise((resolve, reject) => {
      chrome.contentSettings.popups.get({ primaryUrl: probeUrl }, (details) => {
        if (chrome.runtime.lastError) reject(chrome.runtime.lastError);
        else resolve(details || {});
      });
    });
    const setting = result.setting || "";
    if (setting !== "allow") {
      return {
        ok: false,
        reason: "popups_not_allowed",
        popups_setting: setting || "unknown",
        probe_url: probeUrl,
        hint: "请打开 chrome://settings/content/popups，将 \"网站可以发送弹出式窗口并使用重定向\" 设为允许（或为该招聘域名加白名单）后重试。",
      };
    }
    return { ok: true, popups_setting: setting, probe_url: probeUrl };
  } catch (error) {
    return {
      ok: false,
      reason: "popups_probe_error",
      error: String(error?.message || error),
      probe_url: probeUrl,
    };
  }
}

// 首次下载兑底探测：内容脚本拿到附件链接后会经 download_intent / download_direct_url 触发
// chrome.downloads.download。Chrome 端如果勾着"下载前询问每个文件的保存位置"，会先弹一个
// 保存对话框、阻塞 download_created 事件——此时 5 秒内 platform.firstDownloadSeen 不会被翻为
// true，我们就上报 download_prompt_suspected，让 bridge 用深红日志提醒用户去关掉该设置。
function armFirstDownloadProbe(platform) {
  platform.firstDownloadSeen = false;
  if (platform.firstDownloadSuspectedTimer) {
    clearTimeout(platform.firstDownloadSuspectedTimer);
    platform.firstDownloadSuspectedTimer = null;
  }
  platform.firstDownloadIntentAt = 0;
}

function markFirstDownloadIntentSent(platform) {
  if (platform.firstDownloadSeen || platform.firstDownloadIntentAt) return;
  platform.firstDownloadIntentAt = Date.now();
  platform.firstDownloadSuspectedTimer = setTimeout(() => {
    if (platform.firstDownloadSeen) return;
    sendToServer(platform, {
      type: "download_prompt_suspected",
      data: {
        run_id: platform.activeRunId,
        waited_ms: Date.now() - platform.firstDownloadIntentAt,
        hint: "首次下载已发起 5 秒仍未落盘，疑似 Chrome 勾选了\"下载前询问每个文件的保存位置\"。请到 chrome://settings/downloads 关闭后重试。",
      },
    });
  }, 5000);
}

function markFirstDownloadSeen(platform) {
  if (platform.firstDownloadSeen) return;
  platform.firstDownloadSeen = true;
  if (platform.firstDownloadSuspectedTimer) {
    clearTimeout(platform.firstDownloadSuspectedTimer);
    platform.firstDownloadSuspectedTimer = null;
  }
}

function handleServerCommand(platform, msg) {
  const { type, config, run_id } = msg;
  if (run_id) platform.activeRunId = run_id;
  switch (type) {
    case "start_collect":
      runStartCollectPrechecks(platform, config).then((result) => {
        if (!result.ok) {
          sendToServer(platform, {
            type: "settings_precheck_failed",
            data: { run_id: platform.activeRunId, ...result },
          });
          platform.collectState = "idle";
          return;
        }
        platform.collectState = "collecting";
        armFirstDownloadProbe(platform);
        sendToContentScript(platform, { type: "start_collect", config, run_id: platform.activeRunId });
      });
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
  markFirstDownloadIntentSent(platform);
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
      markFirstDownloadSeen(platform);
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

// 智联下载 URL 识别（参考老 Playwright adapter._is_resume_attachment_download_url）
const ZHILIAN_DOWNLOAD_URL_TOKENS = [
  "downloadfiletemporary", "downloadfile", "downfile", "downloadresume",
  "resume/download", "resumeattachment", "attachment/download",
  "download/attachment", "file/download", "downloadurl",
];
function isZhilianDownloadUrl(rawUrl) {
  if (!rawUrl) return false;
  let url;
  try { url = new URL(rawUrl); } catch { return false; }
  if (url.protocol !== "https:" && url.protocol !== "http:") return false;
  const host = url.host.toLowerCase();
  const pq = (url.pathname + url.search).toLowerCase();
  if (host.includes("attachment.zhaopin.com") && pq.includes("downloadfiletemporary")) return true;
  if (!host.includes("zhaopin.com")) return false;
  for (const t of ZHILIAN_DOWNLOAD_URL_TOKENS) { if (pq.includes(t)) return true; }
  return false;
}

const handledZhilianTabs = new Set(); // 已捕获的 tabId，防重复处理

// 智联"查看附件简历"按钮 → window.open 弹出 attachment.zhaopin.com 的 PDF 直链 tab。
// 服务器返回的是 PDF inline（Content-Type: application/pdf，无 Content-Disposition: attachment），
// 所以浏览器只会内嵌渲染，不会自动触发下载，onDeterminingFilename 也不会 fire。
// 解决：URL 命中 → 主动调 chrome.downloads.download({url, filename}) 强制下载 → 关闭弹窗 tab。
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  const url = changeInfo.url || tab.url || "";
  if (!url || !isZhilianDownloadUrl(url)) return;
  if (handledZhilianTabs.has(tabId)) return;
  handledZhilianTabs.add(tabId);
  setTimeout(() => handledZhilianTabs.delete(tabId), 60000);
  const platform = platforms.zhilian;
  if (!platform) return;

  sendToServer(platform, { type: "zhilian_attachment_tab_captured", data: { tab_id: tabId, url } });

  pruneDownloadIntentQueue(platform);
  const intent = platform.downloadIntentQueue.shift() || platform.lastDownloadIntent;
  if (!intent) {
    sendToServer(platform, { type: "zhilian_download_no_intent", data: { tab_id: tabId, url } });
    setTimeout(() => { chrome.tabs.remove(tabId).catch(() => {}); }, 1500);
    return;
  }

  const boundIntent = { ...intent, url, direct_url: url, at: Date.now(), platform_code: "zhilian", download_source: "zhilian_active_download" };
  markFirstDownloadIntentSent(platform);
  sendToServer(platform, { type: "zhilian_active_download_starting", data: boundIntent });

  try {
    chrome.downloads.download({
      url,
      filename: makeSafeDownloadFilename(boundIntent),
      conflictAction: "uniquify",
      saveAs: false,
    }, (downloadId) => {
      if (chrome.runtime.lastError || !downloadId) {
        const reason = chrome.runtime.lastError?.message || "download_start_failed";
        sendToServer(platform, { type: "zhilian_active_download_failed", data: { ...boundIntent, reason } });
        notifyDownloadResult(platform, "download_failed", { ...boundIntent, reason });
        setTimeout(() => { chrome.tabs.remove(tabId).catch(() => {}); }, 500);
        return;
      }
      pendingDownloads.set(downloadId, { ...boundIntent, download_id: downloadId, started_at: Date.now(), bound_by: "zhilian_active_download" });
      markFirstDownloadSeen(platform);
      sendToServer(platform, { type: "download_created", data: { ...boundIntent, download_id: downloadId, filename: url, bound_by: "zhilian_active_download" } });
      setTimeout(() => { chrome.tabs.remove(tabId).catch(() => {}); }, 500);
    });
  } catch (error) {
    const reason = String(error);
    sendToServer(platform, { type: "zhilian_active_download_failed", data: { ...boundIntent, reason } });
    notifyDownloadResult(platform, "download_failed", { ...boundIntent, reason });
    setTimeout(() => { chrome.tabs.remove(tabId).catch(() => {}); }, 500);
  }
});

chrome.downloads.onCreated.addListener((item) => {
  if (pendingDownloads.has(item.id)) {
    const pending = pendingDownloads.get(item.id);
    const platform = platforms[pending.platform_code] || platforms.boss;
    markFirstDownloadSeen(platform);
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
    markFirstDownloadSeen(platform);
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
  } else if (msg.type === "extract_vue_data") {
    const tabId = sender?.tab?.id;
    if (!tabId) { sendResponse({ ok: false, error: "no_tab" }); return true; }
    chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      args: [msg.marker],
      func: (marker) => {
        function _ex(raw) {
          if (!raw) return "";
          if (/^https?:\/\//i.test(raw)) return raw;
          if (/^bosszp:\/\//i.test(raw)) {
            try {
              const q = raw.indexOf("?");
              if (q < 0) return "";
              const p = new URLSearchParams(raw.slice(q + 1));
              const u = p.get("url");
              if (u && /^https?:\/\//i.test(u)) return u;
            } catch (e) {}
          }
          return "";
        }
        const card = document.querySelector('[data-works-extract="' + marker + '"]');
        if (!card) return null;
        card.removeAttribute("data-works-extract");
        let vm = null;
        for (let el = card; el && !vm; el = el.parentElement) { vm = el.__vue__; }
        if (!vm) return null;
        for (let cur = vm; cur; cur = cur.$parent) {
          const msg = cur.message || (cur.$props && cur.$props.message) || (cur.$data && cur.$data.message);
          if (!msg) continue;
          const hl = msg.body && msg.body.hyperLink;
          if (!hl) continue;
          let url = _ex(hl.url || hl.hyperLinkUrl || "");
          if (!url) {
            try {
              const extra = typeof hl.extraJson === "string" ? JSON.parse(hl.extraJson) : hl.extraJson;
              url = _ex((extra && extra.resumeNewUrl) || "") || _ex((extra && extra.resumePreviewH5Url) || "");
            } catch (e) {}
          }
          if (!url) continue;
          return { url, filename: msg.text || "" };
        }
        return null;
      },
    }).then((results) => {
      const val = results?.[0]?.result || null;
      sendResponse({ ok: true, data: val });
    }).catch((err) => {
      sendResponse({ ok: false, error: String(err) });
    });
    return true;
  }
  return true;
});

// 启动所有平台的 WS 连接
for (const platform of Object.values(platforms)) {
  connect(platform);
}
