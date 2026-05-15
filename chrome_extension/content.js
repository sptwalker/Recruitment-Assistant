(function () {
  "use strict";

  const CONTENT_SCRIPT_VERSION = "1.44.0";
  if (window.__bossResumeCollectorVersion === CONTENT_SCRIPT_VERSION) {
    return;
  }
  const INSTANCE_ID = `${CONTENT_SCRIPT_VERSION}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
  window.__bossResumeCollectorVersion = CONTENT_SCRIPT_VERSION;
  window.__bossResumeCollectorActiveInstance = INSTANCE_ID;

  function isActiveInstance() {
    return window.__bossResumeCollectorActiveInstance === INSTANCE_ID;
  }

  const CANDIDATE_SELECTORS = [
    ".chat-list li",
    ".chat-list .item",
    ".user-list li",
    ".friend-list li",
    "[class*='chat-list'] li",
    "[class*='chat-list'] [class*='item']",
    "[class*='friend-list'] li",
    "[class*='friend-list'] [class*='item']",
    "[class*='user-list'] li",
    "[class*='user-list'] [class*='item']",
  ];

  const LIST_CONTAINER_SELECTORS = [
    ".chat-list",
    ".user-list",
    ".friend-list",
    "[class*='chat-list']",
    "[class*='friend-list']",
    "[class*='user-list']",
    "[class*='conversation']",
  ];

  const DETAIL_SELECTORS = [
    "[class*='chat'] [class*='header']",
    "[class*='chat'] [class*='top']",
    "[class*='chat'] [class*='detail']",
    "[class*='geek'] [class*='info']",
    "[class*='user'] [class*='info']",
    ".name-box",
    ".base-info",
  ];

  const AUTH_MARKERS = ["沟通中", "新招呼", "联系人", "附件简历", "牛人", "聊天", "常用语", "发送", "交换微信", "打招呼"];
  const PAGE_MARKERS = ["沟通", "聊天", "联系人", "牛人", "BOSS", "直聘", "发送", "常用语", "附件简历"];
  const RESUME_VIEW_TEXT = ["查看附件简历", "查看简历附件", "下载附件简历", "下载简历附件"];
  const RESUME_REQUEST_TEXT = ["要附件简历", "索要附件简历", "获取附件简历"];
  const RESUME_REQUESTED_TEXT = ["已向对方要附件简历", "已索要附件简历", "等待对方上传", "简历请求已发送"];


  let state = "idle";
  let config = { max_resumes: 5, interval_ms: 5000, request_resume_if_missing: false };
  let results = { downloaded: 0, skipped: 0, currentIndex: 0 };
  let pauseResolve = null;
  let activeRunId = "";
  let activeCollectLoopRunId = "";
  let lastResumeAttachmentClick = { key: "", at: 0 };
  let collectFinishedEmitted = false;
  const pendingDownloadWaiters = new Map();
  const STORAGE_KEYS = {
    learningStage: "boss_resume_learning_stage",
    learnedClick: "boss_resume_download_learned_click",
  };
  const resumePreviewLearnState = {
    learningStage: localStorage.getItem(STORAGE_KEYS.learningStage) || "detect_preview",
    waitingManualClick: false,
    learnedClick: null,
  };
  try {
    const savedClick = localStorage.getItem(STORAGE_KEYS.learnedClick);
    if (savedClick) resumePreviewLearnState.learnedClick = JSON.parse(savedClick);
  } catch {
    resumePreviewLearnState.learnedClick = null;
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  function textOf(el) {
    return `${el?.innerText || ""} ${el?.getAttribute?.("title") || ""} ${el?.getAttribute?.("aria-label") || ""}`.replace(/\s+/g, " ").trim();
  }

  function emit(event) {
    const payload = { ...event, data: { ...(event.data || {}), run_id: activeRunId } };
    try {
      chrome.runtime.sendMessage({ target: "background", event: payload }, () => {
        void chrome.runtime.lastError;
      });
    } catch {}
  }

  function emitCritical(event) {
    emit(event);
  }

  function emitUiStage(message, data = {}) {
    emitCritical({ type: "boss_ui_stage", data: { message, ...data } });
  }

  function emitAttachmentDebug(stage, candidateId = "", signature = "", details = {}) {
    void stage;
    void candidateId;
    void signature;
    void details;
  }

  function isAuthenticated() {
    const text = document.body?.innerText || "";
    return AUTH_MARKERS.filter((m) => text.includes(m)).length >= 2;
  }

  function isBossPageDetected() {
    const text = `${document.title || ""} ${document.body?.innerText || ""}`;
    return location.hostname === "www.zhipin.com" && PAGE_MARKERS.some((m) => text.includes(m));
  }

  function isVisible(el) {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = getComputedStyle(el);
    return style.visibility !== "hidden" && style.display !== "none" && parseFloat(style.opacity || "1") >= 0.2;
  }

  function isDisabled(el) {
    const style = getComputedStyle(el);
    return Boolean(
      el.disabled ||
      el.getAttribute("aria-disabled") === "true" ||
      (el.className || "").toString().includes("disabled") ||
      style.pointerEvents === "none" ||
      parseFloat(style.opacity || "1") < 0.55
    );
  }

  function visibleCenter(el) {
    const rect = el.getBoundingClientRect();
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, rect };
  }

  function isInLeftCandidateArea(el) {
    const { rect } = visibleCenter(el);
    const maxLeft = Math.min(window.innerWidth * 0.45, 520);
    return rect.left < maxLeft && rect.top > 60 && rect.height >= 28;
  }

  function scoreCandidateItem(el) {
    if (!isVisible(el) || !isInLeftCandidateArea(el)) return -1;
    const text = textOf(el);
    if (text.length < 2 || text.length > 180) return -1;
    if (/职位管理|推荐牛人|搜索|牛人管理|道具|工具箱|招聘数据|账号权益|我的客服|附件简历|下载|发送|表情|请输入|系统消息/.test(text)) return -1;
    let score = 0;
    if (/\d{2}\s*岁/.test(text)) score += 4;
    if (/本科|大专|硕士|博士|研究生|专科|高中|中专/.test(text)) score += 3;
    if (/在线|沟通|新招呼|今日|昨天|\d{1,2}:\d{2}/.test(text)) score += 1;
    if (el.matches("li, [class*='item'], [class*='card']")) score += 1;
    return score;
  }

  function candidateKeyFromText(text) {
    const info = parseContactText(text);
    if (info && (info.name !== "待识别" || info.age !== "待识别" || info.education !== "待识别")) {
      return `${info.name}/${info.age}/${info.education}`;
    }
    return stripActivityText(text).replace(/\s+/g, " ").slice(0, 80);
  }

  function findBestListContainer() {
    return LIST_CONTAINER_SELECTORS
      .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
      .filter((el) => isVisible(el) && isInLeftCandidateArea(el))
      .map((el) => ({ el, score: el.scrollHeight - el.clientHeight + el.querySelectorAll("li, [class*='item'], [class*='card'], [class*='user']").length * 20 }))
      .sort((a, b) => b.score - a.score)[0]?.el || null;
  }

  async function resetCandidateListScroll() {
    const container = findBestListContainer();
    if (container) {
      container.scrollTop = 0;
      container.dispatchEvent(new Event("scroll", { bubbles: true }));
    }
    window.scrollTo(0, 0);
    await sleep(800);
  }

  function getCandidateItems() {
    const seen = new Set();
    const seenKeys = new Set();
    const items = [];
    const containers = LIST_CONTAINER_SELECTORS
      .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
      .filter((el) => isVisible(el) && isInLeftCandidateArea(el));

    for (const container of containers) {
      const nodes = container.querySelectorAll("li, [class*='item'], [class*='card'], [class*='user']");
      for (const el of nodes) {
        if (seen.has(el)) continue;
        seen.add(el);
        const score = scoreCandidateItem(el);
        if (score < 2) continue;
        const key = candidateKeyFromText(textOf(el));
        if (!key || seenKeys.has(key)) continue;
        seenKeys.add(key);
        items.push({ el, score, top: el.getBoundingClientRect().top });
      }
      if (items.length > 0) break;
    }

    if (items.length === 0) {
      for (const selector of CANDIDATE_SELECTORS) {
        for (const el of document.querySelectorAll(selector)) {
          if (seen.has(el)) continue;
          seen.add(el);
          const score = scoreCandidateItem(el);
          if (score < 2) continue;
          const key = candidateKeyFromText(textOf(el));
          if (!key || seenKeys.has(key)) continue;
          seenKeys.add(key);
          items.push({ el, score, top: el.getBoundingClientRect().top });
        }
        if (items.length > 0) break;
      }
    }

    return items.sort((a, b) => a.top - b.top || b.score - a.score).map((x) => x.el);
  }

  function stripCandidateUiText(text = "") {
    return (text || "")
      .replace(/查看附件简历|查看简历附件|下载附件简历|下载简历附件|查看简历|下载简历|下简历|附件简历|简历附件/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function stripActivityText(text) {
    return stripCandidateUiText(text)
      .replace(/刚刚活跃|今日活跃|昨日活跃|活跃|在线|分钟前活跃|\d+分钟前活跃/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function isInvalidCandidateNameToken(value = "") {
    const baseValue = value.replace(/先生|女士/g, "");
    const blacklist = new Set([
      "沟通", "在线", "附件", "附件简历", "简历附件", "交换微信", "常用语", "招聘者", "职位管理", "推荐牛人", "搜索",
      "刚刚", "刚刚活跃", "今日", "今日活跃", "昨日", "昨日活跃", "活跃", "下载", "查看", "简历", "下载简历", "查看简历", "下简历",
    ]);
    return !baseValue || blacklist.has(value) || blacklist.has(baseValue) || /简历|下载|附件|查看|沟通|职位|推荐|搜索|客服|工具/.test(baseValue);
  }

  function parseContactText(text) {
    const clean = stripActivityText(text);
    if (!clean || /职位管理|推荐牛人|牛人管理|工具箱|招聘规范|我的客服/.test(clean)) return null;

    const ageMatch = clean.match(/(\d{2})\s*岁/);
    const eduMatch = clean.match(/博士|硕士|研究生|本科|大专|专科|高中|中专/);
    if (!ageMatch && !eduMatch) return null;

    let name = "待识别";
    const beforeAge = ageMatch ? clean.slice(0, ageMatch.index).trim() : clean.slice(0, 40).trim();
    const nameMatches = Array.from(beforeAge.matchAll(/[\u4e00-\u9fa5]{2,4}(?:先生|女士)?/g)).map((m) => m[0]);
    for (let i = nameMatches.length - 1; i >= 0; i--) {
      const value = nameMatches[i];
      if (!isInvalidCandidateNameToken(value)) {
        name = value;
        break;
      }
    }

    let education = eduMatch ? eduMatch[0] : "待识别";
    if (education === "研究生") education = "硕士";
    if (education === "专科") education = "大专";

    return { name, age: ageMatch ? `${ageMatch[1]}岁` : "待识别", education, raw_text: clean.slice(0, 160) };
  }

  function extractContactInfo(clickedItem = null) {
    const detailNodes = DETAIL_SELECTORS.flatMap((selector) => Array.from(document.querySelectorAll(selector)));
    const scored = [];
    for (const el of detailNodes) {
      if (!isVisible(el)) continue;
      const rect = el.getBoundingClientRect();
      if (rect.left < window.innerWidth * 0.25 || rect.top < 55 || rect.top > window.innerHeight * 0.55) continue;
      const info = parseContactText(textOf(el));
      if (!info) continue;
      let score = 0;
      if (info.name !== "待识别") score += 3;
      if (info.age !== "待识别") score += 3;
      if (info.education !== "待识别") score += 3;
      scored.push({ info, score, top: rect.top, len: textOf(el).length });
    }

    scored.sort((a, b) => b.score - a.score || a.top - b.top || a.len - b.len);
    if (scored[0] && scored[0].info.name !== "待识别") return scored[0].info;

    if (clickedItem) {
      const itemInfo = parseContactText(textOf(clickedItem));
      if (itemInfo && itemInfo.name !== "待识别") return itemInfo;
    }

    const bodyLines = (document.body?.innerText || "").split("\n").map((x) => x.trim()).filter(Boolean);
    for (const line of bodyLines.slice(0, 80)) {
      const info = parseContactText(line);
      if (info && (info.name !== "待识别" || info.age !== "待识别" || info.education !== "待识别")) return info;
    }

    return { name: "待识别", age: "待识别", education: "待识别", raw_text: "" };
  }

  function hasResumeRequestSent(scope = document) {
    const text = scope?.innerText || scope?.textContent || "";
    return RESUME_REQUESTED_TEXT.some((k) => text.includes(k));
  }

  function getChatDetailRoot() {
    const candidates = Array.from(document.querySelectorAll("[class*='chat'], [class*='dialog'], [class*='conversation'], [class*='message'], [class*='content']"))
      .filter((el) => isVisible(el))
      .map((el) => ({ el, rect: el.getBoundingClientRect(), text: textOf(el) }))
      .filter(({ rect, text }) => rect.left > window.innerWidth * 0.25 && rect.width > 240 && text.length > 20)
      .sort((a, b) => b.rect.width * b.rect.height - a.rect.width * a.rect.height);
    return candidates[0]?.el || document.body;
  }

  function getResumeRequestSentCount() {
    const text = getChatDetailRoot()?.innerText || "";
    return RESUME_REQUESTED_TEXT.reduce((sum, k) => sum + (text.split(k).length - 1), 0);
  }

  function classifyResumeButtonText(text) {
    if (RESUME_VIEW_TEXT.some((k) => text.includes(k))) return "view";
    if (RESUME_REQUESTED_TEXT.some((k) => text.includes(k))) return "requested";
    if (RESUME_REQUEST_TEXT.some((k) => text.includes(k))) return "request";
    if (text.includes("附件简历")) return "unknown_resume";
    return "none";
  }

  function getClickableResumeElement(el) {
    const rect = el.getBoundingClientRect();
    let node = el;
    while (node && node !== document.body) {
      if (node.matches?.("button, a, [role='button'], [class*='btn'], [class*='resume']")) return node;
      const parent = node.parentElement;
      if (!parent) break;
      const pRect = parent.getBoundingClientRect();
      if (pRect.width > 220 || pRect.height > 90 || pRect.left < window.innerWidth * 0.4) break;
      node = parent;
    }
    return document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2) || el;
  }

  function findResumeButton() {
    const nodes = document.querySelectorAll("button, a, [role='button'], span, i");
    const matches = [];
    for (const el of nodes) {
      const text = textOf(el);
      const stateName = classifyResumeButtonText(text);
      if (stateName === "none") continue;
      if (!isVisible(el)) continue;
      const rect = el.getBoundingClientRect();
      if (rect.top < 60 || rect.left < window.innerWidth * 0.45) continue;
      if (text.length > 40 || /职位管理|推荐牛人|搜索|沟通|牛人管理|工具箱|招聘数据|账号权益|我的客服/.test(text)) continue;
      if (stateName === "requested" && text === "简历请求已发送") continue;
      const clickable = getClickableResumeElement(el);
      if (!isVisible(clickable)) continue;
      matches.push({ el: clickable, text, state: stateName, enabled: stateName === "unknown_resume" || (!isDisabled(clickable) && !isDisabled(el)), left: rect.left, top: rect.top });
    }
    const priority = { view: 1, request: 2, unknown_resume: 3, requested: 4 };
    matches.sort((a, b) => priority[a.state] - priority[b.state] || b.left - a.left || a.top - b.top);
    return matches[0] || null;
  }

  async function waitForResumeRequestSent(timeoutMs = 1800, previousCount = null) {
    const baseline = previousCount ?? getResumeRequestSentCount();
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const current = getResumeRequestSentCount();
      if (current > baseline) return true;
      await sleep(200);
    }
    return false;
  }

  function clickElementOnce(el) {
    if (!el) return false;
    el.scrollIntoView?.({ block: "center", inline: "center" });
    const rect = el.getBoundingClientRect();
    const x = Math.max(1, Math.min(window.innerWidth - 1, rect.left + rect.width / 2));
    const y = Math.max(1, Math.min(window.innerHeight - 1, rect.top + rect.height / 2));
    const target = document.elementFromPoint(x, y) || el;
    const node = target.closest?.("button, a, [role='button'], [class*='btn'], [class*='icon'], [class*='download'], [class*='resume']") || target || el;
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      node.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, view: window }));
    }
    return true;
  }

  function clickElementReliably(el) {
    return clickElementOnce(el);
  }

  function guardResumeAttachmentClick(candidateId, signature) {
    const key = `${activeRunId}|${candidateId}|${signature}`;
    const now = Date.now();
    if (lastResumeAttachmentClick.key === key && now - lastResumeAttachmentClick.at < 5000) return false;
    lastResumeAttachmentClick = { key, at: now };
    return true;
  }

  function clickPointReliably(x, y) {
    const target = document.elementFromPoint(x, y);
    if (!target) return false;
    return clickElementReliably(target);
  }

  async function confirmRequestIfNeeded(candidateId = "", signature = "") {
    await sleep(700);
    const dialogs = Array.from(document.querySelectorAll("[class*='dialog'], [class*='modal'], [role='dialog'], [class*='pop'], [class*='confirm'], [class*='layer']"))
      .filter((el) => isVisible(el))
      .map((el) => ({ el, rect: el.getBoundingClientRect(), text: textOf(el) }))
      .filter(({ text }) => /附件|简历|索要|发送|确认|确定/.test(text));
    const roots = dialogs.length ? dialogs.map((x) => x.el) : [document.body];
    const candidates = [];
    for (const root of roots) {
      const nodes = root.querySelectorAll("button, a, [role='button'], div, span");
      for (const el of nodes) {
        const text = textOf(el);
        if (!/确定|确认|发送|索要/.test(text)) continue;
        if (!isVisible(el) || isDisabled(el)) continue;
        const rect = el.getBoundingClientRect();
        if (rect.top < 60 || rect.width < 20 || rect.height < 18) continue;
        const areaText = textOf(el.closest("[class*='dialog'], [class*='modal'], [role='dialog'], [class*='pop'], [class*='confirm'], [class*='layer']") || root);
        let score = 0;
        if (/附件|简历|索要/.test(areaText)) score += 6;
        if (/确认|确定/.test(text)) score += 4;
        if (/发送|索要/.test(text)) score += 2;
        if (rect.left > window.innerWidth * 0.35 && rect.top > 80) score += 1;
        candidates.push({ el: el.closest("button, a, [role='button']") || el, score, text, left: rect.left, top: rect.top });
      }
    }
    candidates.sort((a, b) => b.score - a.score || b.left - a.left || b.top - a.top);
    if (candidates[0]) {
      clickElementReliably(candidates[0].el);
      emit({ type: "resume_request_confirm_clicked", data: { candidate_id: candidateId, candidate_signature: signature, button_text: candidates[0].text } });
      return true;
    }
    emit({ type: "resume_request_confirm_not_found", data: { candidate_id: candidateId, candidate_signature: signature } });
    return false;
  }

  function getElementDescriptor(el) {
    if (!el) return "";
    const attrs = ["class", "id", "href", "xlink:href", "aria-label", "title", "data-icon", "data-name", "data-testid"];
    const attrText = attrs.map((name) => el.getAttribute?.(name) || "").join(" ");
    return `${textOf(el)} ${attrText} ${el.className || ""}`.replace(/\s+/g, " ").trim();
  }

  function getElementDomPath(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return "";
    const parts = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE && node !== document.body) {
      let part = node.tagName.toLowerCase();
      const id = node.getAttribute("id");
      if (id && !/\s/.test(id)) {
        part += `#${CSS.escape(id)}`;
        parts.unshift(part);
        break;
      }
      const cls = Array.from(node.classList || []).filter(Boolean).slice(0, 2);
      if (cls.length) part += cls.map((x) => `.${CSS.escape(x)}`).join("");
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((x) => x.tagName === node.tagName);
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.slice(-8).join(" > ");
  }

  function getElementSnapshot(el, x = null, y = null) {
    const node = el?.closest?.("button, a, [role='button'], [class*='btn'], [class*='icon'], [class*='download'], [class*='toolbar'] span, [class*='toolbar'] div") || el;
    const rect = node?.getBoundingClientRect?.();
    return {
      x,
      y,
      relative_x: x == null ? null : x / Math.max(1, window.innerWidth),
      relative_y: y == null ? null : y / Math.max(1, window.innerHeight),
      window_width: window.innerWidth,
      window_height: window.innerHeight,
      tag: node?.tagName || "",
      id: node?.getAttribute?.("id") || "",
      class_name: `${node?.className || ""}`,
      title: node?.getAttribute?.("title") || "",
      aria_label: node?.getAttribute?.("aria-label") || "",
      descriptor: getElementDescriptor(node).slice(0, 260),
      path: getElementDomPath(node),
      rect: rect ? { left: rect.left, top: rect.top, width: rect.width, height: rect.height } : null,
    };
  }

  function isStrongBossPdfPreviewUrl(src = "") {
    return /pdf-viewer-b|bzl-office\/pdf-viewer|preview4boss|wflow\/zpgeek\/download\/preview4boss|\.pdf(?:$|[?#])/i.test(src);
  }

  function normalizeBossUrl(url = "", base = location.origin) {
    const raw = `${url || ""}`.trim();
    if (!raw) return "";
    try {
      return new URL(raw, base || location.origin).href;
    } catch {
      return "";
    }
  }

  function parseBossPdfViewerUrl(src = "") {
    const viewer_url = normalizeBossUrl(src);
    if (!viewer_url) return { viewer_url: "", extracted_url: "", direct_url: "", direct_source: "" };
    let extracted_url = "";
    try {
      const parsed = new URL(viewer_url);
      const inner = parsed.searchParams.get("url") || parsed.searchParams.get("file") || parsed.searchParams.get("src") || "";
      extracted_url = inner ? normalizeBossUrl(inner, parsed.origin) : "";
    } catch {
      extracted_url = "";
    }
    const direct_url = extracted_url && isStrongBossPdfPreviewUrl(extracted_url) ? extracted_url : viewer_url;
    return {
      viewer_url,
      extracted_url,
      direct_url,
      direct_source: extracted_url ? "viewer_inner_url" : "viewer_url",
    };
  }

  function isPdfPreviewRoot(root) {
    const tag = root?.tagName || "";
    const src = root?.getAttribute?.("src") || root?.getAttribute?.("data") || "";
    const descriptor = `${src} ${getElementDescriptor(root)}`;
    const isFrame = /IFRAME|OBJECT|EMBED/.test(tag);
    return (isFrame && isStrongBossPdfPreviewUrl(src)) || /pdf-viewer|preview4boss|\.pdf|pdf/i.test(descriptor);
  }

  function findPdfIframePreview(fallbackInfo = {}, debugContext = null) {
    const visibleFrames = Array.from(document.querySelectorAll("iframe, object, embed"))
      .filter((el) => {
        try { return isVisible(el); } catch { return false; }
      });
    if (debugContext) {
      emit({
        type: "pdf_iframe_preview_scan_started",
        data: {
          candidate_id: debugContext.candidateId || "",
          candidate_signature: debugContext.signature || "",
          total_frames: visibleFrames.length,
          strong_candidates: visibleFrames.filter((el) => isStrongBossPdfPreviewUrl(el.getAttribute?.("src") || el.getAttribute?.("data") || "")).length,
          frame_samples: visibleFrames.map((el) => ({ tag: el.tagName || "", rect: getRectSnapshot(el), src: el.getAttribute?.("src") || el.getAttribute?.("data") || "" })).slice(0, 5),
        },
      });
    }
    const frames = visibleFrames
      .filter((el) => {
        try {
          const rect = el.getBoundingClientRect();
          const src = el.getAttribute?.("src") || el.getAttribute?.("data") || "";
          return rect.width >= 300 && rect.height >= 180 && rect.left > window.innerWidth * 0.08 && isStrongBossPdfPreviewUrl(src);
        } catch {
          return false;
        }
      })
      .sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return br.width * br.height - ar.width * ar.height;
      });
    const root = frames[0];
    if (!root) return null;
    const rect = root.getBoundingClientRect();
    const preview = { root, rect, score: 99, info: extractResumePreviewInfo(root, fallbackInfo), pdf_iframe: true };
    if (debugContext) {
      emit({
        type: "pdf_iframe_preview_detected",
        data: {
          candidate_id: debugContext.candidateId || "",
          candidate_signature: debugContext.signature || "",
          ...describePreviewComponent(root),
          ...(preview.info || {}),
        },
      });
    }
    return preview;
  }

  function extractResumePreviewInfo(root, fallbackInfo = {}) {
    const text = (root?.innerText || root?.textContent || "").replace(/\s+/g, " ").trim();
    const emailMatch = text.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i);
    const phoneMatch = text.match(/(?:\+?86[-\s]?)?1[3-9]\d[-\s]?\d{4}[-\s]?\d{4}/);
    const ageMatch = text.match(/(\d{2})\s*岁/);
    const genderMatch = text.match(/(?:^|\s)(男|女)(?:\s|$|·|\||，|,)/);
    const nativePlaceMatch = text.match(/(?:籍贯|户籍|现居住地|所在地|现居地)[:：\s]*([\u4e00-\u9fa5]{2,12})/);
    const toolbarNameBlacklist = new Set(["全屏", "缩放", "放大", "缩小", "打印", "下载", "上一页", "下一页", "页面", "旋转", "适合", "宽度", "附件简历", "下载简历", "在线简历", "个人简历", "查看简历", "简历预览", "求职意向", "工作经历", "项目经历", "教育经历", "正在加载", "请稍等"]);
    const candidateNames = Array.from(text.slice(0, 320).matchAll(/[\u4e00-\u9fa5]{2,4}(?:先生|女士)?/g)).map((m) => m[0]);
    const fallbackName = fallbackInfo.name && fallbackInfo.name !== "待识别" ? fallbackInfo.name : "";
    const extractedName = candidateNames.find((x) => !toolbarNameBlacklist.has(x) && !toolbarNameBlacklist.has(x.replace(/先生|女士/g, "")));
    const name = isPdfPreviewRoot(root) ? (fallbackName || extractedName || "未识别") : (extractedName || fallbackName || "未识别");
    return {
      name,
      gender: genderMatch ? genderMatch[1] : "未识别",
      age: ageMatch ? `${ageMatch[1]}岁` : (fallbackInfo.age || "未识别"),
      native_place: nativePlaceMatch ? nativePlaceMatch[1] : "未识别",
      phone: phoneMatch ? phoneMatch[0] : "未识别",
      email: emailMatch ? emailMatch[0] : "未识别",
      preview_source: isPdfPreviewRoot(root) ? "pdf_iframe_or_viewer" : "dom_text",
      iframe_src: root?.getAttribute?.("src") || root?.getAttribute?.("data") || "",
      text_sample: text.slice(0, 240),
    };
  }

  function isLoadingResumePreviewText(text) {
    return /正在加载简历|正 在 加 载 简 历|请稍等/.test(text || "");
  }

  function describePreviewComponent(root) {
    if (!root) return {};
    return {
      component_tag: root.tagName || "",
      component_id: root.getAttribute?.("id") || "",
      component_role: root.getAttribute?.("role") || "",
      component_class: getElementClassName(root),
      component_path: getElementDomPath(root),
      component_rect: getRectSnapshot(root),
      component_descriptor: getElementDescriptor(root).slice(0, 240),
      component_src: root.getAttribute?.("src") || root.getAttribute?.("data") || "",
      component_preview_type: isPdfPreviewRoot(root) ? "pdf_iframe_or_viewer" : "dom_text",
    };
  }

  function emitResumePreviewCandidateConfirm(candidateId, signature, preview) {
    emitCritical({
      type: "resume_preview_candidate_confirm",
      data: {
        candidate_id: candidateId,
        candidate_signature: signature,
        ...describePreviewComponent(preview.root),
        ...(preview.info || {}),
      },
    });
  }

  function getRectSnapshot(el) {
    const rect = el.getBoundingClientRect();
    return {
      left: Math.round(rect.left),
      top: Math.round(rect.top),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    };
  }

  function getElementClassName(el) {
    return `${el?.className || ""}`.replace(/\s+/g, " ").slice(0, 180);
  }

  function snapshotDiagnosticElement(el, textLimit = 180) {
    return {
      tag: el.tagName || "",
      id: el.getAttribute?.("id") || "",
      role: el.getAttribute?.("role") || "",
      class_name: getElementClassName(el),
      rect: getRectSnapshot(el),
      text: textOf(el).slice(0, textLimit),
      descriptor: getElementDescriptor(el).slice(0, 240),
      path: getElementDomPath(el),
    };
  }

  function collectResumePreviewDiagnostics(candidateId = "", signature = "", info = {}, lastSample = "") {
    const overlaySelectors = [
      "[role='dialog']",
      "[class*='dialog']",
      "[class*='modal']",
      "[class*='preview']",
      "[class*='viewer']",
      "[class*='pdf']",
      "[class*='resume']",
      "[class*='attachment']",
      "[class*='drawer']",
      "[class*='popup']",
      "[class*='pop']",
      "[class*='layer']",
    ];
    const seen = new Set();
    const overlays = overlaySelectors
      .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
      .filter((el) => {
        if (seen.has(el)) return false;
        seen.add(el);
        try { return isVisible(el); } catch { return false; }
      })
      .map((el) => snapshotDiagnosticElement(el, 220))
      .sort((a, b) => (b.rect.width * b.rect.height) - (a.rect.width * a.rect.height))
      .slice(0, 14);

    const frames = Array.from(document.querySelectorAll("iframe, object, embed"))
      .filter((el) => {
        try { return isVisible(el); } catch { return false; }
      })
      .map((el) => ({
        ...snapshotDiagnosticElement(el, 120),
        src: el.getAttribute("src") || el.getAttribute("data") || "",
        type: el.getAttribute("type") || "",
      }))
      .slice(0, 10);

    const largeBlocks = Array.from(document.querySelectorAll("body *"))
      .filter((el) => {
        try {
          if (!isVisible(el)) return false;
          const rect = el.getBoundingClientRect();
          if (rect.width < 260 || rect.height < 120) return false;
          if (rect.top < -30 || rect.left < -30) return false;
          if (rect.left < window.innerWidth * 0.12 && rect.width < window.innerWidth * 0.65) return false;
          return true;
        } catch {
          return false;
        }
      })
      .map((el) => snapshotDiagnosticElement(el, 160))
      .sort((a, b) => (b.rect.width * b.rect.height) - (a.rect.width * a.rect.height))
      .slice(0, 18);

    return {
      candidate_id: candidateId,
      candidate_signature: signature,
      candidate_name: info.name || "",
      url: location.href,
      title: document.title,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      body_sample: lastSample || (document.body?.innerText || "").replace(/\s+/g, " ").slice(0, 600),
      overlays,
      frames,
      large_blocks: largeBlocks,
    };
  }

  function shouldAbortAsyncStep() {
    return !isActiveInstance() || state === "stopped" || state === "idle";
  }

  async function waitUntilResumePreviewGone(timeoutMs = 1200) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (!findResumePreview()) return true;
      await sleep(150);
    }
    return !findResumePreview();
  }

  function emitResumePreviewDiagnostics(candidateId = "", signature = "", info = {}, sample = "", reason = "manual_probe") {
    void candidateId;
    void signature;
    void info;
    void sample;
    void reason;
  }

  function findResumePreview(fallbackInfo = {}, debugContext = null) {
    const pdfFramePreview = findPdfIframePreview(fallbackInfo, debugContext);
    if (pdfFramePreview) return pdfFramePreview;

    const roots = getPreviewRoots();
    const matches = [];
    for (const root of roots) {
      const rect = root.getBoundingClientRect?.();
      const text = (root.innerText || root.textContent || "").replace(/\s+/g, " ").trim();
      if (!rect || rect.width < 220 || rect.height < 120) continue;
      if (isLoadingResumePreviewText(text)) continue;
      const descriptor = getElementDescriptor(root);
      const src = root.getAttribute?.("src") || root.getAttribute?.("data") || "";
      let score = 0;
      if (isPdfPreviewRoot(root)) score += 8;
      if (/简历|附件|预览|PDF|pdf|resume/i.test(text + " " + descriptor + " " + src)) score += 5;
      if (/求职意向|工作经历|项目经历|教育经历|个人优势|个人信息|联系方式/.test(text)) score += 5;
      if (/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i.test(text)) score += 3;
      if (/(?:\+?86[-\s]?)?1[3-9]\d[-\s]?\d{4}[-\s]?\d{4}/.test(text)) score += 3;
      if (rect.left > window.innerWidth * 0.15) score += 1;
      if (!/聊天|常用语|发送/.test(text.slice(0, 120))) score += 1;
      if (score >= 6) matches.push({ root, rect, score, info: extractResumePreviewInfo(root, fallbackInfo) });
    }
    matches.sort((a, b) => b.score - a.score || b.rect.width * b.rect.height - a.rect.width * a.rect.height);
    return matches[0] || null;
  }

  async function waitForResumePreview(candidateId = "", signature = "", info = {}, timeoutMs = 12000) {
    const deadline = Date.now() + timeoutMs;
    let lastSample = "";
    while (Date.now() < deadline) {
      if (shouldAbortAsyncStep()) {
        return null;
      }
      const preview = findResumePreview(info, { candidateId, signature });
      if (preview) {
        emit({ type: "resume_preview_wait_result", data: { candidate_id: candidateId, candidate_signature: signature, found: true, stage: "wait_found", elapsed_ms: timeoutMs - Math.max(0, deadline - Date.now()) } });
        return preview;
      }
      lastSample = (document.body?.innerText || "").replace(/\s+/g, " ").slice(0, 260) || lastSample;
      await sleep(250);
    }
    const weakPreview = makeResumePreviewFromLargestRoot(info);
    if (weakPreview) {
      emit({ type: "resume_preview_not_found", data: { candidate_id: candidateId, candidate_signature: signature, sample: lastSample, weak_candidate: true } });
      return null;
    }
    emit({ type: "resume_preview_not_found", data: { candidate_id: candidateId, candidate_signature: signature, sample: lastSample } });
    return null;
  }

  function saveLearnedDownloadClick(snapshot) {
    resumePreviewLearnState.learnedClick = snapshot;
    try {
      localStorage.setItem(STORAGE_KEYS.learnedClick, JSON.stringify(snapshot));
    } catch {}
  }

  function getFrameDownloadCandidateAtPoint(x, y) {
    const frames = Array.from(document.querySelectorAll("iframe, object, embed"))
      .filter((el) => {
        try { return isVisible(el) && isPdfPreviewRoot(el); } catch { return false; }
      });
    for (const frame of frames) {
      const rect = frame.getBoundingClientRect();
      if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) continue;
      return {
        frame_tag: frame.tagName || "",
        frame_src: frame.getAttribute?.("src") || frame.getAttribute?.("data") || "",
        frame_rect: getRectSnapshot(frame),
        frame_relative_x: (x - rect.left) / Math.max(1, rect.width),
        frame_relative_y: (y - rect.top) / Math.max(1, rect.height),
      };
    }
    return null;
  }

  function captureNextManualDownloadClick(candidateId = "", signature = "", timeoutMs = 60000) {
    resumePreviewLearnState.waitingManualClick = true;
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        cleanup();
        emit({ type: "manual_download_click_timeout", data: { candidate_id: candidateId, candidate_signature: signature } });
        resolve(null);
      }, timeoutMs);

      const handler = (event) => {
        if (!resumePreviewLearnState.waitingManualClick) return;
        const target = document.elementFromPoint(event.clientX, event.clientY) || event.target;
        const snapshot = getElementSnapshot(target, event.clientX, event.clientY);
        const frameCandidate = getFrameDownloadCandidateAtPoint(event.clientX, event.clientY);
        if (frameCandidate) Object.assign(snapshot, frameCandidate);
        snapshot.candidate_id = candidateId;
        snapshot.candidate_signature = signature;
        saveLearnedDownloadClick(snapshot);
        emit({ type: "manual_download_click_captured", data: snapshot });
        cleanup();
        resolve(snapshot);
      };

      function cleanup() {
        resumePreviewLearnState.waitingManualClick = false;
        clearTimeout(timer);
        window.removeEventListener("pointerdown", handler, true);
        window.removeEventListener("mousedown", handler, true);
        window.removeEventListener("click", handler, true);
      }

      window.addEventListener("pointerdown", handler, true);
      window.addEventListener("mousedown", handler, true);
      window.addEventListener("click", handler, true);
    });
  }

  function isLikelyDownloadIcon(el, descriptor) {
    const combined = `${descriptor} ${getElementDescriptor(el.parentElement)} ${getElementDescriptor(el.closest?.("button, a, [role='button'], [class*='btn'], [class*='icon'], [class*='download']"))}`;
    if (isBossSvgDownloadDescriptor(combined)) return true;
    if (/下载附件|下载简历|下载|download|down-load|icon[-_]?download|download[-_]?icon|resume[-_]?download|file[-_]?download|attachment[-_]?download/i.test(combined)) return true;
    const href = el.getAttribute?.("href") || el.getAttribute?.("xlink:href") || "";
    if (/download|xiazai|down/i.test(href)) return true;
    const cls = `${el.className || ""}`;
    if (/download|down|xiazai/i.test(cls)) return true;
    return false;
  }

  function isBossSvgDownloadDescriptor(descriptor = "") {
    return /boss-svg/i.test(descriptor) && /svg-icon/i.test(descriptor) && /SVGAnimatedString/i.test(descriptor);
  }

  function getBossSvgDownloadSnapshot(el) {
    const node = el?.closest?.("button, a, [role='button'], [class*='btn'], [class*='icon'], [class*='download'], [class*='toolbar'] span, [class*='toolbar'] div") || el;
    return {
      component_name: "boss-svg svg-icon [object SVGAnimatedString]",
      component_descriptor: getElementDescriptor(node).slice(0, 260),
      component_path: getElementDomPath(node),
      component_rect: node ? getRectSnapshot(node) : null,
    };
  }

  function findBossSvgDownloadIcon(preview = null) {
    const roots = [];
    const pushRoot = (el) => {
      if (!el) return;
      if (!roots.includes(el)) roots.push(el);
    };
    if (preview?.root) {
      pushRoot(preview.root);
      pushRoot(preview.root.parentElement);
      pushRoot(preview.root.parentElement?.parentElement);
      pushRoot(preview.root.closest?.("[role='dialog'], [class*='dialog'], [class*='modal'], [class*='preview'], [class*='viewer'], [class*='pdf'], [class*='resume'], [class*='attachment'], [class*='drawer'], [class*='popup'], [class*='pop'], [class*='layer']"));
    }
    for (const root of getPreviewRoots()) pushRoot(root);
    if (!roots.length) pushRoot(document.body || document.documentElement);
    const matches = [];
    const selector = "svg, use, [class*='boss-svg'], [class*='svg-icon']";
    for (const root of roots) {
      if (!root) continue;
      const nodes = root.matches?.(selector) ? [root, ...root.querySelectorAll(selector)] : Array.from(root.querySelectorAll?.(selector) || []);
      for (const el of nodes) {
        const clickable = el.closest?.("button, a, [role='button'], [class*='btn'], [class*='icon'], [class*='download'], [class*='toolbar'] span, [class*='toolbar'] div") || el.parentElement || el;
        if (!clickable || !isVisible(clickable) || isDisabled(clickable)) continue;
        const rect = clickable.getBoundingClientRect();
        if (rect.width < 10 || rect.height < 10 || rect.top < 0 || rect.left < 0) continue;
        const descriptor = `${getElementDescriptor(el)} ${getElementDescriptor(clickable)} ${getElementDescriptor(clickable.parentElement)}`;
        const combined = descriptor.toLowerCase();
        if (!isBossSvgDownloadDescriptor(descriptor)) continue;
        if (/关闭|close|取消|返回|back|delete|trash|更多|more|打印|print|zoom|放大|缩小|rotate|旋转|×|✕|esc/i.test(combined)) continue;
        const score = 30 + (rect.top <= Math.min(220, window.innerHeight * 0.28) ? 4 : 0) + (rect.left > window.innerWidth * 0.55 ? 3 : 0) + (rect.left > window.innerWidth * 0.75 ? 2 : 0);
        matches.push({ el: clickable, rect, score });
      }
    }
    matches.sort((a, b) => b.score - a.score || b.rect.left - a.rect.left || a.rect.top - b.rect.top);
    return matches[0]?.el || null;
  }


  function getPreviewRoots() {
    const selectors = [
      "[role='dialog']",
      "[class*='dialog']",
      "[class*='modal']",
      "[class*='preview']",
      "[class*='viewer']",
      "[class*='pdf']",
      "[class*='resume']",
      "[class*='attachment']",
      "[class*='drawer']",
      "[class*='popup']",
      "[class*='pop']",
      "[class*='layer']",
      "iframe",
      "object",
      "embed",
    ];
    const seen = new Set();
    const roots = selectors.flatMap((selector) => Array.from(document.querySelectorAll(selector)))
      .filter((el) => {
        if (seen.has(el)) return false;
        seen.add(el);
        try { return isVisible(el); } catch { return false; }
      })
      .map((el) => ({ el, rect: el.getBoundingClientRect(), text: textOf(el) }))
      .filter(({ rect, text }) => rect.width >= 180 && rect.height >= 90 && rect.left > window.innerWidth * 0.08 && !/聊天|常用语|发送/.test(text.slice(0, 80)))
      .sort((a, b) => b.rect.width * b.rect.height - a.rect.width * a.rect.height)
      .map((x) => x.el);
    return roots;
  }

  function makeResumePreviewFromLargestRoot(fallbackInfo = {}) {
    const roots = getPreviewRoots();
    const root = roots[0];
    if (!root) return null;
    const rect = root.getBoundingClientRect();
    return { root, rect, score: 1, info: extractResumePreviewInfo(root, fallbackInfo), weak: true };
  }

  function findDownloadButton() {
    const matches = [];
    const roots = getPreviewRoots();
    const selector = "button, a, [role='button'], [class*='btn'], [class*='icon'], [class*='download'], div, span, i, svg, use";
    for (const root of roots) {
      for (const el of root.querySelectorAll(selector)) {
        const clickable = el.closest("button, a, [role='button'], [class*='btn'], [class*='icon'], [class*='download'], [class*='toolbar'] span, [class*='toolbar'] div") || el.parentElement || el;
        if (!isVisible(clickable) || isDisabled(clickable)) continue;
        const rect = clickable.getBoundingClientRect();
        if (rect.width < 10 || rect.height < 10 || rect.top < 0 || rect.left < 0) continue;
        const descriptor = `${getElementDescriptor(el)} ${getElementDescriptor(clickable)} ${getElementDescriptor(clickable.parentElement)}`;
        const combined = descriptor.toLowerCase();
        let score = 0;
        if (isLikelyDownloadIcon(el, descriptor)) score += 12;
        if (isBossSvgDownloadDescriptor(descriptor)) score += 30;
        if (/下载附件|下载简历/.test(descriptor)) score += 8;
        if (/下载|download|down/i.test(descriptor)) score += 6;
        if (/svg|icon|btn|button|toolbar/i.test(descriptor)) score += 2;
        if (rect.top <= Math.min(220, window.innerHeight * 0.28)) score += 4;
        if (rect.left > window.innerWidth * 0.55) score += 3;
        if (rect.left > window.innerWidth * 0.75) score += 2;
        if (rect.width <= 80 && rect.height <= 80) score += 2;
        if (/关闭|close|取消|返回|back|delete|trash|更多|more|打印|print|zoom|放大|缩小|rotate|旋转|×|✕|esc/i.test(combined)) score -= 12;
        if (score <= 0) continue;
        matches.push({ el: clickable, rect, score, text: descriptor.slice(0, 160) });
      }
    }

    if (!matches.length) {
      const pointCandidates = [
        { x: window.innerWidth - 92, y: 92 },
        { x: window.innerWidth - 132, y: 92 },
        { x: window.innerWidth - 172, y: 92 },
        { x: window.innerWidth - 92, y: 132 },
        { x: window.innerWidth - 132, y: 132 },
      ];
      for (const point of pointCandidates) {
        const el = document.elementFromPoint(point.x, point.y);
        const clickable = el?.closest?.("button, a, [role='button'], [class*='btn'], [class*='icon'], [class*='download'], div, span") || el;
        if (!clickable || !isVisible(clickable) || isDisabled(clickable)) continue;
        const rect = clickable.getBoundingClientRect();
        const descriptor = getElementDescriptor(clickable);
        if (/关闭|close|取消|返回|back|×|✕/i.test(descriptor)) continue;
        matches.push({ el: clickable, rect, score: /下载|download|down/i.test(descriptor) ? 6 : 2, text: `point:${point.x},${point.y} ${descriptor}`.slice(0, 160) });
      }
    }

    matches.sort((a, b) => b.score - a.score || b.rect.left - a.rect.left || a.rect.top - b.rect.top);
    return matches[0]?.el || null;
  }

  async function waitForDownloadButton(candidateId = "", signature = "", timeoutMs = 10000) {
    const deadline = Date.now() + timeoutMs;
    let lastCandidateText = "";
    let frameInfo = "";
    while (Date.now() < deadline) {
      const btn = findDownloadButton();
      if (btn) return btn;
      const samples = Array.from(document.querySelectorAll("button, a, [role='button'], [class*='btn'], [class*='icon'], [class*='download'], svg, use"))
        .filter((el) => isVisible(el))
        .map((el) => getElementDescriptor(el))
        .filter(Boolean)
        .slice(0, 12)
        .join(" | ");
      const frames = Array.from(document.querySelectorAll("iframe, object, embed"))
        .filter((el) => isVisible(el))
        .map((el) => `${el.tagName}:${el.getAttribute("src") || el.getAttribute("data") || ""}`)
        .slice(0, 4)
        .join(" | ");
      lastCandidateText = samples || lastCandidateText;
      frameInfo = frames || frameInfo;
      await sleep(300);
    }
    emit({ type: "download_button_candidates", data: { candidate_id: candidateId, candidate_signature: signature, samples: lastCandidateText, frames: frameInfo } });
    return null;
  }

  function waitForDownloadResult(candidateId, timeoutMs = 15000) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        pendingDownloadWaiters.delete(candidateId);
        resolve({ ok: false, reason: "download_timeout" });
      }, timeoutMs);
      pendingDownloadWaiters.set(candidateId, (result) => {
        clearTimeout(timer);
        pendingDownloadWaiters.delete(candidateId);
        resolve(result);
      });
    });
  }

  async function skipCandidate(candidateId, signature, reason, extra = {}) {
    emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason, ...extra } });
    results.skipped++;
    emitProgress();
    const delay = extra.fast_skip ? 20 : Math.min(Math.max(config.interval_ms || 0, 300), 900);
    await sleep(delay);
  }

  async function requestResumeAndSkip(btn, candidateId, signature) {
    if (hasResumeRequestSent(getChatDetailRoot())) {
      await skipCandidate(candidateId, signature, "resume_request_already_sent", { fast_skip: true });
      return;
    }
    const beforeCount = getResumeRequestSentCount();
    clickElementOnce(btn.el);
    const confirmed = await confirmRequestIfNeeded(candidateId, signature);
    const requestSent = confirmed ? await waitForResumeRequestSent(3000, beforeCount) : await waitForResumeRequestSent(1200, beforeCount);
    if (requestSent || confirmed) {
      emit({ type: "resume_request_success", data: { candidate_id: candidateId, candidate_signature: signature, confirmed, request_sent: requestSent } });
    }
    await skipCandidate(candidateId, signature, requestSent ? "resume_requested" : "resume_request_clicked", { confirmed, fast_skip: true });
  }

  function downloadDirectUrl(data = {}, timeoutMs = 10000) {
    emit({ type: "direct_download_message_send", data: { candidate_id: data.candidate_id || "", candidate_signature: data.candidate_signature || "", url: data.url || data.direct_url || data.iframe_src || "", timeout_ms: timeoutMs } });
    return new Promise((resolve) => {
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        emit({ type: "direct_download_message_timeout", data: { candidate_id: data.candidate_id || "", candidate_signature: data.candidate_signature || "", url: data.url || data.direct_url || data.iframe_src || "", timeout_ms: timeoutMs } });
        resolve({ ok: false, reason: "direct_download_response_timeout" });
      }, timeoutMs);
      try {
        chrome.runtime.sendMessage({ type: "download_direct_url", data }, (response) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          if (chrome.runtime.lastError) {
            const reason = chrome.runtime.lastError.message || "direct_download_message_failed";
            emit({ type: "direct_download_message_response", data: { candidate_id: data.candidate_id || "", candidate_signature: data.candidate_signature || "", ok: false, reason } });
            resolve({ ok: false, reason });
            return;
          }
          const result = response || { ok: false, reason: "empty_direct_download_response" };
          emit({ type: "direct_download_message_response", data: { candidate_id: data.candidate_id || "", candidate_signature: data.candidate_signature || "", ...result } });
          resolve(result);
        });
      } catch (error) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        const reason = String(error);
        emit({ type: "direct_download_message_response", data: { candidate_id: data.candidate_id || "", candidate_signature: data.candidate_signature || "", ok: false, reason } });
        resolve({ ok: false, reason });
      }
    });
  }

  function normalizeBossAbsoluteUrl(url = "", base = location.origin) {
    if (!url) return "";
    try {
      return new URL(url, base).href;
    } catch {
      return "";
    }
  }

  function resolveBossPdfPreviewDownloadTarget(preview) {
    const rawSrc = preview?.info?.iframe_src || preview?.root?.getAttribute?.("src") || preview?.root?.getAttribute?.("data") || "";
    if (!rawSrc || !isStrongBossPdfPreviewUrl(rawSrc)) {
      return { raw_src: rawSrc, normalized_src: "", viewer_url: "", extracted_src: "", download_url: "" };
    }

    const normalizedSrc = normalizeBossAbsoluteUrl(rawSrc);
    if (!normalizedSrc) {
      return { raw_src: rawSrc, normalized_src: "", viewer_url: "", extracted_src: "", download_url: "" };
    }

    let viewerUrl = "";
    let extractedSrc = "";
    let downloadUrl = normalizedSrc;
    try {
      const parsed = new URL(normalizedSrc);
      viewerUrl = parsed.href;
      const innerUrl = parsed.searchParams.get("url") || "";
      if (innerUrl) {
        extractedSrc = decodeURIComponent(innerUrl);
        const extractedNormalized = normalizeBossAbsoluteUrl(extractedSrc, parsed.origin);
        if (extractedNormalized) {
          downloadUrl = extractedNormalized;
        }
      }
    } catch {
      downloadUrl = normalizedSrc;
    }

    return { raw_src: rawSrc, normalized_src: normalizedSrc, viewer_url: viewerUrl, extracted_src: extractedSrc, download_url: downloadUrl };
  }

  function getPreviewDirectDownloadUrl(preview) {
    return resolveBossPdfPreviewDownloadTarget(preview).download_url || "";
  }

  async function tryDirectIframeDownload(candidateId, signature, info, preview) {
    const resolved = resolveBossPdfPreviewDownloadTarget(preview);
    const url = resolved.download_url;
    if (!url) {
      emit({ type: "direct_iframe_download_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "no_strong_pdf_iframe_url", iframe_src: resolved.raw_src || "" } });
      return false;
    }
    emit({ type: "direct_iframe_download_resolved", data: { candidate_id: candidateId, candidate_signature: signature, raw_src: resolved.raw_src || "", normalized_src: resolved.normalized_src || "", viewer_url: resolved.viewer_url || "", extracted_src: resolved.extracted_src || "", download_url: resolved.download_url || "" } });
    const payload = { candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, url, iframe_src: resolved.raw_src || url, normalized_src: resolved.normalized_src || "", viewer_url: resolved.viewer_url || "", extracted_src: resolved.extracted_src || "", direct_url: url };
    emit({ type: "direct_iframe_download_start", data: payload });
    const resultPromise = waitForDownloadResult(candidateId, 20000);
    const started = await downloadDirectUrl(payload);
    if (!started.ok) {
      emit({ type: "direct_iframe_download_failed", data: { ...payload, reason: started.reason || "direct_download_start_failed" } });
      return false;
    }
    emit({ type: "direct_iframe_download_created", data: { ...payload, download_id: started.download_id || "" } });
    const downloadResult = await resultPromise;
    if (downloadResult.ok) {
      emit({ type: "direct_iframe_download_link_captured", data: { ...payload, download_url: findDownloadUrlFromResult(downloadResult), ...(downloadResult.data || {}) } });
      results.downloaded++;
      emitProgress();
      await sleep(Math.min(Math.max(config.interval_ms || 0, 300), 900));
      return true;
    }
    emit({ type: "direct_iframe_download_failed", data: { ...payload, reason: downloadResult.reason || "download_failed", ...(downloadResult.data || {}) } });
    return false;
  }


  async function clickBossSvgDownloadIcon(candidateId, signature, info, preview) {
    emit({ type: "boss_svg_download_icon_scan_started", data: { candidate_id: candidateId, candidate_signature: signature, preview_source: preview?.info?.preview_source || "", iframe_src: preview?.info?.iframe_src || "" } });
    const target = findBossSvgDownloadIcon(preview);
    if (!target) {
      emit({ type: "boss_svg_download_icon_not_found", data: { candidate_id: candidateId, candidate_signature: signature } });
      return false;
    }
    const snapshot = getBossSvgDownloadSnapshot(target);
    emit({ type: "boss_svg_download_icon_found", data: { candidate_id: candidateId, candidate_signature: signature, ...snapshot } });
    emit({ type: "download_intent", data: { candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, click_strategy: "boss_svg_icon" } });
    const resultPromise = waitForDownloadResult(candidateId, 20000);
    clickElementReliably(target);
    emit({ type: "boss_svg_download_icon_clicked", data: { candidate_id: candidateId, candidate_signature: signature, ...snapshot } });
    const downloadResult = await resultPromise;
    if (downloadResult.ok) {
      emit({ type: "boss_svg_download_link_captured", data: { candidate_id: candidateId, candidate_signature: signature, download_url: findDownloadUrlFromResult(downloadResult), ...downloadResult.data } });
      results.downloaded++;
      emitProgress();
      await sleep(Math.min(Math.max(config.interval_ms || 0, 300), 900));
      return true;
    }
    emit({ type: "boss_svg_download_link_capture_failed", data: { candidate_id: candidateId, candidate_signature: signature, reason: downloadResult.reason || "download_failed", ...(downloadResult.data || {}) } });
    await skipCandidate(candidateId, signature, downloadResult.reason || "download_failed", downloadResult.data || {});
    return true;
  }

  function findLearnedDownloadElement(learned) {
    if (!learned) return null;
    if (learned.path) {
      try {
        const el = document.querySelector(learned.path);
        if (el && isVisible(el) && !isDisabled(el)) return el;
      } catch {}
    }

    if (typeof learned.frame_relative_x === "number" && typeof learned.frame_relative_y === "number") {
      const frames = Array.from(document.querySelectorAll("iframe, object, embed"))
        .filter((el) => {
          try { return isVisible(el) && isPdfPreviewRoot(el); } catch { return false; }
        });
      const learnedSrc = learned.frame_src || "";
      const frame = frames.find((el) => {
        const src = el.getAttribute?.("src") || el.getAttribute?.("data") || "";
        return learnedSrc && src === learnedSrc;
      }) || frames[0];
      if (frame) {
        const rect = frame.getBoundingClientRect();
        const x = rect.left + learned.frame_relative_x * rect.width;
        const y = rect.top + learned.frame_relative_y * rect.height;
        return document.elementFromPoint(
          Math.max(1, Math.min(window.innerWidth - 1, x)),
          Math.max(1, Math.min(window.innerHeight - 1, y))
        );
      }
    }

    const descriptor = (learned.descriptor || "").replace(/\s+/g, " ").trim();
    if (descriptor) {
      const tokens = descriptor.split(/\s+/).filter((x) => x.length >= 2 && !/object|SVGAnimatedString/.test(x)).slice(0, 8);
      const nodes = Array.from(document.querySelectorAll("button, a, [role='button'], [class*='btn'], [class*='icon'], [class*='download'], div, span, i, svg, use"))
        .filter((el) => isVisible(el) && !isDisabled(el));
      const scored = nodes.map((el) => {
        const text = `${getElementDescriptor(el)} ${getElementDescriptor(el.parentElement)}`;
        const score = tokens.reduce((sum, token) => sum + (text.includes(token) ? 1 : 0), 0);
        return { el, score };
      }).filter((x) => x.score > 0).sort((a, b) => b.score - a.score);
      if (scored[0]) return scored[0].el.closest("button, a, [role='button'], [class*='btn'], [class*='icon'], [class*='download']") || scored[0].el;
    }

    if (typeof learned.relative_x === "number" && typeof learned.relative_y === "number") {
      const x = Math.max(1, Math.min(window.innerWidth - 1, learned.relative_x * window.innerWidth));
      const y = Math.max(1, Math.min(window.innerHeight - 1, learned.relative_y * window.innerHeight));
      return document.elementFromPoint(x, y);
    }
    return null;
  }

  async function clickLearnedDownload(candidateId, signature, info) {
    const target = findLearnedDownloadElement(resumePreviewLearnState.learnedClick);
    if (!target) {
      emit({ type: "learned_download_click_failed", data: { candidate_id: candidateId, candidate_signature: signature, reason: "learned_element_not_found" } });
      return false;
    }

    emit({ type: "learned_download_click_used", data: { candidate_id: candidateId, candidate_signature: signature, ...getElementSnapshot(target) } });
    emit({ type: "download_intent", data: { candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf` } });
    const resultPromise = waitForDownloadResult(candidateId);
    clickElementReliably(target);
    const downloadResult = await resultPromise;
    if (downloadResult.ok) {
      results.downloaded++;
      emitProgress();
      await sleep(Math.min(Math.max(config.interval_ms || 0, 500), 1500));
      return true;
    }
    await skipCandidate(candidateId, signature, downloadResult.reason || "download_failed", downloadResult.data || {});
    return true;
  }

  function finishLearningTask(candidateId = "", signature = "", data = {}) {
    resumePreviewLearnState.learningStage = "learned";
    try {
      localStorage.setItem(STORAGE_KEYS.learningStage, "learned");
    } catch {}
    state = "stopped";
    pendingDownloadWaiters.forEach((resolve) => resolve({ ok: false, reason: "learning_finished" }));
    pendingDownloadWaiters.clear();
    collectFinishedEmitted = true;
    emit({ type: "download_learning_finished", data: { candidate_id: candidateId, candidate_signature: signature, ...data } });
    emit({ type: "collect_finished", data: { total_downloaded: results.downloaded, total_skipped: results.skipped, learning_finished: true } });
  }

  async function waitForResumeLearningContinue(candidateId = "", signature = "", preview = null) {
    state = "paused";
    if (preview) emitResumePreviewCandidateConfirm(candidateId, signature, preview);
    emit({ type: "collect_paused_for_resume_preview_confirm", data: { candidate_id: candidateId, candidate_signature: signature } });
    await waitForPause();
  }

  function findDownloadUrlFromResult(downloadResult) {
    const data = downloadResult?.data || {};
    return data.url || data.filename || data.download_path || "未捕获到下载链接";
  }

  async function startResumePreviewRecognition(candidateId, signature, info, btn, beforeUrl, stalePreviewClosed) {
    emitAttachmentDebug("01_enter_after_attachment_click", candidateId, signature, {
      button_state: btn.state,
      button_text: btn.text,
      before_url: beforeUrl,
      after_click_url: location.href,
      stale_preview_closed: stalePreviewClosed,
      state,
    });
    if (shouldAbortAsyncStep()) {
      emitAttachmentDebug("02_abort_before_preview_wait", candidateId, signature, { state });
      return null;
    }
    emitAttachmentDebug("02_call_wait_for_resume_preview", candidateId, signature, { timeout_ms: 12000 });
    const preview = await waitForResumePreview(candidateId, signature, info);
    emitAttachmentDebug(preview ? "03_wait_for_resume_preview_return_found" : "03_wait_for_resume_preview_return_null", candidateId, signature, {
      found: Boolean(preview),
      preview_score: preview?.score || 0,
      preview_rect: preview?.root ? getRectSnapshot(preview.root) : null,
      preview_descriptor: preview?.root ? getElementDescriptor(preview.root).slice(0, 180) : "",
    });
    return preview;
  }

  async function tryDownloadResume(candidateId, signature, info, preview = null, previewAlreadyWaited = false) {
    if (shouldAbortAsyncStep()) return;
    if (!preview && !previewAlreadyWaited) {
      preview = await waitForResumePreview(candidateId, signature, info);
    }
    if (shouldAbortAsyncStep()) return;
    if (!preview) {
      await skipCandidate(candidateId, signature, "resume_preview_not_found");
      return;
    }

    emit({ type: "resume_preview_detected", data: { candidate_id: candidateId, candidate_signature: signature, ...preview.info } });
    emit({ type: "resume_preview_info_extract_success", data: { candidate_id: candidateId, candidate_signature: signature, ...preview.info } });

    emit({ type: "resume_download_strategy_start", data: { candidate_id: candidateId, candidate_signature: signature, pdf_iframe: Boolean(preview.pdf_iframe), iframe_src: preview.info?.iframe_src || "", preview_source: preview.info?.preview_source || "" } });

    if (await tryDirectIframeDownload(candidateId, signature, info, preview)) {
      return;
    }

    if (await clickBossSvgDownloadIcon(candidateId, signature, info, preview)) {
      return;
    }

    if (resumePreviewLearnState.learnedClick && await clickLearnedDownload(candidateId, signature, info)) {
      return;
    }

    const downloadButton = await waitForDownloadButton(candidateId, signature, 5000);
    if (!downloadButton) {
      await skipCandidate(candidateId, signature, "download_button_not_found");
      return;
    }

    emit({ type: "auto_download_click_used", data: { candidate_id: candidateId, candidate_signature: signature, ...getElementSnapshot(downloadButton) } });
    emit({ type: "download_intent", data: { candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf` } });
    const resultPromise = waitForDownloadResult(candidateId, 20000);
    clickElementReliably(downloadButton);
    const downloadResult = await resultPromise;
    if (downloadResult.ok) {
      results.downloaded++;
      emitProgress();
      await sleep(Math.min(Math.max(config.interval_ms || 0, 500), 1500));
    } else {
      await skipCandidate(candidateId, signature, downloadResult.reason || "download_failed", downloadResult.data || {});
    }
  }

  async function waitForPause() {
    if (state !== "paused") return;
    await new Promise((resolve) => { pauseResolve = resolve; });
  }

  async function collectLoop() {
    if (!isActiveInstance()) return;
    if (activeCollectLoopRunId === activeRunId) return;
    activeCollectLoopRunId = activeRunId;
    try {
      if (!isAuthenticated()) {
      emit({ type: "error", data: { message: "未检测到登录态", stage: "pre_check" } });
      state = "idle";
      return;
    }

    emit({ type: "page_ready", data: { url: location.href } });

    await resetCandidateListScroll();
    const items = getCandidateItems();
    if (items.length === 0) {
      emit({ type: "error", data: { message: "未找到候选人列表", stage: "scan" } });
      state = "idle";
      return;
    }

    const seenSignatures = new Set();

    for (let i = results.currentIndex; i < items.length && results.downloaded < config.max_resumes; i++) {
      if (state === "stopped") break;
      await waitForPause();
      if (state === "stopped") break;

      results.currentIndex = i;
      const item = items[i];

      try {
        item.scrollIntoView({ block: "center" });
        await sleep(80);
        item.click();
        await sleep(450);
      } catch (error) {
        emit({ type: "candidate_skipped", data: { candidate_signature: `index_${i}`, reason: "click_failed", error: String(error) } });
        results.skipped++;
        continue;
      }

      const info = extractContactInfo(item);
      const signature = `${info.name}/${info.age}/${info.education}`;
      const candidateId = `${activeRunId || "run"}_${i}_${signature}`;

      if (seenSignatures.has(signature)) {
        continue;
      }
      seenSignatures.add(signature);

      emit({ type: "candidate_clicked", data: { ...info, candidate_id: candidateId, candidate_signature: signature, index: i } });

      if (signature === "待识别/待识别/待识别") {
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "candidate_info_unrecognized", raw_text: info.raw_text || "" } });
        results.skipped++;
        emitProgress();
        await sleep(20);
        continue;
      }

      const btn = findResumeButton();
      if (!btn) {
        await skipCandidate(candidateId, signature, "no_resume_button", { fast_skip: true });
        continue;
      }

      emit({ type: "resume_button_found", data: { candidate_id: candidateId, candidate_signature: signature, button_state: btn.state, button_text: btn.text } });

      if (btn.state === "requested") {
        await skipCandidate(candidateId, signature, "resume_request_already_sent", { fast_skip: true });
        continue;
      }

      if (btn.state === "request") {
        if (config.request_resume_if_missing) {
          await requestResumeAndSkip(btn, candidateId, signature);
        } else {
          await skipCandidate(candidateId, signature, "need_request_resume", { fast_skip: true });
        }
        continue;
      }

      emitAttachmentDebug("00_resume_button_ready", candidateId, signature, {
        button_state: btn.state,
        button_text: btn.text,
        button_rect: btn.el ? getRectSnapshot(btn.el) : null,
        button_descriptor: btn.el ? getElementDescriptor(btn.el).slice(0, 180) : "",
      });

      const beforeUrl = location.href;
      if (!guardResumeAttachmentClick(candidateId, signature)) {
        await skipCandidate(candidateId, signature, "resume_attachment_click_guarded", { fast_skip: true });
        continue;
      }
      clickElementOnce(btn.el);
      const preview = await startResumePreviewRecognition(candidateId, signature, info, btn, beforeUrl, true);
      if (shouldAbortAsyncStep()) break;
      if (!preview) {
        await skipCandidate(candidateId, signature, "resume_preview_not_found");
        continue;
      }

      await tryDownloadResume(candidateId, signature, info, preview, true);
    }

    state = "idle";
    if (!collectFinishedEmitted) {
      emit({ type: "collect_finished", data: { total_downloaded: results.downloaded, total_skipped: results.skipped } });
    }
    } finally {
      if (activeCollectLoopRunId === activeRunId) activeCollectLoopRunId = "";
    }
  }

  function emitProgress() {
    emit({
      type: "collect_progress",
      data: { total: results.downloaded + results.skipped, downloaded: results.downloaded, skipped: results.skipped, current_index: results.currentIndex },
    });
  }

  function emitPageStatus(trigger = "auto") {
    const authenticated = isAuthenticated();
    const detected = isBossPageDetected();
    emit({
      type: authenticated || detected ? "page_ready" : "page_detected",
      data: {
        url: location.href,
        title: document.title,
        authenticated,
        detected,
        trigger,
        text_sample: (document.body?.innerText || "").replace(/\s+/g, " ").slice(0, 200),
      },
    });
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!isActiveInstance()) {
      sendResponse({ ok: false, inactive_instance: true });
      return true;
    }
    if (msg.run_id) activeRunId = msg.run_id;
    switch (msg.type) {
      case "probe_page":
        emitPageStatus("probe");
        break;
      case "start_collect":
        window.__bossResumeCollectorActiveInstance = INSTANCE_ID;
        activeRunId = msg.run_id || `${Date.now()}`;
        if (state === "collecting" && activeCollectLoopRunId === activeRunId) break;
        state = "collecting";
        config = { ...config, ...msg.config };
        results = { downloaded: 0, skipped: 0, currentIndex: 0 };
        resumePreviewLearnState.learningStage = resumePreviewLearnState.learnedClick ? "learned" : "auto_download";
        resumePreviewLearnState.waitingManualClick = false;
        try {
          localStorage.setItem(STORAGE_KEYS.learningStage, resumePreviewLearnState.learningStage);
        } catch {}
        collectFinishedEmitted = false;
        pendingDownloadWaiters.forEach((resolve) => resolve({ ok: false, reason: "new_collect_started" }));
        pendingDownloadWaiters.clear();
        collectLoop();
        break;
      case "pause_collect":
        state = "paused";
        break;
      case "resume_collect":
        state = "collecting";
        if (pauseResolve) { pauseResolve(); pauseResolve = null; }
        break;
      case "reset_content_script":
        window.__bossResumeCollectorVersion = "";
        window.__bossResumeCollectorActiveInstance = "";
        state = "stopped";
        location.reload();
        break;
      case "stop_collect":
        state = "stopped";
        pendingDownloadWaiters.forEach((resolve) => resolve({ ok: false, reason: "collect_stopped" }));
        pendingDownloadWaiters.clear();
        if (pauseResolve) { pauseResolve(); pauseResolve = null; }
        break;
      case "download_completed":
      case "download_failed": {
        const data = msg.data || {};
        const resolve = pendingDownloadWaiters.get(data.candidate_id);
        if (resolve) {
          resolve({ ok: msg.type === "download_completed", reason: data.reason, data });
        }
        break;
      }
    }
    sendResponse({ ok: true });
    return true;
  });

  emitPageStatus("load");
})();
