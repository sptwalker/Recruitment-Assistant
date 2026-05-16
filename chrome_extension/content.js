(function () {
  "use strict";

  const CONTENT_SCRIPT_VERSION = "1.67.0";
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
  const RESUME_REQUESTED_TEXT = ["已向对方要附件简历", "已索要附件简历", "等待对方上传", "简历请求已发送"];


  let state = "idle";
  let config = { max_resumes: 5, interval_ms: 5000, request_resume_if_missing: false, boss_candidate_keys: [], boss_candidate_signatures: [], boss_pre_dedup_ready: false };
  let results = { downloaded: 0, skipped: 0, currentIndex: 0, completed: 0 };
  let pauseResolve = null;
  let activeRunId = "";
  let activeCollectLoopRunId = "";
  let lastResumeAttachmentClick = { key: "", at: 0 };
  let collectFinishedEmitted = false;
  const pendingDownloadWaiters = new Map();
  const pendingPersistAcks = new Map();
  const candidateResourceIdMap = new Map();
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
    const text = textOf(el);
    return Boolean(
      el.disabled ||
      el.getAttribute("aria-disabled") === "true" ||
      el.getAttribute("disabled") != null ||
      (el.className || "").toString().includes("disabled") ||
      /禁用|不可用|置灰|disabled/i.test(`${el.getAttribute?.("class") || ""} ${el.getAttribute?.("aria-label") || ""} ${el.getAttribute?.("title") || ""}`) ||
      /暂无附件|无附件|未上传|没有附件|不可查看|无法查看/.test(text) ||
      style.pointerEvents === "none" ||
      parseFloat(style.opacity || "1") < 0.55
    );
  }

  function getOpacityChain(el) {
    const values = [];
    let node = el;
    while (node && node !== document.body && values.length < 6) {
      try {
        const opacity = parseFloat(getComputedStyle(node).opacity || "1");
        if (!Number.isNaN(opacity)) values.push(opacity);
      } catch {}
      node = node.parentElement;
    }
    return values;
  }

  function isVisuallyDimmed(el) {
    return getOpacityChain(el).some((opacity) => opacity < 0.72);
  }

  function isResumeButtonUnavailable(btn) {
    if (!btn) return false;
    const text = btn.text || textOf(btn.el);
    const descriptor = `${text} ${getElementDescriptor(btn.el)} ${getElementDescriptor(btn.el?.parentElement)}`;
    return Boolean(
      btn.state === "disabled" ||
      btn.enabled === false ||
      isDisabled(btn.el) ||
      isVisuallyDimmed(btn.el) ||
      /暂无附件|无附件|未上传|没有附件|不可查看|无法查看|disabled|disable|置灰|禁用|不可用/i.test(descriptor)
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
    if (ageMatch) {
      const beforeAge = clean.slice(0, ageMatch.index).trim();
      const nameMatches = Array.from(beforeAge.matchAll(/[\u4e00-\u9fa5]{2,4}(?:先生|女士)?/g)).map((m) => m[0]);
      for (let i = nameMatches.length - 1; i >= 0; i--) {
        const value = nameMatches[i];
        if (!isInvalidCandidateNameToken(value)) {
          name = value;
          break;
        }
      }
    }

    let education = eduMatch ? eduMatch[0] : "待识别";
    if (education === "研究生") education = "硕士";
    if (education === "专科") education = "大专";

    return { name, age: ageMatch ? `${ageMatch[1]}岁` : "待识别", education, raw_text: clean.slice(0, 160) };
  }

  function normalizeEducation(value = "") {
    if (value === "研究生") return "硕士";
    if (value === "专科") return "大专";
    return value;
  }

  function normalizeBossCandidateKeyPart(value = "", fallback = "待识别") {
    let text = String(value || "").replace(/\s+/g, "").replace(/^[-—_｜|/\\:：,，;；.。()（）\[\]【】]+|[-—_｜|/\\:：,，;；.。()（）\[\]【】]+$/g, "");
    if (!text || text === "待识别") text = fallback;
    text = text.replace(/[^\p{L}\p{N}_.-]/gu, "_").replace(/^[_\.]+|[_\.]+$/g, "");
    return (text || fallback).slice(0, 24);
  }

  async function sha256Hex(value = "") {
    const normalized = String(value || "").trim().toLowerCase();
    if (!normalized) return "";
    const bytes = new TextEncoder().encode(normalized);
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  function normalizeBossCandidateSignature(signature = "") {
    const parts = String(signature || "").split("/").map((x) => x.trim());
    while (parts.length < 3) parts.push("");
    const name = normalizeBossCandidateKeyPart(parts[0], "待识别");
    const age = normalizeBossCandidateKeyPart(parts[1], "待识别");
    const education = normalizeBossCandidateKeyPart(parts[2], "待识别");
    return `${name}/${age}/${education}`;
  }

  async function buildBossCandidateKey(signature = "", info = {}) {
    const parts = String(signature || "").split("/").map((x) => x.trim());
    while (parts.length < 3) parts.push("");
    const name = normalizeBossCandidateKeyPart(info.name || parts[0], "待识别");
    const age = normalizeBossCandidateKeyPart(info.age || parts[1], "待识别");
    const education = normalizeBossCandidateKeyPart(info.education || parts[2], "待识别");
    return sha256Hex(["boss", "profile_name_age_education", name, age, education].join("|"));
  }

  function parseTopProfileName(text = "") {
    const clean = stripActivityText(text).replace(/刚刚活跃|今日活跃|昨日活跃|活跃|在线/g, " ").trim();
    const titledName = clean.match(/([\u4e00-\u9fa5]{1,3}(?:先生|女士))/);
    if (titledName && !isInvalidCandidateNameToken(titledName[1])) return titledName[1];
    const beforeLocation = clean.split(/在|现居|居住|位于|来自/)[0].trim();
    const source = beforeLocation || clean;
    const names = Array.from(source.matchAll(/[\u4e00-\u9fa5]{2,4}/g)).map((m) => m[0]);
    for (const value of names) {
      if (!isInvalidCandidateNameToken(value)) return value;
    }
    return "";
  }

  function parseTopProfileText(text) {
    const clean = stripActivityText(text);
    if (!clean || /职位管理|推荐牛人|牛人管理|工具箱|招聘规范|我的客服|在线简历|附件简历|沟通职位|期望|工作经历/.test(clean)) return null;
    const ageMatch = clean.match(/(\d{2})\s*岁/);
    const eduMatch = clean.match(/博士|硕士|研究生|本科|大专|专科|高中|中专/);
    if (!ageMatch || !eduMatch) return null;
    const name = parseTopProfileName(clean.slice(0, ageMatch.index));
    if (!name) return null;
    const education = normalizeEducation(eduMatch[0]);
    return { name, age: `${ageMatch[1]}岁`, education, raw_text: clean.slice(0, 160) };
  }

  function isTopProfileBandRect(rect) {
    const pageWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    return rect.left >= pageWidth * 0.28 && rect.left <= pageWidth * 0.78 && rect.top >= 80 && rect.top <= 170;
  }

  function isTopProfileNameBandRect(rect) {
    const pageWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    return rect.left >= pageWidth * 0.28 && rect.left <= pageWidth * 0.62 && rect.top >= 80 && rect.top <= 170;
  }

  function isTopProfileAgeEducationBandRect(rect) {
    const pageWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    return rect.left >= pageWidth * 0.42 && rect.left <= pageWidth * 0.78 && rect.top >= 80 && rect.top <= 170;
  }

  function getTopProfileTokens() {
    return Array.from(document.querySelectorAll("body *"))
      .filter((el) => {
        if (!isVisible(el)) return false;
        const rect = el.getBoundingClientRect();
        if (!isTopProfileBandRect(rect)) return false;
        if (rect.width < 8 || rect.width > 520 || rect.height < 10 || rect.height > 90) return false;
        const text = textOf(el);
        if (!text || text.length > 180) return false;
        if (/职位管理|推荐牛人|牛人管理|工具箱|招聘规范|我的客服|在线简历|附件简历|沟通职位|期望|工作经历|职位:|沟通职位/.test(text)) return false;
        return /\d{2}\s*岁|博士|硕士|研究生|本科|大专|专科|高中|中专|[\u4e00-\u9fa5]{2,4}/.test(text);
      })
      .map((el) => ({ el, rect: el.getBoundingClientRect(), text: textOf(el) }))
      .sort((a, b) => a.rect.left - b.rect.left || a.rect.top - b.rect.top);
  }

  function findTopProfileInfoRoot() {
    const tokens = getTopProfileTokens();
    const nameTokens = tokens
      .filter((x) => isTopProfileNameBandRect(x.rect))
      .map((x) => ({ ...x, name: parseTopProfileName(x.text) }))
      .filter((x) => x.name);
    const ageTokens = tokens
      .filter((x) => isTopProfileAgeEducationBandRect(x.rect) && /\d{2}\s*岁/.test(x.text));
    const eduTokens = tokens
      .filter((x) => isTopProfileAgeEducationBandRect(x.rect))
      .map((x) => ({ ...x, education: normalizeEducation((x.text.match(/博士|硕士|研究生|本科|大专|专科|高中|中专/) || [""])[0]) }))
      .filter((x) => x.education);

    for (const ageToken of ageTokens) {
      const ageMatch = ageToken.text.match(/(\d{2})\s*岁/);
      const ageCenterY = ageToken.rect.top + ageToken.rect.height / 2;
      const rowNameTokens = nameTokens
        .filter((x) => Math.abs((x.rect.top + x.rect.height / 2) - ageCenterY) <= 34 && x.rect.right <= ageToken.rect.left + 48)
        .map((x) => ({ ...x, distance: Math.abs(ageToken.rect.left - x.rect.right) + Math.abs((x.rect.top + x.rect.height / 2) - ageCenterY) * 2 }))
        .sort((a, b) => a.distance - b.distance);
      const rowEduTokens = eduTokens
        .filter((x) => Math.abs((x.rect.top + x.rect.height / 2) - ageCenterY) <= 34 && x.rect.left >= ageToken.rect.left - 24)
        .map((x) => ({ ...x, distance: Math.abs(x.rect.left - ageToken.rect.right) + Math.abs((x.rect.top + x.rect.height / 2) - ageCenterY) * 2 }))
        .sort((a, b) => a.distance - b.distance);
      const nameToken = rowNameTokens[0];
      const eduToken = rowEduTokens[0];
      if (nameToken && eduToken && ageMatch) {
        const rawText = `${nameToken.text} ${ageToken.text} ${eduToken.text}`.replace(/\s+/g, " ").trim();
        return {
          el: ageToken.el,
          info: { name: nameToken.name, age: `${ageMatch[1]}岁`, education: eduToken.education, raw_text: rawText.slice(0, 160) },
          score: 100,
          top: ageToken.rect.top,
          area: ageToken.rect.width * ageToken.rect.height,
          len: rawText.length,
        };
      }
    }

    const combined = tokens
      .filter((x) => x.rect.width >= 80 && x.rect.width <= 520 && x.rect.height >= 18 && x.rect.height <= 90)
      .map((x) => ({ ...x, info: parseTopProfileText(x.text) }))
      .filter((x) => x.info)
      .sort((a, b) => a.rect.top - b.rect.top || a.rect.left - b.rect.left)[0];
    return combined ? { el: combined.el, info: combined.info, score: 80, top: combined.rect.top, area: combined.rect.width * combined.rect.height, len: combined.text.length } : null;
  }

  function buildContactSourceInfo(el, sourceType = "unknown", sourceNote = "") {
    if (!el) return { contact_source: sourceType, contact_source_note: sourceNote };
    return {
      contact_source: sourceType,
      contact_source_note: sourceNote,
      contact_source_rect: getRectSnapshot(el),
      contact_source_path: getElementDomPath(el),
      contact_source_class: getElementClassName(el),
      contact_source_text_sample: textOf(el).slice(0, 160),
    };
  }

  function extractContactInfo(clickedItem = null) {
    void clickedItem;
    const match = findTopProfileInfoRoot();
    if (match) {
      return {
        ...match.info,
        ...buildContactSourceInfo(match.el, "top_profile_red_boxes", "右侧沟通页顶部红框区域：姓名、年龄、学历")
      };
    }
    return { name: "待识别", age: "待识别", education: "待识别", raw_text: "", contact_source: "top_profile_red_boxes_not_found", contact_source_note: "未在右侧沟通页顶部红框区域识别到姓名、年龄、学历" };
  }

  async function waitForContactInfo(clickedItem = null, timeoutMs = 2200) {
    const deadline = Date.now() + timeoutMs;
    let info = extractContactInfo(clickedItem);
    while (`${info.name}/${info.age}/${info.education}` === "待识别/待识别/待识别" && Date.now() < deadline) {
      await sleep(120);
      info = extractContactInfo(clickedItem);
    }
    return info;
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
    if (!text.includes("附件简历")) return "none";
    return "attachment";
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
      const clickable = getClickableResumeElement(el);
      if (!isVisible(clickable)) continue;
      const dimmed = isDisabled(clickable) || isDisabled(el) || isVisuallyDimmed(clickable) || isVisuallyDimmed(el);
      matches.push({
        el: clickable,
        text,
        state: dimmed ? "dim" : "bright",
        state_label: dimmed ? "暗淡" : "明亮",
        enabled: !dimmed,
        left: rect.left,
        top: rect.top,
      });
    }
    matches.sort((a, b) => (a.state === b.state ? 0 : a.state === "bright" ? -1 : 1) || b.left - a.left || a.top - b.top);
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
    if (!el) return false;
    const nodes = [];
    const pushNode = (node) => {
      if (node && !nodes.includes(node)) nodes.push(node);
    };
    pushNode(el.closest?.("button, a, [role='button'], [class*='btn'], [class*='icon-content'], [class*='download'], [class*='resume'], [class*='popover']"));
    pushNode(el);
    pushNode(el.parentElement);
    let clicked = false;
    for (const node of nodes) {
      if (!node || !isVisible(node) || isDisabled(node)) continue;
      try { node.click?.(); clicked = true; } catch {}
      clicked = clickElementOnce(node) || clicked;
    }
    return clicked;
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

  function getElementSvgHints(el) {
    const root = el || null;
    const nodes = root ? [root, ...Array.from(root.querySelectorAll?.("svg, use, i, span") || [])] : [];
    return nodes.slice(0, 10).map((node) => ({
      tag: node.tagName || "",
      class_name: getElementClassName(node),
      title: node.getAttribute?.("title") || "",
      aria_label: node.getAttribute?.("aria-label") || "",
      href: node.getAttribute?.("href") || node.getAttribute?.("xlink:href") || "",
      data_icon: node.getAttribute?.("data-icon") || node.getAttribute?.("data-name") || "",
      text: textOf(node).slice(0, 80),
      descriptor: getElementDescriptor(node).slice(0, 160),
    }));
  }

  function getNeighborActionSnapshots(el, limit = 8) {
    const area = el?.closest?.("[class*='attachment-resume-btns'], [class*='resume-footer'], [class*='toolbar'], [class*='icon-content'], [class*='popover']")?.parentElement || el?.parentElement;
    if (!area) return [];
    return Array.from(area.querySelectorAll("button, a, [role='button'], [class*='icon-content'], [class*='popover'], [class*='download'], svg, use"))
      .filter((node) => {
        try { return isVisible(node); } catch { return false; }
      })
      .map((node) => ({
        ...snapshotDiagnosticElement(node, 80),
        svg_hints: getElementSvgHints(node).slice(0, 4),
      }))
      .slice(0, limit);
  }

  function getDownloadClickDiagnostics(el, stage = "before_click") {
    const node = getDownloadClickableNode(el) || el;
    const frames = Array.from(document.querySelectorAll("iframe, object, embed"))
      .filter((frame) => {
        try { return isVisible(frame); } catch { return false; }
      })
      .map((frame) => ({ tag: frame.tagName || "", rect: getRectSnapshot(frame), src: frame.getAttribute?.("src") || frame.getAttribute?.("data") || "" }))
      .slice(0, 6);
    return {
      stage,
      target: node ? snapshotDiagnosticElement(node, 140) : null,
      svg_hints: getElementSvgHints(node),
      neighbor_actions: getNeighborActionSnapshots(node),
      frames,
      body_toast_sample: Array.from(document.querySelectorAll("[class*='toast'], [class*='message'], [class*='notice'], [class*='tip']"))
        .filter((tip) => {
          try { return isVisible(tip); } catch { return false; }
        })
        .map((tip) => textOf(tip).slice(0, 120))
        .filter(Boolean)
        .slice(0, 6),
    };
  }

  function isStrongBossPdfPreviewUrl(src = "") {
    return /pdf-viewer-b|bzl-office\/pdf-viewer|preview4boss|wflow\/zpgeek\/download\/preview4boss|\.pdf(?:$|[?#])/i.test(src);
  }

  function extractBossPdfResourceId(src = "") {
    if (!src) return "";
    const raw = String(src);
    try {
      const parsed = new URL(raw, location.origin);
      const innerCandidate = parsed.searchParams.get("url") || parsed.searchParams.get("file") || parsed.searchParams.get("src") || "";
      const candidates = [];
      if (innerCandidate) {
        try { candidates.push(decodeURIComponent(innerCandidate)); } catch { candidates.push(innerCandidate); }
      }
      candidates.push(parsed.href);
      for (const candidate of candidates) {
        try {
          const sub = new URL(candidate, parsed.origin);
          const segments = sub.pathname.split("/").filter(Boolean);
          for (let i = segments.length - 1; i >= 0; i -= 1) {
            const seg = segments[i].replace(/\.[a-z0-9]{1,5}$/i, "");
            if (seg.length >= 10 && /[A-Za-z0-9]/.test(seg)) return seg;
          }
        } catch {}
      }
    } catch {}
    const tail = raw.split("?")[0].split("#")[0].split("/").filter(Boolean).pop() || "";
    return tail.replace(/\.[a-z0-9]{1,5}$/i, "");
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
          if (!(rect.width >= 300 && rect.height >= 180 && rect.left > window.innerWidth * 0.08 && isStrongBossPdfPreviewUrl(src))) return false;
          const resourceId = extractBossPdfResourceId(src);
          const ownerSig = resourceId ? resourceIdOwnerSignature(resourceId) : "";
          const callerSig = debugContext?.signature || "";
          if (ownerSig && callerSig && ownerSig !== callerSig) {
            emit({ type: "pdf_iframe_preview_skipped_owned_by_other", data: { candidate_signature: callerSig, owner_signature: ownerSig, resource_id: resourceId, src } });
            return false;
          }
          return true;
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
    const callerSig = debugContext?.signature || "";
    const previewResourceId = extractBossPdfResourceId(preview.info?.iframe_src || "");
    if (callerSig && previewResourceId && !resourceIdOwnerSignature(previewResourceId)) {
      candidateResourceIdMap.set(callerSig, previewResourceId);
      emit({ type: "pdf_iframe_resource_id_claimed", data: { candidate_signature: callerSig, resource_id: previewResourceId } });
    }
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
    const name = fallbackName || extractedName || "未识别";
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

  function getResumePreviewFingerprint(preview = null) {
    const root = preview?.root || findResumePreview()?.root;
    if (!root) return "";
    const tag = root.tagName || "";
    const src = root.getAttribute?.("src") || root.getAttribute?.("data") || "";
    if (/IFRAME|OBJECT|EMBED/.test(tag) && src) {
      const resourceId = extractBossPdfResourceId(src);
      if (resourceId) return `pdf-resource|${resourceId}`;
    }
    const rect = root.getBoundingClientRect?.();
    const text = (root.innerText || root.textContent || "").replace(/\s+/g, " ").slice(0, 160);
    const path = getElementDomPath(root);
    return `${tag}|${src}|${path}|${rect ? `${Math.round(rect.left)},${Math.round(rect.top)},${Math.round(rect.width)},${Math.round(rect.height)}` : ""}|${text}`;
  }

  function clickResumePreviewCloseButton(candidateId = "", signature = "") {
    const roots = getPreviewRoots();
    const matches = [];
    for (const root of roots) {
      const nodes = root.querySelectorAll?.("button, a, [role='button'], span, i, svg, use, div") || [];
      for (const el of nodes) {
        if (!isVisible(el)) continue;
        const clickable = el.closest?.("button, a, [role='button'], [class*='btn'], [class*='icon'], span, i, div") || el;
        if (!clickable || !isVisible(clickable)) continue;
        const rect = clickable.getBoundingClientRect();
        if (rect.width < 10 || rect.height < 10 || rect.top < 0) continue;
        const descriptor = `${getElementDescriptor(el)} ${getElementDescriptor(clickable)}`;
        let score = 0;
        if (/关闭|close|取消|返回|back|×|✕|icon-close/i.test(descriptor)) score += 8;
        if (rect.left > window.innerWidth * 0.55 && rect.top < window.innerHeight * 0.35) score += 3;
        if (rect.width <= 80 && rect.height <= 80) score += 1;
        if (/下载|download|打印|print|zoom|放大|缩小|rotate|旋转/i.test(descriptor)) score -= 12;
        if (score > 0) matches.push({ el: clickable, score, rect, descriptor });
      }
    }
    matches.sort((a, b) => b.score - a.score || b.rect.left - a.rect.left || a.rect.top - b.rect.top);
    emit({
      type: "stale_preview_close_diagnostics",
      data: {
        candidate_id: candidateId,
        candidate_signature: signature,
        close_candidate_count: matches.length,
        close_candidates: matches.slice(0, 6).map((item) => ({ score: item.score, descriptor: item.descriptor.slice(0, 220), path: getElementDomPath(item.el), rect: getRectSnapshot(item.el) })),
      },
    });
    if (matches[0]) return clickElementReliably(matches[0].el);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", keyCode: 27, which: 27, bubbles: true, cancelable: true }));
    return false;
  }

  async function closeExistingResumePreview(candidateId = "", signature = "") {
    const beforeFingerprint = getResumePreviewFingerprint();
    if (!beforeFingerprint) return { closed: true, before_fingerprint: "", force_removed: 0 };
    clickResumePreviewCloseButton(candidateId, signature);
    let gone = await waitUntilResumePreviewGone(1800);
    let forceRemoved = 0;
    if (!gone) {
      forceRemoved = forceRemoveStalePdfPreviewFrames(signature);
      if (forceRemoved > 0) {
        gone = await waitUntilResumePreviewGone(600);
      }
    }
    const remainingPreview = findResumePreview();
    emit({ type: gone ? "stale_resume_preview_closed" : "stale_resume_preview_close_failed", data: { candidate_id: candidateId, candidate_signature: signature, before_fingerprint: beforeFingerprint, force_removed: forceRemoved, remaining_preview: remainingPreview ? describePreviewComponent(remainingPreview.root) : null } });
    return { closed: gone, before_fingerprint: beforeFingerprint, force_removed: forceRemoved };
  }

  function forceRemoveStalePdfPreviewFrames(currentSignature = "") {
    const frames = Array.from(document.querySelectorAll("iframe, object, embed"));
    let removed = 0;
    for (const el of frames) {
      try {
        const src = el.getAttribute?.("src") || el.getAttribute?.("data") || "";
        if (!src || !isStrongBossPdfPreviewUrl(src)) continue;
        const resourceId = extractBossPdfResourceId(src);
        const ownerSig = resourceId ? resourceIdOwnerSignature(resourceId) : "";
        // 旧 iframe = 指纹已被记录给上一位（不是当前候选人）
        const isOwnedByOther = ownerSig && ownerSig !== currentSignature;
        const isOrphan = !ownerSig; // 没认领 + 没人正在处理 → 上一轮残留
        if (!isOwnedByOther && !isOrphan) continue;
        const popupHost = el.closest?.(".popover, .modal, .ant-modal, .ant-modal-root, .resume-preview, .resume-preview-modal, [class*='preview']") || null;
        const target = popupHost && popupHost !== document.body ? popupHost : el;
        target.remove();
        removed += 1;
        emit({ type: "stale_pdf_preview_frame_removed", data: { candidate_signature: currentSignature, resource_id: resourceId, owner_signature: ownerSig, removed_target_tag: target.tagName || "", src } });
      } catch (err) {
        emit({ type: "stale_pdf_preview_frame_remove_error", data: { candidate_signature: currentSignature, error: String(err && err.message || err) } });
      }
    }
    return removed;
  }

  function resourceIdOwnerSignature(resourceId) {
    if (!resourceId) return "";
    for (const [sig, recorded] of candidateResourceIdMap.entries()) {
      if (recorded === resourceId) return sig;
    }
    return "";
  }

  function isPreviewLikelyCurrentCandidate(preview, signature = "", info = {}) {
    if (!preview) return false;
    const previewInfo = preview.info || extractResumePreviewInfo(preview.root, info);
    const root = preview.root;
    const tag = root?.tagName || "";
    const isFrame = /IFRAME|OBJECT|EMBED/.test(tag);
    const innerText = `${textOf(root).slice(0, 500)}`.trim();
    if (isFrame && !innerText) {
      // 跨域 PDF iframe 拿不到正文，单靠 fallbackInfo 推断的 name 就是自证循环。
      // 此时必须用 iframe 的资源 ID 是否与该候选人首次看到的 ID 一致来判断。
      const src = root.getAttribute?.("src") || root.getAttribute?.("data") || "";
      const resourceId = extractBossPdfResourceId(src);
      if (!resourceId) return false;
      const recordedId = candidateResourceIdMap.get(signature) || "";
      if (recordedId && recordedId === resourceId) return true;
      // 当前候选人首见的资源 ID 不在跟踪表里；如果该 ID 已被其他候选人认领，绝不可复用
      for (const [otherSig, otherId] of candidateResourceIdMap.entries()) {
        if (otherSig !== signature && otherId === resourceId) return false;
      }
      // 既未认领也无冲突 — 留给上层逻辑写入 candidateResourceIdMap
      return false;
    }
    const expectedName = `${info.name || signature.split("/")[0] || ""}`.replace(/先生|女士/g, "").trim();
    const expectedAge = `${info.age || signature.split("/")[1] || ""}`.replace(/岁/g, "").trim();
    const previewName = `${previewInfo.name || ""}`.replace(/先生|女士/g, "").trim();
    const previewAge = `${previewInfo.age || ""}`.replace(/岁/g, "").trim();
    const sample = `${previewInfo.text_sample || ""} ${innerText}`;
    const nameMatched = Boolean(expectedName && (previewName.includes(expectedName) || expectedName.includes(previewName) || sample.includes(expectedName)));
    const ageMatched = Boolean(expectedAge && (previewAge.includes(expectedAge) || sample.includes(`${expectedAge}岁`) || sample.includes(expectedAge)));
    return nameMatched && (!expectedAge || ageMatched);
  }

  async function waitForFreshResumePreview(candidateId = "", signature = "", info = {}, beforeFingerprint = "", timeoutMs = 4500) {
    const deadline = Date.now() + timeoutMs;
    let staleSeen = false;
    let lastStalePreview = null;
    while (Date.now() < deadline) {
      if (shouldAbortAsyncStep()) return null;
      const preview = await waitForResumePreview(candidateId, signature, info, Math.min(800, Math.max(200, deadline - Date.now())));
      if (!preview) continue;
      const currentFingerprint = getResumePreviewFingerprint(preview);
      if (!beforeFingerprint || currentFingerprint !== beforeFingerprint) return preview;
      staleSeen = true;
      lastStalePreview = preview;
      const matchedCurrent = isPreviewLikelyCurrentCandidate(preview, signature, info);
      emit({ type: "stale_resume_preview_ignored", data: { candidate_id: candidateId, candidate_signature: signature, preview_fingerprint: currentFingerprint, matched_current_candidate: matchedCurrent, preview_info: preview.info || {} } });
      if (matchedCurrent) {
        emit({ type: "stale_resume_preview_reused_for_current_candidate", data: { candidate_id: candidateId, candidate_signature: signature, preview_fingerprint: currentFingerprint, ...(preview.info || {}) } });
        return preview;
      }
      await sleep(250);
    }
    if (staleSeen) emit({ type: "stale_resume_preview_detected", data: { candidate_id: candidateId, candidate_signature: signature, reason: "preview_fingerprint_not_changed", matched_current_candidate: isPreviewLikelyCurrentCandidate(lastStalePreview, signature, info), preview_info: lastStalePreview?.info || {} } });
    return null;
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

  async function waitForResumePreview(candidateId = "", signature = "", info = {}, timeoutMs = 3000) {
    const deadline = Date.now() + timeoutMs;
    let lastSample = "";
    let scanLogged = false;
    while (Date.now() < deadline) {
      if (shouldAbortAsyncStep()) {
        return null;
      }
      const preview = findResumePreview(info, scanLogged ? null : { candidateId, signature });
      scanLogged = true;
      if (preview) {
        emit({ type: "resume_preview_wait_result", data: { candidate_id: candidateId, candidate_signature: signature, found: true, stage: "wait_found", elapsed_ms: timeoutMs - Math.max(0, deadline - Date.now()) } });
        return preview;
      }
      lastSample = (document.body?.innerText || "").replace(/\s+/g, " ").slice(0, 260) || lastSample;
      await sleep(250);
    }
    const weakPreview = makeResumePreviewFromLargestRoot(info);
    if (weakPreview) {
      emit({ type: "resume_preview_weak_candidate_used", data: { candidate_id: candidateId, candidate_signature: signature, ...describePreviewComponent(weakPreview.root), ...(weakPreview.info || {}) } });
      return weakPreview;
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

  function emitManualDownloadIntent(candidateId, signature, info, downloadRequestId) {
    emit({ type: "download_intent", data: {
      candidate_id: candidateId,
      candidate_signature: signature,
      candidate_info: info,
      expected_filename: `${signature}.pdf`,
      click_strategy: "manual_user_click",
      download_request_id: downloadRequestId,
    } });
  }

  async function learnManualDownloadClickAfterFailure(candidateId, signature, info, reason = "download_failed") {
    if (resumePreviewLearnState.waitingManualClick || shouldAbortAsyncStep()) return false;
    state = "paused";
    resumePreviewLearnState.learningStage = "manual_download_after_failure";
    emitCritical({
      type: "manual_download_learning_required",
      data: {
        candidate_id: candidateId,
        candidate_signature: signature,
        reason,
        message: "无法触发下载按钮，请你手动点击下载按钮供系统分析学习。",
      },
    });
    emit({ type: "manual_download_recording_started", data: { candidate_id: candidateId, candidate_signature: signature, reason } });
    const downloadRequestId = makeDownloadRequestId(candidateId, signature);
    emitManualDownloadIntent(candidateId, signature, info, downloadRequestId);
    const resultPromise = waitForDownloadResult(downloadRequestId, 90000);
    const snapshot = await captureNextManualDownloadClick(candidateId, signature, 90000);
    if (!snapshot) {
      state = "collecting";
      return false;
    }
    const downloadResult = await resultPromise;
    state = "collecting";
    if (downloadResult.ok) {
      const learned = { ...snapshot, download_confirmed: true, download_url: findDownloadUrlFromResult(downloadResult), download_data: downloadResult.data || {} };
      saveLearnedDownloadClick(learned);
      resumePreviewLearnState.learningStage = "learned";
      try { localStorage.setItem(STORAGE_KEYS.learningStage, "learned"); } catch {}
      emit({ type: "manual_download_learning_success", data: { ...learned, candidate_id: candidateId, candidate_signature: signature } });
      await finalizeDownloadWithPersistAck(candidateId, signature, downloadRequestId, "manual_user_click");
      return true;
    }
    emit({ type: "manual_download_learning_failed", data: { candidate_id: candidateId, candidate_signature: signature, reason: downloadResult.reason || "manual_click_no_download_event", ...(downloadResult.data || {}) } });
    return false;
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

  function isInExcludedBossDownloadArea(el) {
    const descriptor = `${getElementDescriptor(el)} ${getElementDescriptor(el?.parentElement)} ${getElementDescriptor(el?.parentElement?.parentElement)}`;
    return Boolean(
      el?.closest?.("[class*='rightbar'], [class*='rightbar-container'], [class*='rightbar-item'], [class*='add-to-label'], [class*='sidebar']") ||
      /rightbar|rightbar-container|rightbar-item|add-to-label|side-?bar|sidebar|打标签|备注|收藏|举报|屏蔽/i.test(descriptor)
    );
  }

  function isStrictResumeActionArea(el, preview = null) {
    if (!el || isInExcludedBossDownloadArea(el)) return false;
    const actionArea = el.closest?.("[class*='attachment-resume-btns'], [class*='resume-footer'], [class*='resume-detail'], [class*='resume-content']");
    if (!actionArea) return false;
    if (preview?.root && !(preview.root.contains(el) || actionArea.contains(preview.root) || preview.root.contains(actionArea))) return false;
    const descriptor = `${getElementDescriptor(actionArea)} ${textOf(actionArea).slice(0, 220)}`;
    return /简历|附件|resume|attachment|download|下载/i.test(descriptor);
  }

  function isInResumeDownloadArea(el, preview = null) {
    if (!el) return false;
    if (isInExcludedBossDownloadArea(el)) return false;
    const rect = el.getBoundingClientRect?.();
    if (!rect || rect.width < 10 || rect.height < 10 || rect.width > 180 || rect.height > 120) return false;
    if (rect.top < 0 || rect.left < window.innerWidth * 0.25) return false;
    if (isStrictResumeActionArea(el, preview)) return true;
    const resumeArea = el.closest?.("[class*='resume-content'], [class*='resume-detail'], [class*='resume-footer'], [class*='attachment-resume'], [class*='icon-content'], [class*='preview'], [class*='viewer'], [class*='pdf'], [class*='dialog'], [class*='modal'], [class*='drawer'], [class*='popup'], [class*='pop'], [role='dialog']");
    if (!resumeArea) return false;
    if (preview?.root && !(preview.root.contains(el) || resumeArea.contains(preview.root) || preview.root.contains(resumeArea))) return false;
    const areaDescriptor = `${getElementDescriptor(resumeArea)} ${textOf(resumeArea).slice(0, 260)}`;
    return /简历|附件|resume|attachment|preview|viewer|pdf|download|下载/i.test(areaDescriptor);
  }

  function getDownloadClickableNode(el) {
    if (!el) return null;
    const node = el.closest?.("button, a, [role='button'], [class*='icon-content'], [class*='download'], [class*='btn'], [class*='popover']") || el.parentElement || el;
    if (!node || isInExcludedBossDownloadArea(node)) return null;
    const rect = node.getBoundingClientRect?.();
    if (!rect || rect.width < 10 || rect.height < 10 || rect.width > 180 || rect.height > 120) return null;
    if (/page-content|chat-box|chat-container|chat-conversation|rightbar/i.test(getElementDescriptor(node))) return null;
    return node;
  }

  function getBossSvgDownloadSnapshot(el) {
    const node = getDownloadClickableNode(el) || el;
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
      if (!el || isInExcludedBossDownloadArea(el)) return;
      if (!roots.includes(el)) roots.push(el);
    };
    if (preview?.root) {
      pushRoot(preview.root);
      pushRoot(preview.root.parentElement);
      pushRoot(preview.root.parentElement?.parentElement);
      pushRoot(preview.root.closest?.("[role='dialog'], [class*='dialog'], [class*='modal'], [class*='preview'], [class*='viewer'], [class*='pdf'], [class*='resume'], [class*='attachment'], [class*='drawer'], [class*='popup'], [class*='pop'], [class*='layer']"));
    }
    for (const root of getPreviewRoots()) pushRoot(root);
    const matches = [];
    const selector = "[class*='attachment-resume-btns'] svg, [class*='attachment-resume-btns'] use, [class*='resume-footer'] svg, [class*='resume-footer'] use, [class*='resume-detail'] [class*='boss-svg'], [class*='resume-detail'] [class*='svg-icon']";
    for (const root of roots) {
      const nodes = root.matches?.(selector) ? [root, ...root.querySelectorAll(selector)] : Array.from(root.querySelectorAll?.(selector) || []);
      for (const el of nodes) {
        if (isInExcludedBossDownloadArea(el)) continue;
        const clickable = getDownloadClickableNode(el);
        if (!clickable || !isVisible(clickable) || isDisabled(clickable)) continue;
        const elHref = el.getAttribute?.("href") || el.getAttribute?.("xlink:href") || "";
        const isXlinkDownload = /download/i.test(elHref) && !!el.closest?.("[class*='attachment-resume-btns']");
        if (!isXlinkDownload && !isStrictResumeActionArea(clickable, preview) && !isStrictResumeActionArea(el, preview)) continue;
        const rect = clickable.getBoundingClientRect();
        if (rect.width < 10 || rect.height < 10 || rect.top < 0 || rect.left < 0) continue;
        const descriptor = `${getElementDescriptor(el)} ${getElementDescriptor(clickable)} ${getElementDescriptor(clickable.parentElement)}`;
        const combined = descriptor.toLowerCase();
        if (!isXlinkDownload && !isBossSvgDownloadDescriptor(descriptor)) continue;
        if (/关闭|close|取消|返回|back|delete|trash|更多|more|打印|print|zoom|放大|缩小|rotate|旋转|×|✕|esc/i.test(combined)) continue;
        let score = 40;
        if (clickable.closest?.("[class*='attachment-resume-btns']")) score += 22;
        if (clickable.closest?.("[class*='resume-footer']")) score += 14;
        if (clickable.closest?.("[class*='icon-content']")) score += 8;
        if (/下载|download|down/i.test(descriptor)) score += 10;
        if (rect.left > window.innerWidth * 0.55) score += 3;
        if (rect.left > window.innerWidth * 0.75) score += 2;
        const finalTarget = isXlinkDownload ? (el.closest?.("span") || clickable) : clickable;
        matches.push({ el: finalTarget, rect: finalTarget.getBoundingClientRect(), score, text: descriptor.slice(0, 160), descriptor });
      }
    }
    matches.sort((a, b) => b.score - a.score || b.rect.left - a.rect.left || a.rect.top - b.rect.top);
    if (matches.length) {
      emit({
        type: "download_button_candidates_detailed",
        data: {
          candidate_id: candidateId,
          candidate_signature: signature,
          candidates: matches.slice(0, 8).map((item) => ({
            score: item.score,
            text: item.text,
            descriptor: (item.descriptor || item.text || "").slice(0, 220),
            path: getElementDomPath(item.el),
            rect: getRectSnapshot(item.el),
            svg_hints: getElementSvgHints(item.el).slice(0, 5),
          })),
        },
      });
    }
    return matches[0]?.el || null;
  }

  function tryVueDirectDownload(target) {
    try {
      const iconDiv = target?.closest?.("[class*='icon-content']") || target?.parentElement?.closest?.("[class*='icon-content']");
      const vm = iconDiv?.__vue__;
      const href = vm?.$parent?.href || vm?.$parent?.$props?.href || "";
      if (!href || !/^https?:\/\//i.test(href)) return null;
      const a = document.createElement("a");
      a.href = href;
      a.download = "";
      a.style.display = "none";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      return href;
    } catch (e) { return null; }
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

  function findDownloadButton(candidateId = "", signature = "") {
    const matches = [];
    const roots = getPreviewRoots();
    const selector = "button, a, [role='button'], [class*='btn'], [class*='icon-content'], [class*='download'], [class*='toolbar'], span, i, svg, use";
    for (const root of roots) {
      if (isInExcludedBossDownloadArea(root)) continue;
      for (const el of root.querySelectorAll(selector)) {
        if (isInExcludedBossDownloadArea(el)) continue;
        const clickable = getDownloadClickableNode(el);
        if (!clickable || !isVisible(clickable) || isDisabled(clickable)) continue;
        if (!isInResumeDownloadArea(clickable) && !isInResumeDownloadArea(el)) continue;
        const rect = clickable.getBoundingClientRect();
        if (rect.width < 10 || rect.height < 10 || rect.width > 180 || rect.height > 120 || rect.top < 0 || rect.left < 0) continue;
        const descriptor = `${getElementDescriptor(el)} ${getElementDescriptor(clickable)} ${getElementDescriptor(clickable.parentElement)}`;
        const combined = descriptor.toLowerCase();
        let score = 0;
        if (isLikelyDownloadIcon(el, descriptor)) score += 12;
        if (isBossSvgDownloadDescriptor(descriptor)) score += 18;
        if (/下载附件|下载简历/.test(descriptor)) score += 12;
        if (/下载|download|down/i.test(descriptor)) score += 8;
        if (clickable.closest?.("[class*='attachment-resume-btns']")) score += 16;
        if (clickable.closest?.("[class*='resume-footer']")) score += 10;
        if (clickable.closest?.("[class*='icon-content']")) score += 6;
        if (/svg|icon|btn|button|toolbar/i.test(descriptor)) score += 2;
        if (rect.top <= Math.min(260, window.innerHeight * 0.35)) score += 4;
        if (rect.left > window.innerWidth * 0.55) score += 3;
        if (rect.left > window.innerWidth * 0.75) score += 2;
        if (rect.width <= 80 && rect.height <= 80) score += 2;
        if (/关闭|close|取消|返回|back|delete|trash|更多|more|打印|print|zoom|放大|缩小|rotate|旋转|×|✕|esc/i.test(combined)) score -= 18;
        if (score <= 0) continue;
        matches.push({ el: clickable, rect, score, text: descriptor.slice(0, 160), descriptor });
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
        const clickable = getDownloadClickableNode(el);
        if (!clickable || !isVisible(clickable) || isDisabled(clickable)) continue;
        if (isInExcludedBossDownloadArea(clickable) || !isInResumeDownloadArea(clickable)) continue;
        const rect = clickable.getBoundingClientRect();
        const descriptor = getElementDescriptor(clickable);
        if (/关闭|close|取消|返回|back|×|✕/i.test(descriptor)) continue;
        matches.push({ el: clickable, rect, score: /下载|download|down/i.test(descriptor) ? 8 : 3, text: `point:${point.x},${point.y} ${descriptor}`.slice(0, 160), descriptor });
      }
    }

    matches.sort((a, b) => b.score - a.score || b.rect.left - a.rect.left || a.rect.top - b.rect.top);
    if (matches.length) {
      emit({
        type: "download_button_candidates_detailed",
        data: {
          candidate_id: candidateId,
          candidate_signature: signature,
          candidates: matches.slice(0, 8).map((item) => ({
            score: item.score,
            text: item.text,
            descriptor: (item.descriptor || item.text || "").slice(0, 220),
            path: getElementDomPath(item.el),
            rect: getRectSnapshot(item.el),
            svg_hints: getElementSvgHints(item.el).slice(0, 5),
          })),
        },
      });
    }
    return matches[0]?.el || null;
  }

  async function waitForDownloadButton(candidateId = "", signature = "", timeoutMs = 10000) {
    const deadline = Date.now() + timeoutMs;
    let lastCandidateText = "";
    let frameInfo = "";
    while (Date.now() < deadline) {
      const btn = findDownloadButton(candidateId, signature);
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

  function makeDownloadRequestId(candidateId = "", signature = "") {
    return `${activeRunId || "run"}|${candidateId}|${signature}|${Date.now()}|${Math.random().toString(16).slice(2)}`;
  }

  function waitForDownloadResult(downloadRequestId, timeoutMs = 15000) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        pendingDownloadWaiters.delete(downloadRequestId);
        resolve({ ok: false, reason: "download_timeout" });
      }, timeoutMs);
      pendingDownloadWaiters.set(downloadRequestId, (result) => {
        clearTimeout(timer);
        pendingDownloadWaiters.delete(downloadRequestId);
        resolve(result);
      });
    });
  }

  function waitForPersistAck(downloadRequestId, candidateSignature, timeoutMs = 10000) {
    const primaryKey = downloadRequestId || candidateSignature;
    const fallbackKey = candidateSignature;
    return new Promise((resolve) => {
      const cleanup = () => {
        if (primaryKey) pendingPersistAcks.delete(primaryKey);
        if (fallbackKey && fallbackKey !== primaryKey) pendingPersistAcks.delete(fallbackKey);
      };
      const timer = setTimeout(() => {
        cleanup();
        resolve({ ok: false, status: "persist_ack_timeout", reason: "ack 等待超时" });
      }, timeoutMs);
      const settler = (result) => {
        clearTimeout(timer);
        cleanup();
        resolve(result);
      };
      if (primaryKey) pendingPersistAcks.set(primaryKey, settler);
      if (fallbackKey && fallbackKey !== primaryKey) pendingPersistAcks.set(fallbackKey, settler);
    });
  }

  async function finalizeDownloadWithPersistAck(candidateId, signature, downloadRequestId, strategy) {
    const persist = await waitForPersistAck(downloadRequestId, signature, 12000);
    if (persist.ok) {
      results.completed++;
      emit({ type: "resume_persist_confirmed", data: { candidate_id: candidateId, candidate_signature: signature, download_request_id: downloadRequestId, strategy, ...(persist.data || {}) } });
      emitProgress();
      await sleep(Math.min(Math.max(config.interval_ms || 0, 300), 900));
      return true;
    }
    emit({ type: "resume_persist_rejected", data: { candidate_id: candidateId, candidate_signature: signature, download_request_id: downloadRequestId, strategy, status: persist.status || "unknown", reason: persist.reason || "", ...(persist.data || {}) } });
    return false;
  }

  async function skipCandidate(candidateId, signature, reason, extra = {}) {
    emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason, ...extra } });
    results.skipped++;
    emitProgress();
    const delay = extra.fast_skip ? 20 : Math.min(Math.max(config.interval_ms || 0, 300), 900);
    await sleep(delay);
  }

  async function requestResumeAndSkip(btn, candidateId, signature) {
    const beforeCount = getResumeRequestSentCount();
    clickElementOnce(btn.el);
    const confirmed = await confirmRequestIfNeeded(candidateId, signature);
    const requestSent = confirmed ? await waitForResumeRequestSent(3000, beforeCount) : await waitForResumeRequestSent(1200, beforeCount);
    const payload = { candidate_id: candidateId, candidate_signature: signature, confirmed, request_sent: requestSent };
    if (requestSent) {
      emit({ type: "resume_request_success", data: payload });
      await skipCandidate(candidateId, signature, "resume_requested_by_user", { confirmed, request_sent: true, fast_skip: true });
      return;
    }
    emit({ type: "resume_request_unconfirmed", data: payload });
    await skipCandidate(candidateId, signature, confirmed ? "resume_request_unconfirmed" : "resume_request_confirm_not_found", { confirmed, request_sent: false, fast_skip: true });
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
    const downloadRequestId = makeDownloadRequestId(candidateId, signature);
    const payload = { candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, url, iframe_src: resolved.raw_src || url, normalized_src: resolved.normalized_src || "", viewer_url: resolved.viewer_url || "", extracted_src: resolved.extracted_src || "", direct_url: url, download_request_id: downloadRequestId };
    emit({ type: "direct_iframe_download_start", data: payload });
    const resultPromise = waitForDownloadResult(downloadRequestId, 20000);
    const started = await downloadDirectUrl(payload);
    if (!started.ok) {
      emit({ type: "direct_iframe_download_failed", data: { ...payload, reason: started.reason || "direct_download_start_failed" } });
      return false;
    }
    emit({ type: "direct_iframe_download_created", data: { ...payload, download_id: started.download_id || "" } });
    const downloadResult = await resultPromise;
    if (downloadResult.ok) {
      emit({ type: "direct_iframe_download_link_captured", data: { ...payload, download_url: findDownloadUrlFromResult(downloadResult), ...(downloadResult.data || {}) } });
      const accepted = await finalizeDownloadWithPersistAck(candidateId, signature, downloadRequestId, "pdf_iframe_direct");
      return accepted;
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
    emit({ type: "boss_svg_download_icon_found", data: { candidate_id: candidateId, candidate_signature: signature, ...snapshot, diagnostics: getDownloadClickDiagnostics(target, "boss_svg_before_click") } });
    const downloadRequestId = makeDownloadRequestId(candidateId, signature);
    emit({ type: "download_intent", data: { candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, click_strategy: "boss_svg_icon", download_request_id: downloadRequestId } });
    const resultPromise = waitForDownloadResult(downloadRequestId, 20000);
    const vueUrl = tryVueDirectDownload(target);
    if (!vueUrl) clickElementReliably(target);
    emit({ type: "boss_svg_download_icon_clicked", data: { candidate_id: candidateId, candidate_signature: signature, ...snapshot, diagnostics: getDownloadClickDiagnostics(target, "boss_svg_after_click") } });
    await sleep(1000);
    emit({ type: "download_click_post_diagnostics", data: { candidate_id: candidateId, candidate_signature: signature, click_strategy: "boss_svg_icon", diagnostics: getDownloadClickDiagnostics(target, "boss_svg_1s_after_click") } });
    const downloadResult = await resultPromise;
    if (downloadResult.ok) {
      emit({ type: "boss_svg_download_link_captured", data: { candidate_id: candidateId, candidate_signature: signature, download_url: findDownloadUrlFromResult(downloadResult), ...downloadResult.data } });
      const accepted = await finalizeDownloadWithPersistAck(candidateId, signature, downloadRequestId, "boss_svg_icon");
      return accepted;
    }
    emit({ type: "boss_svg_download_link_capture_failed", data: { candidate_id: candidateId, candidate_signature: signature, reason: downloadResult.reason || "download_failed", ...(downloadResult.data || {}) } });
    if (await learnManualDownloadClickAfterFailure(candidateId, signature, info, downloadResult.reason || "boss_svg_download_failed")) {
      return true;
    }
    return false;
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
    const downloadRequestId = makeDownloadRequestId(candidateId, signature);
    emit({ type: "download_intent", data: { candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, download_request_id: downloadRequestId } });
    const resultPromise = waitForDownloadResult(downloadRequestId);
    clickElementReliably(target);
    const downloadResult = await resultPromise;
    if (downloadResult.ok) {
      const accepted = await finalizeDownloadWithPersistAck(candidateId, signature, downloadRequestId, "learned_click");
      return accepted;
    }
    if (await learnManualDownloadClickAfterFailure(candidateId, signature, info, downloadResult.reason || "learned_download_failed")) {
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
    emit({ type: "collect_finished", data: { total_completed: results.completed, total_skipped: results.skipped, learning_finished: true } });
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

  async function startResumePreviewRecognition(candidateId, signature, info, btn, beforeUrl, stalePreviewClosed, beforePreviewFingerprint = "") {
    emitAttachmentDebug("01_enter_after_attachment_click", candidateId, signature, {
      button_state: btn.state,
      button_text: btn.text,
      before_url: beforeUrl,
      after_click_url: location.href,
      stale_preview_closed: stalePreviewClosed,
      before_preview_fingerprint: beforePreviewFingerprint,
      state,
    });
    if (shouldAbortAsyncStep()) {
      emitAttachmentDebug("02_abort_before_preview_wait", candidateId, signature, { state });
      return null;
    }
    emitAttachmentDebug("02_call_wait_for_fresh_resume_preview", candidateId, signature, { timeout_ms: 4500 });
    const preview = await waitForFreshResumePreview(candidateId, signature, info, beforePreviewFingerprint, 4500);
    emitAttachmentDebug(preview ? "03_wait_for_fresh_resume_preview_return_found" : "03_wait_for_fresh_resume_preview_return_null", candidateId, signature, {
      found: Boolean(preview),
      preview_score: preview?.score || 0,
      preview_rect: preview?.root ? getRectSnapshot(preview.root) : null,
      preview_descriptor: preview?.root ? getElementDescriptor(preview.root).slice(0, 180) : "",
      preview_fingerprint: preview ? getResumePreviewFingerprint(preview) : "",
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

    emit({ type: "auto_download_click_used", data: { candidate_id: candidateId, candidate_signature: signature, ...getElementSnapshot(downloadButton), diagnostics: getDownloadClickDiagnostics(downloadButton, "auto_before_click") } });
    const downloadRequestId = makeDownloadRequestId(candidateId, signature);
    emit({ type: "download_intent", data: { candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, download_request_id: downloadRequestId } });
    const resultPromise = waitForDownloadResult(downloadRequestId, 20000);
    clickElementReliably(downloadButton);
    await sleep(1000);
    emit({ type: "download_click_post_diagnostics", data: { candidate_id: candidateId, candidate_signature: signature, click_strategy: "auto_download_button", diagnostics: getDownloadClickDiagnostics(downloadButton, "auto_1s_after_click") } });
    const downloadResult = await resultPromise;
    if (downloadResult.ok) {
      await finalizeDownloadWithPersistAck(candidateId, signature, downloadRequestId, "auto_download_button");
    } else if (await learnManualDownloadClickAfterFailure(candidateId, signature, info, downloadResult.reason || "auto_download_failed")) {
      return;
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

    for (let i = results.currentIndex; i < items.length && results.completed < config.max_resumes; i++) {
      if (state === "stopped") break;
      await waitForPause();
      if (state === "stopped") break;

      results.currentIndex = i;
      const item = items[i];

      const candidateStepStartedAt = Date.now();
      try {
        item.scrollIntoView({ block: "center" });
        await sleep(80);
        item.click();
        await sleep(450);
      } catch (error) {
        emit({ type: "candidate_skipped", data: { candidate_signature: `index_${i}`, reason: "click_failed", error: String(error), elapsed_ms: Date.now() - candidateStepStartedAt } });
        results.skipped++;
        emitProgress();
        continue;
      }

      const infoStartedAt = Date.now();
      const info = await waitForContactInfo(item, 2200);
      const infoElapsedMs = Date.now() - infoStartedAt;
      const signature = `${info.name}/${info.age}/${info.education}`;
      const candidateId = `${activeRunId || "run"}_${i}_${signature}`;

      emit({ type: "candidate_clicked", data: { ...info, candidate_id: candidateId, candidate_signature: signature, index: i, elapsed_ms: infoElapsedMs } });

      if (signature === "待识别/待识别/待识别") {
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "candidate_info_unrecognized", raw_text: info.raw_text || "" } });
        results.skipped++;
        emitProgress();
        state = "idle";
        break;
      }

      if (seenSignatures.has(signature)) {
        await skipCandidate(candidateId, signature, "duplicate_in_run", { fast_skip: true });
        continue;
      }
      seenSignatures.add(signature);

      const dedupStartedAt = Date.now();
      const candidateKey = await buildBossCandidateKey(signature, info);
      const candidateSignatures = new Set(Array.isArray(config.boss_candidate_signatures) ? config.boss_candidate_signatures : []);
      const normalizedSignature = normalizeBossCandidateSignature(signature);
      const rawSignatureHit = candidateSignatures.has(signature);
      const normalizedSignatureHit = candidateSignatures.has(normalizedSignature);
      const keyHit = Boolean(candidateKey && Array.isArray(config.boss_candidate_keys) && config.boss_candidate_keys.includes(candidateKey));
      emit({
        type: "boss_pre_dedup_checked",
        data: {
          candidate_id: candidateId,
          candidate_signature: signature,
          normalized_signature: normalizedSignature,
          candidate_key: candidateKey,
          key_count: Array.isArray(config.boss_candidate_keys) ? config.boss_candidate_keys.length : 0,
          signature_count: candidateSignatures.size,
          key_hit: keyHit,
          signature_hit: rawSignatureHit || normalizedSignatureHit,
          elapsed_ms: Date.now() - dedupStartedAt,
          content_script_version: CONTENT_SCRIPT_VERSION,
        },
      });
      if (keyHit || rawSignatureHit || normalizedSignatureHit) {
        await skipCandidate(candidateId, signature, "boss_dedup_hit", { fast_skip: true, candidate_key: candidateKey, normalized_signature: normalizedSignature });
        continue;
      }

      const resumeButtonLookupStartedAt = Date.now();
      emit({ type: "boss_resume_button_lookup_started", data: { candidate_id: candidateId, candidate_signature: signature } });
      const btn = findResumeButton();
      if (!btn) {
        await skipCandidate(candidateId, signature, "no_resume_button", { fast_skip: true, elapsed_ms: Date.now() - resumeButtonLookupStartedAt });
        continue;
      }

      emit({ type: "resume_button_found", data: { candidate_id: candidateId, candidate_signature: signature, button_state: btn.state, button_state_label: btn.state_label, button_text: btn.text, elapsed_ms: Date.now() - resumeButtonLookupStartedAt } });

      if (btn.state === "dim") {
        if (hasResumeRequestSent(getChatDetailRoot())) {
          await skipCandidate(candidateId, signature, "resume_request_already_sent", { fast_skip: true, button_state: btn.state, button_state_label: btn.state_label, button_text: btn.text });
        } else if (config.request_resume_if_missing) {
          await requestResumeAndSkip(btn, candidateId, signature);
        } else {
          await skipCandidate(candidateId, signature, "no_resume_attachment", { fast_skip: true, button_state: btn.state, button_state_label: btn.state_label, button_text: btn.text });
        }
        continue;
      }

      emitAttachmentDebug("00_resume_button_ready", candidateId, signature, {
        button_state: btn.state,
        button_state_label: btn.state_label,
        button_text: btn.text,
        button_rect: btn.el ? getRectSnapshot(btn.el) : null,
        button_descriptor: btn.el ? getElementDescriptor(btn.el).slice(0, 180) : "",
      });

      const beforeUrl = location.href;
      if (!guardResumeAttachmentClick(candidateId, signature)) {
        await skipCandidate(candidateId, signature, "resume_attachment_click_guarded", { fast_skip: true });
        continue;
      }
      const stalePreview = await closeExistingResumePreview(candidateId, signature);
      const beforePreviewFingerprint = stalePreview.before_fingerprint || getResumePreviewFingerprint();
      const btnRect = btn.el?.getBoundingClientRect?.();
      const btnCenterX = btnRect ? Math.round(btnRect.left + btnRect.width / 2) : null;
      const btnCenterY = btnRect ? Math.round(btnRect.top + btnRect.height / 2) : null;
      const attachmentClickSnapshot = getElementSnapshot(btn.el, btnCenterX, btnCenterY);
      const clickOk = clickElementReliably(btn.el);
      emit({ type: "resume_attachment_click_dispatched", data: {
        candidate_id: candidateId,
        candidate_signature: signature,
        click_ok: clickOk,
        button_state: btn.state,
        button_state_label: btn.state_label,
        button_text: btn.text,
        ...attachmentClickSnapshot,
      } });
      await sleep(350);
      const preview = await startResumePreviewRecognition(candidateId, signature, info, btn, beforeUrl, stalePreview.closed, beforePreviewFingerprint);
      if (shouldAbortAsyncStep()) break;
      if (!preview) {
        await skipCandidate(candidateId, signature, "resume_preview_not_found");
        continue;
      }

      await tryDownloadResume(candidateId, signature, info, preview, true);
    }

    state = "idle";
    if (!collectFinishedEmitted) {
      emit({ type: "collect_finished", data: { total_completed: results.completed, total_skipped: results.skipped } });
    }
    } finally {
      if (activeCollectLoopRunId === activeRunId) activeCollectLoopRunId = "";
    }
  }

  function emitProgress() {
    emit({
      type: "collect_progress",
      data: { total: results.completed + results.skipped, completed: results.completed, skipped: results.skipped, current_index: results.currentIndex, scanned_count: results.currentIndex + 1 },
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
        config = {
          ...config,
          ...msg.config,
          boss_candidate_keys: Array.isArray(msg.config?.boss_candidate_keys) ? msg.config.boss_candidate_keys : [],
          boss_candidate_signatures: Array.isArray(msg.config?.boss_candidate_signatures) ? msg.config.boss_candidate_signatures : [],
          boss_pre_dedup_ready: msg.config?.boss_pre_dedup_ready === true,
        };
        emit({
          type: "boss_content_script_collect_started",
          data: {
            content_script_version: CONTENT_SCRIPT_VERSION,
            key_count: config.boss_candidate_keys.length,
            signature_count: config.boss_candidate_signatures.length,
            pre_dedup_ready: config.boss_pre_dedup_ready,
          },
        });
        if (!config.boss_pre_dedup_ready) {
          state = "stopped";
          emitCritical({
            type: "error",
            data: {
              message: "BOSS 下载前去重数据未由后端确认下发，已阻止采集以避免重复下载；请重启后端服务后重试",
              content_script_version: CONTENT_SCRIPT_VERSION,
              key_count: config.boss_candidate_keys.length,
              signature_count: config.boss_candidate_signatures.length,
            },
          });
          break;
        }
        results = { downloaded: 0, skipped: 0, currentIndex: 0, completed: 0 };
        resumePreviewLearnState.learningStage = resumePreviewLearnState.learnedClick ? "learned" : "auto_download";
        resumePreviewLearnState.waitingManualClick = false;
        try {
          localStorage.setItem(STORAGE_KEYS.learningStage, resumePreviewLearnState.learningStage);
        } catch {}
        collectFinishedEmitted = false;
        pendingDownloadWaiters.forEach((resolve) => resolve({ ok: false, reason: "new_collect_started" }));
        pendingDownloadWaiters.clear();
        pendingPersistAcks.forEach((resolve) => resolve({ ok: false, status: "new_collect_started" }));
        pendingPersistAcks.clear();
        candidateResourceIdMap.clear();
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
        pendingPersistAcks.forEach((resolve) => resolve({ ok: false, status: "collect_stopped" }));
        pendingPersistAcks.clear();
        if (pauseResolve) { pauseResolve(); pauseResolve = null; }
        break;
      case "download_completed":
      case "download_failed": {
        const data = msg.data || {};
        const resolve = pendingDownloadWaiters.get(data.download_request_id);
        if (resolve && data.run_id === activeRunId) {
          resolve({ ok: msg.type === "download_completed", reason: data.reason, data });
        } else if (data.candidate_id && pendingDownloadWaiters.has(data.candidate_id)) {
          const legacyResolve = pendingDownloadWaiters.get(data.candidate_id);
          legacyResolve({ ok: false, reason: "download_request_id_missing_or_mismatch", data });
        }
        break;
      }
      case "resume_persist_ack": {
        const data = msg.data || {};
        const key = data.download_request_id || data.candidate_signature || "";
        const resolver = pendingPersistAcks.get(key);
        if (resolver) {
          pendingPersistAcks.delete(key);
          resolver({ ok: data.status === "saved", status: data.status || "unknown", reason: data.reason || "", data });
        }
        break;
      }
    }
    sendResponse({ ok: true });
    return true;
  });

  emitPageStatus("load");
})();
