(function () {
  "use strict";

  const CONTENT_SCRIPT_VERSION = "2.50.0";

  // 平台注册表：每个平台的 hostname、WS 端口、文本标记、localStorage key 一站式声明。
  // 这是从单平台升级到多平台的核心入口——新加平台只需在此对象增加一条配置。
  const PLATFORM_REGISTRY = {
    boss: {
      code: "boss",
      hostnames: ["www.zhipin.com"],
      ws_url: "ws://127.0.0.1:8765",
      auth_markers: ["沟通中", "新招呼", "联系人", "附件简历", "牛人", "聊天", "常用语", "发送", "交换微信", "打招呼"],
      page_markers: ["沟通", "聊天", "联系人", "牛人", "BOSS", "直聘", "发送", "常用语", "附件简历"],
      resume_view_text: ["查看附件简历", "查看简历附件", "下载附件简历", "下载简历附件"],
      resume_requested_text: ["已向对方要附件简历", "已索要附件简历", "等待对方上传", "简历请求已发送"],
      storage_keys: {
        learning_stage: "boss_resume_learning_stage",
        learned_click: "boss_resume_download_learned_click",
      },
    },
    qiancheng: {
      code: "qiancheng",
      hostnames: ["ehire.51job.com"],
      ws_url: "ws://127.0.0.1:8766",
      // 以下字段为占位值，阶段 3 学习模式跑通后再校准
      auth_markers: ["人才沟通", "应聘者", "招呼", "简历"],
      page_markers: ["前程无忧", "51job", "ehire", "招聘", "人才"],
      resume_view_text: [],
      resume_requested_text: [],
      storage_keys: {
        learning_stage: "qiancheng_resume_learning_stage",
        learned_click: "qiancheng_resume_download_learned_click",
        learned_candidate_card: "qiancheng_candidate_card_selector",
        learned_attachment_btn: "qiancheng_attachment_btn_selector",
        learned_preview_form: "qiancheng_preview_form_kind",
        learned_nav_menu_chat: "qiancheng_nav_menu_chat_selector",
        learned_tab_chatting: "qiancheng_tab_chatting_selector",
        learned_profile_info: "qiancheng_profile_info_container_selector",
        learned_close_preview: "qiancheng_close_preview_selector",
      },
    },
    zhilian: {
      code: "zhilian",
      hostnames: ["rd5.zhaopin.com", "rd6.zhaopin.com"],
      ws_url: "ws://127.0.0.1:8767",
      auth_markers: ["沟通中", "联系人", "职位管理", "人才推荐", "查看附件简历"],
      page_markers: ["智联招聘", "zhaopin", "招聘", "沟通", "候选人"],
      resume_view_text: ["查看附件简历", "查看简历附件", "下载附件简历"],
      resume_requested_text: ["已向对方要附件简历", "已要附件简历", "附件简历索要中"],
      storage_keys: {
        learning_stage: "zhilian_resume_learning_stage",
        learned_click: "zhilian_resume_download_learned_click",
      },
    },
  };

  function detectPlatform() {
    const host = location.hostname;
    for (const cfg of Object.values(PLATFORM_REGISTRY)) {
      if (cfg.hostnames.includes(host)) return cfg;
    }
    return null;
  }

  const PLATFORM = detectPlatform();
  if (!PLATFORM) {
    // manifest 宽匹配可能让 content.js 注入到非目标 hostname，静默退出
    return;
  }

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

  const RESUME_VIEW_TEXT = PLATFORM.resume_view_text;
  const RESUME_REQUESTED_TEXT = PLATFORM.resume_requested_text;


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
  const receivedPersistAcks = new Map();
  const persistAckCreditedRequests = new Set();
  const persistAckCreditedSignatures = new Set();
  const candidateResourceIdMap = new Map();
  const STORAGE_KEYS = {
    learningStage: PLATFORM.storage_keys.learning_stage,
    learnedClick: PLATFORM.storage_keys.learned_click,
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
    const precise = document.querySelector("div.user-list.b-scroll-stable") || document.querySelector("div.user-list");
    if (precise && isVisible(precise)) return precise;
    return LIST_CONTAINER_SELECTORS
      .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
      .filter((el) => isVisible(el) && isInLeftCandidateArea(el))
      .map((el) => ({ el, score: el.scrollHeight - el.clientHeight + el.querySelectorAll("li, [class*='item'], [class*='card'], [class*='user']").length * 20 }))
      .sort((a, b) => b.score - a.score)[0]?.el || null;
  }

  // 优先通过 boss-virtual-list 的 Vue API scrollToOffset 驱动虚拟列表滚动，
  // 比 scrollTop 更可靠，因为 user-list overflow:hidden 仅做布局，虚拟列表内部用 padding 模拟高度。
  function scrollBossCandidateList(dy) {
    const container = document.querySelector("div.user-list.b-scroll-stable") || document.querySelector("div.user-list");
    if (container) {
      const vue = container.__vue__;
      if (vue && typeof vue.scrollToOffset === "function") {
        const currentOffset = vue.getOffset?.() || container.scrollTop || 0;
        const newOffset = currentOffset + dy;
        vue.scrollToOffset(newOffset);
        container.dispatchEvent(new Event("scroll", { bubbles: true }));
        return { ok: true, container, mode: "vue_scrollToOffset", before: currentOffset, after: newOffset };
      }
      const before = container.scrollTop;
      container.scrollTop = before + dy;
      container.dispatchEvent(new Event("scroll", { bubbles: true }));
      if (container.scrollTop !== before) return { ok: true, container, mode: "user_list_scrollTop", before, after: container.scrollTop };
    }
    const items = getCandidateItems();
    const anchor = items.length ? items[items.length - 1] : null;
    if (anchor) {
      try { anchor.scrollIntoView({ block: "end" }); return { ok: true, container: anchor.parentElement || null, mode: "scroll_into_view_end", before: 0, after: 0 }; } catch {}
    }
    return { ok: false, container: null, mode: "no_container", before: 0, after: 0 };
  }

  async function clickBossChatMenu() {
    if (/\/web\/chat(\/|$|\?)/.test(location.pathname + location.search)) {
      emit({ type: "boss_chat_menu_skip", data: { reason: "already_on_chat", url: location.href } });
      return true;
    }
    for (let wave = 0; wave < 3; wave++) {
      const target = document.querySelector('dl.menu-chat a[href*="/web/chat"]');
      if (target && isVisible(target)) {
        try {
          clickElementDirect(target);
          emit({ type: "boss_chat_menu_clicked", data: { rect: target.getBoundingClientRect(), attempts: wave + 1 } });
          await sleep(800);
          return true;
        } catch (exc) {
          emit({ type: "boss_chat_menu_skip", data: { reason: "click_failed", error: String(exc), attempts: wave + 1 } });
          return false;
        }
      }
      await sleep(500);
    }
    emit({ type: "boss_chat_menu_skip", data: { reason: "not_found", url: location.href, attempts: 3 } });
    return false;
  }

  async function clickBossChattingTab() {
    for (let wave = 0; wave < 3; wave++) {
      const target = document.querySelector('.chat-label-item[title="沟通中"]');
      if (target && isVisible(target)) {
        const already = target.classList.contains("selected");
        if (already) {
          emit({ type: "boss_chatting_tab_clicked", data: { rect: target.getBoundingClientRect(), text: "沟通中", attempts: wave + 1, already_selected: true } });
          return true;
        }
        try {
          clickElementDirect(target);
          emit({ type: "boss_chatting_tab_clicked", data: { rect: target.getBoundingClientRect(), text: "沟通中", attempts: wave + 1 } });
          await sleep(800);
          return true;
        } catch (exc) {
          emit({ type: "boss_chatting_tab_skip", data: { reason: "click_failed", error: String(exc), attempts: wave + 1 } });
          return false;
        }
      }
      await sleep(600);
    }
    emit({ type: "boss_chatting_tab_skip", data: { reason: "not_found", url: location.href, attempts: 3 } });
    return false;
  }

  async function resetCandidateListScroll() {
    const container = document.querySelector("div.user-list.b-scroll-stable") || document.querySelector("div.user-list");
    const scrollList = [];
    if (container && isVisible(container)) {
      scrollList.push(container);
      const vue = container.__vue__;
      if (vue && typeof vue.scrollToOffset === "function") {
        vue.scrollToOffset(0);
      }
    }
    const before = scrollList.map((el) => ({
      tag: el.tagName,
      cls: String(el.className || "").slice(0, 80),
      prev: el.scrollTop,
      h: el.scrollHeight,
      ch: el.clientHeight,
    }));
    for (const el of scrollList) {
      try {
        el.scrollTop = 0;
        el.scrollTo?.({ top: 0, behavior: "auto" });
        el.dispatchEvent(new Event("scroll", { bubbles: true }));
      } catch (_) { /* ignore */ }
    }
    try { document.documentElement.scrollTop = 0; } catch (_) { /* ignore */ }
    try { document.body.scrollTop = 0; } catch (_) { /* ignore */ }
    window.scrollTo(0, 0);
    await sleep(900);
    const after = scrollList.map((el) => el.scrollTop);
    emit({
      type: "boss_diag",
      data: {
        step: "scroll_reset_detail",
        anchor_found: !!container,
        scrollable_count: scrollList.length,
        before,
        after,
        doc_scrollTop: document.documentElement.scrollTop,
        body_scrollTop: document.body?.scrollTop || 0,
      },
    });
  }

  function getCandidateItems() {
    const seen = new Set();
    const items = [];
    let hitContainerInfo = null;

    // 精确路径：直接从 div.user-list 取 .geek-item-wrap > .geek-item
    const userList = document.querySelector("div.user-list.b-scroll-stable") || document.querySelector("div.user-list");
    if (userList && isVisible(userList)) {
      const geekItems = userList.querySelectorAll(".geek-item-wrap > .geek-item");
      for (const el of geekItems) {
        if (seen.has(el)) continue;
        seen.add(el);
        if (!isVisible(el)) continue;
        items.push({ el, score: 5, top: el.getBoundingClientRect().top });
      }
      if (items.length > 0) {
        hitContainerInfo = {
          selector: "div.user-list .geek-item-wrap > .geek-item",
          tag: userList.tagName,
          cls: String(userList.className || "").slice(0, 100),
          rect: getRectSnapshot(userList),
          trusted: true,
        };
      }
    }

    // 回退：如果精确路径未命中，走旧的容器搜索
    if (items.length === 0) {
      const seenKeys = new Set();
      const containers = LIST_CONTAINER_SELECTORS
        .flatMap((selector) => Array.from(document.querySelectorAll(selector)).map((el) => ({ selector, el })))
        .filter(({ el }) => isVisible(el) && isInLeftCandidateArea(el));
      for (const { selector, el: container } of containers) {
        const before = items.length;
        const trustedNodes = Array.from(container.querySelectorAll(".geek-item-wrap"));
        if (trustedNodes.length > 0) {
          for (const el of trustedNodes) {
            if (seen.has(el)) continue;
            seen.add(el);
            if (!isVisible(el)) continue;
            items.push({ el, score: 5, top: el.getBoundingClientRect().top });
          }
        } else {
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
        }
        if (items.length > before) {
          hitContainerInfo = {
            selector,
            tag: container.tagName,
            cls: String(container.className || "").slice(0, 100),
            rect: getRectSnapshot(container),
            trusted: trustedNodes.length > 0,
          };
        }
        if (items.length > 0) break;
      }
    }

    let fallbackUsed = null;
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
        if (items.length > 0) {
          fallbackUsed = selector;
          break;
        }
      }
    }

    try {
      window.__bossLastCandidateScanDiag = {
        container: hitContainerInfo,
        fallback_selector: fallbackUsed,
        item_count: items.length,
      };
    } catch (_) { /* ignore */ }

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
      // 完全忠实采集：取年龄前最后一个匹配到的连续中文片段（最靠近年龄的）。
      // 不做职位词/无效名过滤——网站上看到什么就采集什么。
      if (nameMatches.length > 0) {
        name = nameMatches[nameMatches.length - 1];
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
    try {
      const bytes = new TextEncoder().encode(normalized);
      const digest = await Promise.race([
        crypto.subtle.digest("SHA-256", bytes),
        new Promise((_, reject) => setTimeout(() => reject(new Error("sha256_timeout")), 3000))
      ]);
      return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
    } catch (e) {
      let hash = 0;
      for (let i = 0; i < normalized.length; i++) {
        hash = ((hash << 5) - hash + normalized.charCodeAt(i)) | 0;
      }
      return Math.abs(hash).toString(16).padStart(8, "0");
    }
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
    // 完全忠实采集：原样返回页面文本，仅剥离明确的 UI 噪音（按钮文字 + 活跃状态）。
    // 不做任何"内容修正"——包括"先生/女士"优先匹配、地名/职业词剥离、
    // 名字长度检查、单字兜底等。如果用户在网站填写的就是看起来"奇怪"的姓名，
    // 也忠实保留——否则会导致同一候选人在不同读取时识别出不一致的姓名。
    return stripActivityText(text).trim();
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

  function isJobTitleContext(el) {
    const jobLabelRe = /沟通职位|期望职位|求职意向|应聘职位/;
    let node = el.parentElement;
    for (let i = 0; i < 3 && node && node !== document.body; i++) {
      if (node.className && /job|position|expect|intent/i.test(String(node.className))) return true;
      const directText = Array.from(node.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent).join("");
      if (jobLabelRe.test(directText)) return true;
      for (const sib of node.children) {
        if (sib === el || sib.contains(el)) continue;
        const sibText = sib.textContent || "";
        if (sibText.length < 20 && jobLabelRe.test(sibText)) return true;
      }
      node = node.parentElement;
    }
    return false;
  }

  function findAgeAnchorInRightPanel() {
    const AGE_RE = /\d{2}\s*\u5c81/;
    const pageWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (!node.textContent || !AGE_RE.test(node.textContent)) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    while (walker.nextNode()) {
      const el = walker.currentNode.parentElement;
      if (!el || !isVisible(el)) continue;
      const rect = el.getBoundingClientRect();
      if (rect.left < pageWidth * 0.28 || rect.top < 60 || rect.top > 200) continue;
      if (rect.height < 8 || rect.height > 50) continue;
      const text = (el.innerText || el.textContent || "").trim();
      if (text.length > 60) continue;
      return { el, rect, text };
    }
    return null;
  }

  function getInfoBarContainer(ageEl) {
    let node = ageEl;
    for (let i = 0; i < 6; i++) {
      const parent = node.parentElement;
      if (!parent || parent === document.body) break;
      const pRect = parent.getBoundingClientRect();
      if (pRect.height > 100 || pRect.width > 900) break;
      const pText = parent.innerText || "";
      if (/[|\uff5c]/.test(pText) && pRect.width > 150) return parent;
      node = parent;
    }
    return node;
  }

  function findLargestFontElement(container, centerY) {
    const candidates = [];
    const els = container.querySelectorAll("*");
    for (const el of els) {
      if (!isVisible(el)) continue;
      const rect = el.getBoundingClientRect();
      if (Math.abs((rect.top + rect.height / 2) - centerY) > 20) continue;
      if (rect.width < 10 || rect.width > 200 || rect.height < 10 || rect.height > 50) continue;
      const text = (el.innerText || "").trim();
      if (!text || text.length > 30 || /[|\uff5c]/.test(text)) continue;
      if (el.children.length > 2) continue;
      candidates.push({ el, rect, text });
    }
    let maxFs = 0, best = null;
    for (const c of candidates) {
      const fs = parseFloat(getComputedStyle(c.el).fontSize);
      if (fs > maxFs) { maxFs = fs; best = { ...c, fontSize: fs }; }
    }
    return best;
  }

  function hasGenderIndicator(nameEl) {
    const GENDER_TEXT = /[\u2642\u2640]|^[\u7537\u5973]$/;
    const GENDER_CLASS = /sex|gender|male|female|icon-man|icon-woman/i;
    const parent = nameEl.parentElement;
    if (!parent) return false;
    const nameRect = nameEl.getBoundingClientRect();
    for (const sib of parent.children) {
      if (sib === nameEl) continue;
      const sibRect = sib.getBoundingClientRect();
      if (sibRect.left < nameRect.right - 5 || sibRect.left > nameRect.right + 80) continue;
      const sibText = (sib.innerText || sib.textContent || "").trim();
      if (GENDER_TEXT.test(sibText) || GENDER_CLASS.test(sib.className || "")) return true;
      const icon = sib.querySelector("[class*='sex'], [class*='gender'], [class*='male'], [class*='female']");
      if (icon) return true;
    }
    const next = nameEl.nextElementSibling;
    if (next) {
      const nt = (next.innerText || next.textContent || "").trim();
      if (GENDER_TEXT.test(nt) || GENDER_CLASS.test(next.className || "")) return true;
    }
    return false;
  }

  function extractEducationFromPipes(container) {
    const EDU_RE = /\u535a\u58eb|\u7855\u58eb|\u7814\u7a76\u751f|\u672c\u79d1|\u5927\u4e13|\u4e13\u79d1|\u9ad8\u4e2d|\u4e2d\u4e13/;
    const fullText = (container.innerText || "").replace(/\s+/g, " ").trim();
    const segments = fullText.split(/[|\uff5c]/);
    for (const seg of segments) {
      const m = seg.match(EDU_RE);
      if (m) return normalizeEducation(m[0]);
    }
    return "";
  }

  function findProfileByFontSize() {
    const ageAnchor = findAgeAnchorInRightPanel();
    if (!ageAnchor) return null;
    const ageMatch = ageAnchor.text.match(/(\d{2})\s*\u5c81/);
    if (!ageMatch) return null;
    const container = getInfoBarContainer(ageAnchor.el);
    const ageCenterY = ageAnchor.rect.top + ageAnchor.rect.height / 2;
    const largest = findLargestFontElement(container, ageCenterY);
    if (!largest) return null;
    const rawName = largest.text.replace(/[\u2642\u2640\s]/g, "").trim();
    // \u76f4\u63a5\u7528\u6700\u5927\u5b57\u53f7\u5143\u7d20\u7684\u6587\u672c\u4f5c\u4e3a\u59d3\u540d\uff0c\u4ec5\u5265\u6389\u660e\u663e\u7684\u6d3b\u52a8\u72b6\u6001\u540e\u7f00\uff08\u521a\u521a\u6d3b\u8dc3/\u5728\u7ebf/X\u5206\u949f\u524d\u6d3b\u8dc3 \u7b49\uff09\u3002
    // \u4e0d\u505a"\u662f\u5426\u5408\u7406"\u7684\u5224\u65ad\u2014\u2014\u4fdd\u7559\u9875\u9762\u4e0a\u7aef\u539f\u6837\u7684\u8fde\u7eed\u5927\u5b57\u53f7\u5b57\u7b26\u4e32\uff0c\u5bf9\u91c7\u96c6\u7cfb\u7edf\u800c\u8a00\u8fd9\u5c31\u8db3\u4ee5\u505a\u5339\u914d/\u53bb\u91cd\u3002
    const nameFromLargest = stripActivityText(rawName);
    if (!nameFromLargest || nameFromLargest.length < 1) return null;
    if (largest.el === ageAnchor.el) return null;
    const genderOk = hasGenderIndicator(largest.el);
    const education = extractEducationFromPipes(container);
    const age = `${ageMatch[1]}\u5c81`;
    const rawText = `${nameFromLargest} ${ageAnchor.text} ${education}`.replace(/\s+/g, " ").trim();
    return {
      el: ageAnchor.el,
      info: { name: nameFromLargest, age, education: education || "\u5f85\u8bc6\u522b", raw_text: rawText.slice(0, 160) },
      score: genderOk ? 120 : 105,
      top: ageAnchor.rect.top,
      area: ageAnchor.rect.width * ageAnchor.rect.height,
      len: rawText.length,
    };
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
        if (isJobTitleContext(el)) return false;
        return /\d{2}\s*岁|博士|硕士|研究生|本科|大专|专科|高中|中专|[\u4e00-\u9fa5]{2,4}|[A-Za-z]{2,}/.test(text);
      })
      .map((el) => ({ el, rect: el.getBoundingClientRect(), text: textOf(el) }))
      .sort((a, b) => a.rect.left - b.rect.left || a.rect.top - b.rect.top);
  }

  function findTopProfileInfoRoot() {
    const fontSizeResult = findProfileByFontSize();
    if (fontSizeResult) return fontSizeResult;

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

  function extractBossTalkingPosition() {
    const pageWidth = window.innerWidth || document.documentElement.clientWidth || 1440;
    const rightLeft = Math.max(380, pageWidth * 0.28);
    const rePatterns = [
      /沟通的职位[\s\-：:—–]*([^\n\r]{1,80})/,
      /沟通职位[\s\-：:—–]*[：:]?\s*([^\n\r]{1,80})/,
      /聊天职位[\s\-：:—–]*[：:]?\s*([^\n\r]{1,80})/,
    ];
    const textKeywords = ["沟通的职位", "沟通职位", "聊天职位"];
    const candidates = [];
    const nodes = document.querySelectorAll("div, span, p, header, section");
    for (const el of nodes) {
      if (!isVisible(el)) continue;
      const rect = el.getBoundingClientRect();
      if (rect.left < rightLeft) continue;
      if (rect.top < 40 || rect.top > 320) continue;
      if (rect.width < 100 || rect.width > pageWidth) continue;
      const text = (el.innerText || el.textContent || "").trim();
      if (!text || text.length > 240) continue;
      if (!textKeywords.some(kw => text.includes(kw))) continue;
      for (const re of rePatterns) {
        const m = text.match(re);
        if (!m) continue;
        const raw = m[1].trim();
        if (!raw) continue;
        candidates.push({ el, rect, raw, top: rect.top, len: text.length });
        break;
      }
    }
    if (candidates.length === 0) {
      // 宽范围兜底：扫描右半侧 top 40-500 区间是否有任何包含职位关键词的文本
      const fallbackHits = [];
      for (const el of nodes) {
        if (!isVisible(el)) continue;
        const rect = el.getBoundingClientRect();
        if (rect.left < rightLeft || rect.top < 40 || rect.top > 500) continue;
        const text = (el.innerText || el.textContent || "").trim();
        if (text.length > 240 || text.length < 2) continue;
        if (textKeywords.some(kw => text.includes(kw))) {
          fallbackHits.push({ tag: el.tagName, cls: (el.className || "").toString().slice(0, 80), text: text.slice(0, 120), rect: { l: Math.round(rect.left), t: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height) } });
        }
      }
      if (fallbackHits.length > 0) {
        emit({ type: "boss_diag", data: { step: "talking_position_fallback_hits", hits: fallbackHits.slice(0, 5) } });
      } else {
        emit({ type: "boss_diag", data: { step: "talking_position_no_keyword", rightLeft, viewport: { w: pageWidth, h: window.innerHeight } } });
      }
      return "";
    }
    candidates.sort((a, b) => a.top - b.top || a.len - b.len);
    return candidates[0].raw;
  }

  function simplifyBossTalkingPosition(raw) {
    if (!raw) return "";
    let s = String(raw).replace(/[（(][^）)]*[）)]/g, "");
    s = s.split(/[/／]/)[0];
    s = s.trim();
    const chineseOnly = (s.match(/[一-龥]/g) || []).join("");
    if (chineseOnly && chineseOnly.length > 0) {
      return chineseOnly.length > 12 ? chineseOnly.slice(0, 12) : chineseOnly;
    }
    return s.length > 12 ? s.slice(0, 12) : s;
  }

  async function waitForContactInfo(clickedItem = null, timeoutMs = 2200) {
    const deadline = Date.now() + timeoutMs;
    await waitForRightPanelReady(Math.min(timeoutMs * 0.4, 1500));
    let info = extractContactInfo(clickedItem);
    while (`${info.name}/${info.age}/${info.education}` === "待识别/待识别/待识别" && Date.now() < deadline) {
      await sleep(120);
      info = extractContactInfo(clickedItem);
    }
    return info;
  }

  function isRightPanelLoading() {
    const pageWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    const rightArea = Array.from(document.querySelectorAll("[class*='loading'], [class*='skeleton'], [class*='spinner'], .chat-loading, .geek-loading"))
      .filter((el) => {
        if (!isVisible(el)) return false;
        const rect = el.getBoundingClientRect();
        return rect.left >= pageWidth * 0.28 && rect.top >= 50 && rect.top <= 300;
      });
    if (rightArea.length > 0) return true;
    const topBand = Array.from(document.querySelectorAll("body *"))
      .filter((el) => {
        if (!isVisible(el)) return false;
        const rect = el.getBoundingClientRect();
        return rect.left >= pageWidth * 0.28 && rect.left <= pageWidth * 0.78 && rect.top >= 80 && rect.top <= 170 && rect.width > 50 && rect.height > 10;
      });
    if (topBand.length === 0) return true;
    return false;
  }

  async function waitForRightPanelReady(maxWaitMs = 1500) {
    const deadline = Date.now() + maxWaitMs;
    while (isRightPanelLoading() && Date.now() < deadline) {
      await sleep(100);
    }
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
    // 精确选择器：附件简历按钮
    const precise = document.querySelector("a.btn.resume-btn-file");
    if (precise && isVisible(precise)) {
      const text = textOf(precise);
      const stateName = classifyResumeButtonText(text);
      const dimmed = isDisabled(precise) || isVisuallyDimmed(precise);
      return {
        el: precise,
        text,
        state: dimmed ? "dim" : "bright",
        state_label: dimmed ? "暗淡" : "明亮",
        enabled: !dimmed,
        left: precise.getBoundingClientRect().left,
        top: precise.getBoundingClientRect().top,
      };
    }
    // 回退：旧的启发式搜索
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

  function acceptResumeConsentIfNeeded() {
    const cardBtns = document.querySelectorAll(".message-card-wrap.boss-green .message-card-buttons .card-btn");
    for (const btn of cardBtns) {
      if (textOf(btn).trim() === "同意" && isVisible(btn)) return btn;
    }
    const noticeBtns = document.querySelectorAll(".notice-list.notice-blue-list .op a.btn");
    for (const btn of noticeBtns) {
      if (textOf(btn).trim() === "同意" && isVisible(btn)) return btn;
    }
    return null;
  }

  function findBossWorksPreviewButton() {
    const btns = document.querySelectorAll(".message-card-wrap.boss-green .message-card-buttons .card-btn");
    for (const btn of btns) {
      if (textOf(btn).trim() === "预览作品集") return btn;
    }
    return null;
  }

  function findBossChatScrollContainer() {
    const anyCard = document.querySelector(".message-card-wrap");
    if (anyCard) {
      for (let el = anyCard.parentElement; el; el = el.parentElement) {
        const style = getComputedStyle(el);
        if ((style.overflowY === "auto" || style.overflowY === "scroll") && el.scrollHeight > el.clientHeight) {
          return el;
        }
      }
    }
    for (const sel of [".chat-conversation", "[class*='chat-container']", "[class*='message-list']"]) {
      const el = document.querySelector(sel);
      if (el && el.scrollHeight > el.clientHeight) return el;
    }
    return null;
  }

  function _extractHttpsFromBosszp(raw) {
    if (!raw) return "";
    if (/^https?:\/\//i.test(raw)) return raw;
    if (/^bosszp:\/\//i.test(raw)) {
      try {
        const qIdx = raw.indexOf("?");
        if (qIdx < 0) return "";
        const params = new URLSearchParams(raw.slice(qIdx + 1));
        const inner = params.get("url");
        if (inner && /^https?:\/\//i.test(inner)) return inner;
      } catch {}
    }
    return "";
  }

  function extractBossWorksDownloadInfo(btn) {
    // __vue__ is only accessible from main world; use background.js chrome.scripting.executeScript
    return new Promise((resolve) => {
      const card = btn.closest(".message-card-wrap");
      if (!card) { resolve(null); return; }
      const marker = "__wex_" + Date.now() + "_" + Math.random().toString(36).slice(2);
      card.setAttribute("data-works-extract", marker);
      chrome.runtime.sendMessage({ type: "extract_vue_data", marker }, (resp) => {
        card.removeAttribute("data-works-extract");
        if (resp?.ok && resp.data) { resolve(resp.data); }
        else { resolve(null); }
      });
    });
  }

  async function tryDownloadBossAttachmentWorks(candidateId, signature, info) {
    let btn = findBossWorksPreviewButton();
    const allCardBtns = document.querySelectorAll(".message-card-wrap.boss-green .message-card-buttons .card-btn");
    emit({ type: "boss_works_detection_start", data: {
      candidate_id: candidateId, candidate_signature: signature,
      btn_found_immediate: !!btn,
      total_card_btns: allCardBtns.length,
      card_btn_texts: Array.from(allCardBtns).map(b => b.innerText.trim()).slice(0, 10),
    }});
    if (!btn) {
      const chatScroll = findBossChatScrollContainer();
      emit({ type: "boss_works_scroll_attempt", data: {
        candidate_id: candidateId, candidate_signature: signature,
        scroll_container_found: !!chatScroll,
        scroll_tag: chatScroll ? chatScroll.tagName : "",
        scroll_class: chatScroll ? (chatScroll.className || "").toString().slice(0, 80) : "",
        scroll_height: chatScroll ? chatScroll.scrollHeight : 0,
        client_height: chatScroll ? chatScroll.clientHeight : 0,
      }});
      if (chatScroll) {
        const originalTop = chatScroll.scrollTop;
        for (let attempt = 0; attempt < 6 && !btn; attempt++) {
          chatScroll.scrollTop = Math.max(0, chatScroll.scrollTop - chatScroll.clientHeight * 0.7);
          chatScroll.dispatchEvent(new Event("scroll", { bubbles: true }));
          await sleep(300);
          btn = findBossWorksPreviewButton();
        }
        if (!btn) {
          chatScroll.scrollTop = originalTop;
          chatScroll.dispatchEvent(new Event("scroll", { bubbles: true }));
        }
      }
    }

    // ── 多附件扫描：当"预览作品集"按钮不存在时，扫描所有"点击预览附件简历"卡片 ──
    // 通过 Vue 数据读取每张卡片的原始文件名，按关键词分类后分别下载简历和作品集。
    const WORKS_KW = ["作品集", "作品", "portfolio", "works"];
    const RESUME_KW = ["简历", "resume", "cv"];
    const previewBtns = Array.from(allCardBtns).filter(b => textOf(b).trim() === "点击预览附件简历");
    if (!btn && previewBtns.length >= 2) {
      const attachments = [];
      for (const pb of previewBtns) {
        const extractedInfo = await extractBossWorksDownloadInfo(pb);
        if (extractedInfo && extractedInfo.url) {
          const fn = (extractedInfo.filename || "").toLowerCase();
          const isWorks = WORKS_KW.some(kw => fn.includes(kw));
          const isResume = RESUME_KW.some(kw => fn.includes(kw));
          attachments.push({ btn: pb, info: extractedInfo, isWorks, isResume, filename: extractedInfo.filename || "" });
        }
      }
      emit({ type: "boss_multi_attachment_scan", data: {
        candidate_id: candidateId, candidate_signature: signature,
        total_preview_btns: previewBtns.length,
        extracted: attachments.map(a => ({ filename: a.filename, isWorks: a.isWorks, isResume: a.isResume })),
      }});

      let downloadedCount = 0;
      for (const att of attachments) {
        const variant = att.isWorks ? "attachment_works" : (att.isResume ? "attachment_resume_extra" : "attachment_extra");
        const typeLabel = att.isWorks ? "作品集" : (att.isResume ? "简历(补充下载)" : "附件");
        emit({ type: "boss_multi_attachment_downloading", data: {
          candidate_id: candidateId, candidate_signature: signature,
          filename: att.filename, variant, typeLabel,
        }});
        const dlReqId = makeDownloadRequestId(candidateId, signature) + "_" + variant + "_" + downloadedCount;
        emit({ type: "download_intent", data: {
          candidate_id: candidateId, candidate_signature: signature,
          candidate_info: info,
          expected_filename: att.filename || `${signature}_${variant}.pdf`,
          download_request_id: dlReqId, variant,
        }});
        const dlResult = await downloadDirectUrl({
          candidate_id: candidateId, candidate_signature: signature,
          candidate_info: info, url: att.info.url,
          download_request_id: dlReqId, variant,
        }, 15000);
        if (dlResult.ok) {
          await finalizeDownloadWithPersistAck(candidateId, signature, dlReqId, "boss_" + variant);
          emit({ type: "boss_multi_attachment_downloaded", data: {
            candidate_id: candidateId, candidate_signature: signature,
            filename: att.filename, variant, typeLabel,
          }});
          downloadedCount++;
        } else {
          emit({ type: "boss_multi_attachment_failed", data: {
            candidate_id: candidateId, candidate_signature: signature,
            filename: att.filename, variant, reason: dlResult.reason || "download_failed",
          }});
        }
      }
      return downloadedCount > 0;
    }

    if (!btn) return false;
    emit({ type: "boss_attachment_works_button_found", data: {
      candidate_id: candidateId, candidate_signature: signature
    }});

    const worksInfo = await extractBossWorksDownloadInfo(btn);
    if (!worksInfo || !worksInfo.url) {
      emit({ type: "boss_attachment_works_skipped", data: {
        candidate_id: candidateId, candidate_signature: signature,
        reason: "url_extraction_failed"
      }});
      return false;
    }

    emit({ type: "boss_attachment_works_found", data: {
      candidate_id: candidateId, candidate_signature: signature,
      url: worksInfo.url, filename: worksInfo.filename
    }});

    const downloadRequestId = makeDownloadRequestId(candidateId, signature);
    emit({ type: "download_intent", data: {
      candidate_id: candidateId,
      candidate_signature: signature,
      candidate_info: info,
      expected_filename: worksInfo.filename || `${signature}_works.pdf`,
      download_request_id: downloadRequestId,
      variant: "attachment_works",
    }});

    const dlResult = await downloadDirectUrl({
      candidate_id: candidateId,
      candidate_signature: signature,
      candidate_info: info,
      url: worksInfo.url,
      download_request_id: downloadRequestId,
      variant: "attachment_works",
    }, 15000);

    if (!dlResult.ok) {
      emit({ type: "boss_attachment_works_skipped", data: {
        candidate_id: candidateId, candidate_signature: signature,
        reason: dlResult.reason || "download_failed"
      }});
      return false;
    }

    await finalizeDownloadWithPersistAck(
      candidateId, signature, downloadRequestId, "boss_attachment_works"
    );
    emit({ type: "boss_attachment_works_downloaded", data: {
      candidate_id: candidateId, candidate_signature: signature,
      url: worksInfo.url, filename: worksInfo.filename
    }});
    return true;
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

  function clickElementDirect(el) {
    // 对已经精准定位到的元素派发点击 —— 不做 closest 上跳、不做 elementFromPoint 重定位。
    // 用途：智联顶部 tab、左导菜单这种 sibling 共享外层容器的场景。
    // clickElementReliably 会走 closest("[class*='filter']") 跳到容器，再用容器中心坐标派发，
    // 容器中心可能落在错误兄弟 tab 上（如"已获取微信"），造成误点。这里完全锚定 el 本身。
    if (!el) return false;
    try { el.scrollIntoView?.({ block: "center", inline: "center" }); } catch {}
    const rect = el.getBoundingClientRect();
    const x = Math.max(1, Math.min(window.innerWidth - 1, rect.left + rect.width / 2));
    const y = Math.max(1, Math.min(window.innerHeight - 1, rect.top + rect.height / 2));
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      try {
        el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, view: window }));
      } catch {}
    }
    try { el.click?.(); } catch {}
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

  function collectDomSnapshot() {
    const result = {};
    const maxLeft = Math.min(window.innerWidth * 0.45, 520);

    // 旧选择器全量检测
    const selectors = {
      "div.user-list.b-scroll-stable": 0,
      "div.user-list": 0,
      ".geek-item-wrap > .geek-item": 0,
      ".geek-item-wrap": 0,
      ".geek-item": 0,
      '.chat-label-item[title="沟通中"]': 0,
      ".chat-label-item": 0,
      'dl.menu-chat a[href*="/web/chat"]': 0,
      "dl.menu-chat": 0,
      '[class*="user-list"]': 0,
      '[class*="conversation"]': 0,
      '[class*="geek"]': 0,
    };
    for (const sel of Object.keys(selectors)) {
      try { selectors[sel] = document.querySelectorAll(sel).length; } catch (_) { /* ignore */ }
    }
    result.selector_counts = selectors;

    // 页面信息
    result.page = {
      url: location.href,
      title: document.title.slice(0, 100),
      viewport: { w: window.innerWidth, h: window.innerHeight },
    };

    // body 直属 div
    result.body_children = Array.from(document.body.children).slice(0, 15).map(el => {
      const r = el.getBoundingClientRect();
      return {
        tag: el.tagName,
        cls: (el.className || "").toString().slice(0, 120),
        id: el.id || "",
        children: el.children.length,
        rect: { l: Math.round(r.left), t: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) },
      };
    });

    // 左侧区域大容器
    const leftContainers = [];
    document.querySelectorAll("div, ul, section, nav").forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.left < maxLeft && r.width > 80 && r.height > 150 && r.top > 40 && el.children.length > 2) {
        leftContainers.push({
          tag: el.tagName,
          cls: (el.className || "").toString().slice(0, 120),
          children: el.children.length,
          scrollH: el.scrollHeight,
          clientH: el.clientHeight,
          rect: { l: Math.round(r.left), t: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) },
          firstKids: Array.from(el.children).slice(0, 3).map(c => ({
            tag: c.tagName,
            cls: (c.className || "").toString().slice(0, 60),
          })),
        });
      }
    });
    leftContainers.sort((a, b) => b.children - a.children);
    result.left_containers = leftContainers.slice(0, 15);

    // 含年龄/学历文本的元素
    const ageRe = /\d{2}\s*岁/;
    const eduRe = /本科|大专|硕士|博士|研究生|专科|高中|中专/;
    const textHits = [];
    document.querySelectorAll("li, div, span, a").forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.width < 50 || r.height < 20 || r.left > maxLeft) return;
      const t = (el.textContent || "").trim();
      if (t.length > 10 && t.length < 200 && (ageRe.test(t) || eduRe.test(t))) {
        textHits.push({
          tag: el.tagName,
          cls: (el.className || "").toString().slice(0, 100),
          text: t.replace(/\s+/g, " ").slice(0, 100),
          rect: { l: Math.round(r.left), t: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) },
          path: getElementDomPath(el),
        });
      }
    });
    result.candidate_text_hits = textHits.slice(0, 20);

    // 含"沟通中"文本的元素
    const chattingHits = [];
    document.querySelectorAll("div, span, a, li, label, button").forEach(el => {
      const t = (el.textContent || "").trim();
      if (t === "沟通中") {
        const r = el.getBoundingClientRect();
        if (r.width > 0) chattingHits.push({
          tag: el.tagName,
          cls: (el.className || "").toString().slice(0, 100),
          title: el.getAttribute("title") || "",
          rect: { l: Math.round(r.left), t: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) },
          path: getElementDomPath(el),
        });
      }
    });
    result.chatting_tab_hits = chattingHits;

    return result;
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
    // 精确选择器：预览弹窗关闭按钮
    const preciseClose = document.querySelector(".dialog-wrap.active .close-btn");
    if (preciseClose && isVisible(preciseClose)) {
      emit({
        type: "stale_preview_close_diagnostics",
        data: {
          candidate_id: candidateId,
          candidate_signature: signature,
          close_candidate_count: 1,
          close_candidates: [{ score: 99, descriptor: "precise:.dialog-wrap.active .close-btn", path: getElementDomPath(preciseClose), rect: getRectSnapshot(preciseClose) }],
        },
      });
      return clickElementReliably(preciseClose);
    }
    // 回退：启发式搜索
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
        const popupHost = el.closest?.(".dialog-wrap, .popover, .modal, .ant-modal, .ant-modal-root, .resume-preview, .resume-preview-modal, [class*='preview']") || null;
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
    // 精确路径：BOSS 附件简历 iframe
    const preciseIframe = document.querySelector("iframe.attachment-iframe");
    if (preciseIframe && isVisible(preciseIframe)) {
      const rect = preciseIframe.getBoundingClientRect();
      if (rect.width >= 300 && rect.height >= 180) {
        return { root: preciseIframe, rect, score: 100, info: extractResumePreviewInfo(preciseIframe, fallbackInfo), pdf_iframe: true };
      }
    }

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
        resumePreviewLearnState._cancelCapture = null;
        clearTimeout(timer);
        window.removeEventListener("pointerdown", handler, true);
        window.removeEventListener("mousedown", handler, true);
        window.removeEventListener("click", handler, true);
      }

      resumePreviewLearnState._cancelCapture = () => { cleanup(); resolve(null); };

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
      if (state === "paused") state = "collecting";
      return false;
    }
    const downloadResult = await resultPromise;
    if (state === "paused") state = "collecting";
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
    try {
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
      // 仅在没有任何预览根时才退化到 document.body，避免在 chat 列表 dom 上做全局
      // [class*='card-btn'] 扫描（聊天列表里大量 card-btn + getElementDescriptor → textOf
      // 会触发数十万 layout，卡死主线程）。
      if (roots.length === 0) {
        pushRoot(document.body);
      }
      emit({ type: "boss_svg_scan_roots_prepared", data: { roots_count: roots.length, has_preview: Boolean(preview?.root), fallback_body: roots.length === 1 && roots[0] === document.body } });
      const matches = [];
      const selector = "[class*='attachment-resume-btns'] svg, [class*='attachment-resume-btns'] use, [class*='resume-footer'] svg, [class*='resume-footer'] use, [class*='resume-detail'] [class*='boss-svg'], [class*='resume-detail'] [class*='svg-icon'], span.card-btn, [class*='card-btn'], [class*='resume-btn-file'], a[class*='resume-btn-file']";
      for (const root of roots) {
        const nodes = root.matches?.(selector) ? [root, ...root.querySelectorAll(selector)] : Array.from(root.querySelectorAll?.(selector) || []);
        for (const el of nodes) {
          if (isInExcludedBossDownloadArea(el)) continue;
          const clickable = getDownloadClickableNode(el);
          if (!clickable || !isVisible(clickable) || isDisabled(clickable)) continue;
          const elHref = el.getAttribute?.("href") || el.getAttribute?.("xlink:href") || "";
          const isXlinkDownload = /download/i.test(elHref) && (!!el.closest?.("[class*='attachment-resume-btns']") || (preview?.root && preview.root.contains(el)));
          if (!isXlinkDownload && !isStrictResumeActionArea(clickable, preview) && !isStrictResumeActionArea(el, preview)) continue;
          const rect = clickable.getBoundingClientRect();
          if (rect.width < 10 || rect.height < 10 || rect.top < 0 || rect.left < 0) continue;
          const descriptor = `${getElementDescriptor(el)} ${getElementDescriptor(clickable)} ${getElementDescriptor(clickable.parentElement)}`;
          const combined = descriptor.toLowerCase();
          const inPopupContainer = !!clickable.closest?.("[class*='preview'], [class*='dialog'], [class*='modal'], [class*='drawer'], [class*='popup'], [class*='layer'], [role='dialog']");
          const inPreviewRoot = preview?.root && preview.root.contains(clickable);
          const isHtmlPopupDownload = /card-btn/i.test(descriptor) && /附件简历|下载/.test(descriptor) && (inPopupContainer || inPreviewRoot);
          const isPreviewRootDownload = inPreviewRoot && /下载|download|附件简历/.test(combined) && !/关闭|close|取消|返回/i.test(combined);
          const isResumeBtnFile = /resume-btn-file/i.test(clickable.className || "") && /附件简历|下载|download/i.test(combined);
          if (!isXlinkDownload && !isHtmlPopupDownload && !isPreviewRootDownload && !isResumeBtnFile && !isBossSvgDownloadDescriptor(descriptor)) continue;
          if (/关闭|close|取消|返回|back|delete|trash|更多|more|打印|print|zoom|放大|缩小|rotate|旋转|×|✕|esc/i.test(combined)) continue;
          let score = 40;
          if (isResumeBtnFile) score += 35;
          if (isHtmlPopupDownload) score += 30;
          if (isPreviewRootDownload) score += 20;
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
    } catch (err) {
      emit({ type: "boss_svg_scan_error", data: { message: String(err?.message || err), stack: String(err?.stack || "").slice(0, 800) } });
      return null;
    }
  }

  function _extractVueHref(vm) {
    if (!vm) return "";
    for (const src of [vm, vm.$parent, vm.$parent?.$parent]) {
      if (!src) continue;
      const h = src.href || src.$props?.href || src.$data?.href || src.$attrs?.href || "";
      if (h && /^https?:\/\//i.test(h)) return h;
    }
    return "";
  }

  function _extractDomHref(el) {
    if (!el) return "";
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      const href = node.getAttribute?.("href") || node.getAttribute?.("data-href") || "";
      if (href && /^https?:\/\//i.test(href)) return href;
    }
    return "";
  }

  function _safeClickNoNavigation(el) {
    const origOpen = window.open;
    window.open = function () { return null; };
    const anchor = el.closest?.("a[href]");
    let origHref = "";
    if (anchor) {
      origHref = anchor.getAttribute("href") || "";
      anchor.removeAttribute("href");
    }
    try { clickElementReliably(el); } finally {
      window.open = origOpen;
      if (anchor && origHref) anchor.setAttribute("href", origHref);
    }
  }

  function tryVueDirectDownload(target) {
    try {
      const candidates = [
        target?.closest?.("[class*='icon-content']"),
        target?.parentElement?.closest?.("[class*='icon-content']"),
        target,
        target?.parentElement,
        target?.parentElement?.parentElement,
      ];
      for (let i = 0; i < 5; i++) {
        const node = i < candidates.length ? candidates[i] : target;
        if (!node) continue;
        const vm = node.__vue__;
        const href = _extractVueHref(vm);
        if (href) {
          return href;
        }
      }
      return null;
    } catch (e) { return null; }
  }

  function getPreviewRoots() {
    const selectors = [
      ".dialog-wrap.active .resume-detail",
      ".dialog-wrap.active",
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
    // 精确选择器：附件简历工具栏下载按钮（第三个 icon-content，含 #icon-attacthment-download SVG）
    const preciseDownload = document.querySelector('.attachment-resume-btns .popover.icon-content:last-child');
    if (preciseDownload && isVisible(preciseDownload)) {
      const svgUse = preciseDownload.querySelector('use');
      const href = svgUse?.getAttribute('xlink:href') || svgUse?.getAttribute('href') || '';
      if (href.includes('download')) {
        emit({
          type: "download_button_candidates_detailed",
          data: { candidate_id: candidateId, candidate_signature: signature, candidates: [{ score: 100, text: "precise:attachment-resume-btns download", descriptor: `svg:${href}`, path: getElementDomPath(preciseDownload), rect: getRectSnapshot(preciseDownload) }] },
        });
        return preciseDownload;
      }
    }
    // 回退：启发式搜索
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
        if (/card-btn/i.test(descriptor) && /附件简历|下载/.test(descriptor)) score += 24;
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
      // 先消费早到的 ack（race: 桥侧 ack 可能在扩展进入 await 之前就到了）
      const earlyByPrimary = primaryKey ? receivedPersistAcks.get(primaryKey) : null;
      const earlyByFallback = (!earlyByPrimary && fallbackKey && fallbackKey !== primaryKey) ? receivedPersistAcks.get(fallbackKey) : null;
      const early = earlyByPrimary || earlyByFallback;
      if (early) {
        if (primaryKey) receivedPersistAcks.delete(primaryKey);
        if (fallbackKey) receivedPersistAcks.delete(fallbackKey);
        resolve(early);
        return;
      }
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

  function creditPersistCompletion(downloadRequestId, signature, source) {
    const reqKey = downloadRequestId || "";
    const sigKey = signature || "";
    if (reqKey && persistAckCreditedRequests.has(reqKey)) return false;
    if (sigKey && persistAckCreditedSignatures.has(sigKey)) return false;
    if (reqKey) persistAckCreditedRequests.add(reqKey);
    if (sigKey) persistAckCreditedSignatures.add(sigKey);
    results.completed++;
    emit({ type: "persist_completion_credited", data: { candidate_signature: sigKey, download_request_id: reqKey, source, completed: results.completed } });
    emitProgress();
    return true;
  }

  const BOSS_COOLDOWN_BATCH = 5;
  const BOSS_COOLDOWN_BASE_MIN_MS = 30000;
  const BOSS_COOLDOWN_BASE_MAX_MS = 60000;
  const BOSS_COOLDOWN_ESCALATION = 0.20;

  async function maybeBossCooldown() {
    if (PLATFORM.code !== "boss") return;
    const completed = results.completed;
    if (completed <= 0 || completed % BOSS_COOLDOWN_BATCH !== 0) return;
    const round = Math.floor(completed / BOSS_COOLDOWN_BATCH) - 1;
    const multiplier = Math.pow(1 + BOSS_COOLDOWN_ESCALATION, round);
    const minMs = Math.round(BOSS_COOLDOWN_BASE_MIN_MS * multiplier);
    const maxMs = Math.round(BOSS_COOLDOWN_BASE_MAX_MS * multiplier);
    const waitMs = minMs + Math.floor(Math.random() * (maxMs - minMs + 1));
    const waitSec = (waitMs / 1000).toFixed(1);
    emit({ type: "boss_cooldown_start", data: { completed, round: round + 1, wait_ms: waitMs, wait_sec: waitSec, multiplier: multiplier.toFixed(2), range: `${(minMs/1000).toFixed(0)}-${(maxMs/1000).toFixed(0)}s` } });
    await sleep(waitMs);
    emit({ type: "boss_cooldown_end", data: { completed, wait_ms: waitMs } });
  }

  async function finalizeDownloadWithPersistAck(candidateId, signature, downloadRequestId, strategy) {
    const persist = await waitForPersistAck(downloadRequestId, signature, 8000);
    if (persist.ok) {
      creditPersistCompletion(downloadRequestId, signature, "ack_ok");
      emit({ type: "resume_persist_confirmed", data: { candidate_id: candidateId, candidate_signature: signature, download_request_id: downloadRequestId, strategy, ...(persist.data || {}) } });
      await maybeBossCooldown();
      await sleep(Math.min(Math.max(config.interval_ms || 0, 300), 900));
      return true;
    }
    emit({ type: "resume_persist_rejected", data: { candidate_id: candidateId, candidate_signature: signature, download_request_id: downloadRequestId, strategy, status: persist.status || "unknown", reason: persist.reason || "", ...(persist.data || {}) } });
    // ack 超时 ≠ 下载失败：桥侧的 resume_saved 事件会在 onMessage 链路里补回 completed 计数（见 creditPersistCompletion）。
    // 此处保底再补一次：桥侧无论 saved/timeout 都已经落盘，跑完上层应直接结束本候选人，避免重复点击下载。
    if (persist.status === "persist_ack_timeout") {
      creditPersistCompletion(downloadRequestId, signature, "ack_timeout_fallback");
      await maybeBossCooldown();
      return true;
    }
    // bridge 返回 duplicate_in_run / duplicate_skipped 说明文件已落盘（本轮或历史），不是真失败
    if (persist.status === "duplicate_in_run" || persist.status === "duplicate_skipped") {
      creditPersistCompletion(downloadRequestId, signature, "ack_duplicate_fallback");
      return true;
    }
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
    const directUrl = tryVueDirectDownload(target) || _extractDomHref(target);
    if (directUrl) {
      const started = await downloadDirectUrl({ candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, url: directUrl, download_request_id: downloadRequestId });
      if (!started.ok) _safeClickNoNavigation(target);
    } else {
      _safeClickNoNavigation(target);
    }
    emit({ type: "boss_svg_download_icon_clicked", data: { candidate_id: candidateId, candidate_signature: signature, ...snapshot, click_had_direct_url: Boolean(directUrl), diagnostics: getDownloadClickDiagnostics(target, "boss_svg_after_click") } });
    await sleep(1000);
    emit({ type: "download_click_post_diagnostics", data: { candidate_id: candidateId, candidate_signature: signature, click_strategy: "boss_svg_icon", diagnostics: getDownloadClickDiagnostics(target, "boss_svg_1s_after_click") } });
    const downloadResult = await resultPromise;
    if (downloadResult.ok) {
      emit({ type: "boss_svg_download_link_captured", data: { candidate_id: candidateId, candidate_signature: signature, download_url: findDownloadUrlFromResult(downloadResult), ...downloadResult.data } });
      const accepted = await finalizeDownloadWithPersistAck(candidateId, signature, downloadRequestId, "boss_svg_icon");
      return accepted;
    }
    emit({ type: "boss_svg_download_link_capture_failed", data: { candidate_id: candidateId, candidate_signature: signature, reason: downloadResult.reason || "download_failed", ...(downloadResult.data || {}) } });
    // 下载已触发但失败（文件损坏/网络错误）→ 直接返回 false，不进 manual learning
    // 仅当 download_timeout（Chrome 从未创建下载）才认为点击目标可能有误，尝试学习
    if (downloadResult.reason === "download_timeout" && !resumePreviewLearnState.learnedClick) {
      if (await learnManualDownloadClickAfterFailure(candidateId, signature, info, "boss_svg_download_timeout")) {
        return true;
      }
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

    // If target is an <a> with a download-like href, use direct download API instead of simulated click
    const anchorEl = target.closest?.("a[href]") || (target.tagName === "A" && target.href ? target : null);
    const anchorHref = anchorEl?.href || "";
    if (anchorHref && /^https?:\/\//i.test(anchorHref) && /download|docdownload|attachment|resume/i.test(anchorHref)) {
      const downloadRequestId = makeDownloadRequestId(candidateId, signature);
      emit({ type: "download_intent", data: { candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, download_request_id: downloadRequestId } });
      emit({ type: "learned_download_using_direct_url", data: { candidate_id: candidateId, candidate_signature: signature, url: anchorHref } });
      const resultPromise = waitForDownloadResult(downloadRequestId, 20000);
      const started = await downloadDirectUrl({ candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, url: anchorHref, download_request_id: downloadRequestId });
      if (!started.ok) {
        emit({ type: "learned_download_direct_url_failed", data: { candidate_id: candidateId, candidate_signature: signature, reason: started.reason || "start_failed" } });
        return false;
      }
      const downloadResult = await resultPromise;
      if (downloadResult.ok) {
        const accepted = await finalizeDownloadWithPersistAck(candidateId, signature, downloadRequestId, "learned_click_direct_url");
        return accepted;
      }
      emit({ type: "learned_download_click_download_failed", data: { candidate_id: candidateId, candidate_signature: signature, reason: downloadResult.reason || "direct_url_download_failed" } });
      return false;
    }

    const learnedDirectUrl = tryVueDirectDownload(target) || _extractDomHref(target);
    if (!learnedDirectUrl) {
      emit({ type: "learned_download_click_no_url", data: { candidate_id: candidateId, candidate_signature: signature, reason: "no_extractable_url_skip_to_next_strategy" } });
      return false;
    }
    const downloadRequestId = makeDownloadRequestId(candidateId, signature);
    emit({ type: "download_intent", data: { candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, download_request_id: downloadRequestId } });
    const resultPromise = waitForDownloadResult(downloadRequestId);
    const started = await downloadDirectUrl({ candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, url: learnedDirectUrl, download_request_id: downloadRequestId });
    if (!started.ok) {
      emit({ type: "learned_download_click_download_failed", data: { candidate_id: candidateId, candidate_signature: signature, reason: started.reason || "direct_url_start_failed" } });
      return false;
    }
    const downloadResult = await resultPromise;
    if (downloadResult.ok) {
      const accepted = await finalizeDownloadWithPersistAck(candidateId, signature, downloadRequestId, "learned_click");
      return accepted;
    }
    emit({ type: "learned_download_click_download_failed", data: { candidate_id: candidateId, candidate_signature: signature, reason: downloadResult.reason || "download_not_triggered" } });
    return false;
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

  async function tryDomTextDownloadUrlScan(candidateId, signature, info, preview) {
    if (!preview?.root || preview.info?.preview_source !== "dom_text") return false;
    emit({ type: "dom_text_download_url_scan_started", data: { candidate_id: candidateId, candidate_signature: signature } });
    const root = preview.root;
    const urlPattern = /preview4boss|\/download\/|attachment.*\.pdf|\.pdf(?:$|[?#])/i;
    const anchors = Array.from(root.querySelectorAll("a[href]"));
    for (const a of anchors) {
      const href = a.href || a.getAttribute("href") || "";
      if (href && urlPattern.test(href) && /^https?:\/\//i.test(href)) {
        emit({ type: "dom_text_download_url_found", data: { candidate_id: candidateId, candidate_signature: signature, url: href, source: "anchor" } });
        const downloadRequestId = makeDownloadRequestId(candidateId, signature);
        emit({ type: "download_intent", data: { candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, download_request_id: downloadRequestId } });
        const resultPromise = waitForDownloadResult(downloadRequestId, 20000);
        const started = await downloadDirectUrl({ candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, url: href, download_request_id: downloadRequestId });
        if (!started.ok) { emit({ type: "dom_text_direct_download_failed", data: { candidate_id: candidateId, candidate_signature: signature, reason: started.reason || "start_failed" } }); return false; }
        const downloadResult = await resultPromise;
        if (downloadResult.ok) { await finalizeDownloadWithPersistAck(candidateId, signature, downloadRequestId, "dom_text_anchor_url"); return true; }
        return false;
      }
    }
    const allEls = root.querySelectorAll("*");
    for (const el of allEls) {
      const vm = el.__vue__;
      if (!vm) continue;
      const href = _extractVueHref(vm);
      if (href && urlPattern.test(href)) {
        emit({ type: "dom_text_download_url_found", data: { candidate_id: candidateId, candidate_signature: signature, url: href, source: "vue_instance" } });
        const downloadRequestId = makeDownloadRequestId(candidateId, signature);
        emit({ type: "download_intent", data: { candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, download_request_id: downloadRequestId } });
        const resultPromise = waitForDownloadResult(downloadRequestId, 20000);
        const started = await downloadDirectUrl({ candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, url: href, download_request_id: downloadRequestId });
        if (!started.ok) { emit({ type: "dom_text_direct_download_failed", data: { candidate_id: candidateId, candidate_signature: signature, reason: started.reason || "start_failed" } }); return false; }
        const downloadResult = await resultPromise;
        if (downloadResult.ok) { await finalizeDownloadWithPersistAck(candidateId, signature, downloadRequestId, "dom_text_vue_url"); return true; }
        return false;
      }
    }
    emit({ type: "dom_text_download_url_not_found", data: { candidate_id: candidateId, candidate_signature: signature, anchor_count: anchors.length, vue_element_count: Array.from(allEls).filter((e) => e.__vue__).length } });
    // Fallback: search outside preview root for resume-btn-file links (BOSS places download button in toolbar area)
    const globalResumeBtnLinks = document.querySelectorAll("a[class*='resume-btn-file'][href]");
    for (const a of globalResumeBtnLinks) {
      const href = a.href || a.getAttribute("href") || "";
      if (href && urlPattern.test(href) && /^https?:\/\//i.test(href) && isVisible(a)) {
        emit({ type: "dom_text_download_url_found", data: { candidate_id: candidateId, candidate_signature: signature, url: href, source: "resume_btn_file_global" } });
        const downloadRequestId = makeDownloadRequestId(candidateId, signature);
        emit({ type: "download_intent", data: { candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, download_request_id: downloadRequestId } });
        const resultPromise = waitForDownloadResult(downloadRequestId, 20000);
        const started = await downloadDirectUrl({ candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, url: href, download_request_id: downloadRequestId });
        if (!started.ok) { emit({ type: "dom_text_direct_download_failed", data: { candidate_id: candidateId, candidate_signature: signature, reason: started.reason || "start_failed" } }); return false; }
        const downloadResult = await resultPromise;
        if (downloadResult.ok) { await finalizeDownloadWithPersistAck(candidateId, signature, downloadRequestId, "dom_text_resume_btn_file"); return true; }
        return false;
      }
    }
    return false;
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

    if (await tryDomTextDownloadUrlScan(candidateId, signature, info, preview)) {
      return;
    }

    if (resumePreviewLearnState.learnedClick && await clickLearnedDownload(candidateId, signature, info)) {
      return;
    }

    if (await clickBossSvgDownloadIcon(candidateId, signature, info, preview)) {
      return;
    }

    const downloadButton = await waitForDownloadButton(candidateId, signature, 5000);
    if (!downloadButton) {
      emit({ type: "all_download_strategies_exhausted", data: { candidate_id: candidateId, candidate_signature: signature, preview_source: preview?.info?.preview_source || "" } });
      // 兜底进入手动学习模式：所有自动策略都失败，让用户手动点一次重新捕获 selector
      if (await learnManualDownloadClickAfterFailure(candidateId, signature, info, "all_strategies_exhausted")) {
        return;
      }
      await skipCandidate(candidateId, signature, "download_button_not_found");
      return;
    }

    emit({ type: "auto_download_click_used", data: { candidate_id: candidateId, candidate_signature: signature, ...getElementSnapshot(downloadButton), diagnostics: getDownloadClickDiagnostics(downloadButton, "auto_before_click") } });
    const downloadRequestId = makeDownloadRequestId(candidateId, signature);
    emit({ type: "download_intent", data: { candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, download_request_id: downloadRequestId } });
    const resultPromise = waitForDownloadResult(downloadRequestId, 20000);
    const autoDirectUrl = tryVueDirectDownload(downloadButton) || _extractDomHref(downloadButton);
    if (autoDirectUrl) {
      const started = await downloadDirectUrl({ candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, url: autoDirectUrl, download_request_id: downloadRequestId });
      if (!started.ok) _safeClickNoNavigation(downloadButton);
    } else {
      _safeClickNoNavigation(downloadButton);
    }
    await sleep(1000);
    emit({ type: "download_click_post_diagnostics", data: { candidate_id: candidateId, candidate_signature: signature, click_strategy: "auto_download_button", click_had_direct_url: Boolean(autoDirectUrl), diagnostics: getDownloadClickDiagnostics(downloadButton, "auto_1s_after_click") } });
    const downloadResult = await resultPromise;
    if (downloadResult.ok) {
      await finalizeDownloadWithPersistAck(candidateId, signature, downloadRequestId, "auto_download_button");
    } else if (downloadResult.reason === "download_timeout" && await learnManualDownloadClickAfterFailure(candidateId, signature, info, "auto_download_timeout")) {
      return;
    } else {
      await skipCandidate(candidateId, signature, downloadResult.reason || "download_failed", downloadResult.data || {});
    }
  }

  async function waitForPause() {
    if (state !== "paused") return;
    await new Promise((resolve) => { pauseResolve = resolve; });
  }

  function needsQianchengLearning() {
    return PLATFORM.code === "qiancheng" && !isQianchengLearningComplete();
  }

  // ============================================================
  // Qiancheng (51job ehire) 学习模式
  // ============================================================
  // 首次采集时通过浮动 banner 引导用户依次点击 4 个 DOM 锚点，
  // 抓取 selector 存入 localStorage，供后续自动采集使用。
  // ============================================================

  const QIANCHENG_LEARNING_BANNER_ID = "__qc_learning_banner__";

  function isQianchengLearningComplete() {
    if (!PLATFORM || PLATFORM.code !== "qiancheng") return true;
    const keys = PLATFORM.storage_keys || {};
    const required = [
      keys.learned_nav_menu_chat,
      keys.learned_tab_chatting,
      keys.learned_candidate_card,
      keys.learned_profile_info,
      keys.learned_attachment_btn,
      keys.learned_preview_form,
      keys.learned_click,
      keys.learned_close_preview,
    ];
    for (const key of required) {
      if (!key) continue;
      const val = localStorage.getItem(key);
      if (!val) return false;
    }
    return true;
  }

  function clearQianchengLearningKeys() {
    if (!PLATFORM || PLATFORM.code !== "qiancheng") return;
    const keys = PLATFORM.storage_keys || {};
    [
      keys.learned_nav_menu_chat,
      keys.learned_tab_chatting,
      keys.learned_candidate_card,
      keys.learned_profile_info,
      keys.learned_attachment_btn,
      keys.learned_preview_form,
      keys.learned_click,
      keys.learned_close_preview,
      keys.learning_stage,
    ].forEach((k) => {
      if (k) {
        try { localStorage.removeItem(k); } catch {}
      }
    });
  }

  function getElementSelectorCandidates(el) {
    // 返回当前元素 + 5 级祖先的描述链
    const chain = [];
    let cur = el;
    for (let i = 0; i < 6 && cur && cur.tagName; i++) {
      const attrs = {};
      for (const a of (cur.attributes || [])) attrs[a.name] = String(a.value).slice(0, 200);
      chain.push({
        tag: cur.tagName,
        id: cur.id || "",
        cls: String(cur.className || "").slice(0, 240),
        text: (cur.innerText || "").trim().slice(0, 120),
        attrs: attrs,
        rect: (() => { try { const r = cur.getBoundingClientRect(); return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) }; } catch { return null; } })(),
      });
      cur = cur.parentElement;
    }
    return chain;
  }

  function deriveStableSelector(el) {
    // 按优先级生成最稳定的 selector，Phase 5 固化时会再校准
    if (!el || !el.tagName) return "";
    // 1) data-* 属性
    for (const a of (el.attributes || [])) {
      const name = a.name || "";
      if (name.startsWith("data-") && a.value && /^[a-zA-Z0-9_-]+$/.test(a.value)) {
        return `${el.tagName.toLowerCase()}[${name}="${a.value}"]`;
      }
    }
    // 2) 自身稳定 class（不含数字 hash 段）
    const classes = String(el.className || "").split(/\s+/).filter((c) => c && !/^\d/.test(c) && !/[a-z]\d[a-zA-Z]/.test(c));
    if (classes.length) {
      return `${el.tagName.toLowerCase()}.${classes[0]}`;
    }
    // 3) role + 部分文本
    const role = el.getAttribute && el.getAttribute("role");
    if (role) {
      return `${el.tagName.toLowerCase()}[role="${role}"]`;
    }
    return el.tagName.toLowerCase();
  }

  function ensureQianchengLearningBanner() {
    let banner = document.getElementById(QIANCHENG_LEARNING_BANNER_ID);
    if (banner) return banner;
    banner = document.createElement("div");
    banner.id = QIANCHENG_LEARNING_BANNER_ID;
    banner.style.cssText = [
      "position:fixed", "top:16px", "right:16px", "z-index:2147483647",
      "background:#fff", "border:2px solid #4A90E2", "border-radius:12px",
      "padding:14px 16px", "min-width:320px", "max-width:380px",
      "box-shadow:0 12px 32px rgba(31,41,55,.18)",
      "font-family:-apple-system, 'Microsoft YaHei', sans-serif",
      "font-size:13px", "line-height:1.5", "color:#1F2937",
    ].join(";");
    banner.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
        <strong style="font-size:14px;color:#4A90E2;">🎓 51前程无忧采集器首次学习</strong>
        <span id="${QIANCHENG_LEARNING_BANNER_ID}_close" style="cursor:pointer;color:#94A3B8;font-size:18px;line-height:1;padding:0 4px;">✕</span>
      </div>
      <div id="${QIANCHENG_LEARNING_BANNER_ID}_step" style="margin:6px 0;font-weight:700;color:#172033;">Step 1/7</div>
      <div id="${QIANCHENG_LEARNING_BANNER_ID}_msg" style="margin:4px 0 8px;color:#475569;">准备中...</div>
      <div id="${QIANCHENG_LEARNING_BANNER_ID}_progress" style="display:flex;gap:5px;">
        <span class="qc-dot" style="width:12px;height:12px;border-radius:50%;background:#E5EAF2;display:inline-block;"></span>
        <span class="qc-dot" style="width:12px;height:12px;border-radius:50%;background:#E5EAF2;display:inline-block;"></span>
        <span class="qc-dot" style="width:12px;height:12px;border-radius:50%;background:#E5EAF2;display:inline-block;"></span>
        <span class="qc-dot" style="width:12px;height:12px;border-radius:50%;background:#E5EAF2;display:inline-block;"></span>
        <span class="qc-dot" style="width:12px;height:12px;border-radius:50%;background:#E5EAF2;display:inline-block;"></span>
        <span class="qc-dot" style="width:12px;height:12px;border-radius:50%;background:#E5EAF2;display:inline-block;"></span>
        <span class="qc-dot" style="width:12px;height:12px;border-radius:50%;background:#E5EAF2;display:inline-block;"></span>
      </div>
    `;
    document.body.appendChild(banner);
    const closeBtn = document.getElementById(`${QIANCHENG_LEARNING_BANNER_ID}_close`);
    if (closeBtn) {
      closeBtn.addEventListener("click", () => {
        banner.style.display = "none";
      });
    }
    return banner;
  }

  function updateQianchengLearningBanner(stepIdx, stepTotal, message) {
    const banner = ensureQianchengLearningBanner();
    banner.style.display = "block";
    const stepEl = document.getElementById(`${QIANCHENG_LEARNING_BANNER_ID}_step`);
    const msgEl = document.getElementById(`${QIANCHENG_LEARNING_BANNER_ID}_msg`);
    if (stepEl) stepEl.textContent = `Step ${stepIdx}/${stepTotal}`;
    if (msgEl) msgEl.textContent = message;
    const dots = banner.querySelectorAll(".qc-dot");
    dots.forEach((d, i) => {
      d.style.background = i < stepIdx - 1 ? "#16A34A" : (i === stepIdx - 1 ? "#4A90E2" : "#E5EAF2");
    });
  }

  function dismissQianchengLearningBanner(finalMessage = "🎉 学习完成") {
    const banner = ensureQianchengLearningBanner();
    const stepEl = document.getElementById(`${QIANCHENG_LEARNING_BANNER_ID}_step`);
    const msgEl = document.getElementById(`${QIANCHENG_LEARNING_BANNER_ID}_msg`);
    if (stepEl) stepEl.textContent = "完成";
    if (msgEl) msgEl.textContent = finalMessage;
    const dots = banner.querySelectorAll(".qc-dot");
    dots.forEach((d) => { d.style.background = "#16A34A"; });
    setTimeout(() => { try { banner.style.display = "none"; } catch {} }, 5000);
  }

  function captureNextQianchengClick(timeoutMs = 120000) {
    return new Promise((resolve) => {
      let done = false;
      const handler = (ev) => {
        if (done) return;
        const target = ev.target;
        if (!target) return;
        // 排除 banner 自身的点击
        if (target.closest && target.closest(`#${QIANCHENG_LEARNING_BANNER_ID}`)) return;
        done = true;
        document.removeEventListener("click", handler, true);
        clearTimeout(timer);
        resolve({
          ok: true,
          target_element: target,
          client_xy: [ev.clientX, ev.clientY],
          chain: getElementSelectorCandidates(target),
          selector: deriveStableSelector(target),
        });
      };
      const timer = setTimeout(() => {
        if (done) return;
        done = true;
        document.removeEventListener("click", handler, true);
        resolve({ ok: false, reason: "timeout" });
      }, timeoutMs);
      document.addEventListener("click", handler, true);
    });
  }

  async function detectQianchengPreviewKind(observeMs = 2000) {
    // 监听 iframe / dialog 出现 / window.open 调用
    const startTime = Date.now();
    const newIframes = [];
    const newDialogs = [];
    let windowOpenCalled = false;

    const origOpen = window.open;
    window.open = function () {
      windowOpenCalled = true;
      try {
        return origOpen.apply(this, arguments);
      } catch (e) { return null; }
    };

    const observer = new MutationObserver((muts) => {
      for (const m of muts) {
        for (const n of m.addedNodes) {
          if (n.nodeType !== 1) continue;
          if ((n.tagName || "") === "IFRAME") {
            newIframes.push({ src: n.getAttribute("src") || "", tag: n.tagName });
          }
          const cls = String(n.className || "");
          if (/dialog|modal|preview|popup|popover|drawer/i.test(cls) || n.getAttribute?.("role") === "dialog") {
            newDialogs.push({ tag: n.tagName, cls: cls.slice(0, 200) });
          }
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });

    await sleep(observeMs);

    observer.disconnect();
    window.open = origOpen;

    let kind = "unknown";
    if (windowOpenCalled) kind = "new_window";
    else if (newIframes.length) kind = "pdf_iframe";
    else if (newDialogs.length) kind = "html_popup";

    return {
      kind,
      evidence: {
        new_iframes: newIframes.slice(0, 3),
        new_dialogs: newDialogs.slice(0, 3),
        window_open_called: windowOpenCalled,
      },
      observed_ms: Date.now() - startTime,
    };
  }

  async function runQianchengLearningSession(runId) {
    if (!PLATFORM || PLATFORM.code !== "qiancheng") return;
    const keys = PLATFORM.storage_keys;
    const TOTAL_STEPS = 7;
    emit({ type: "qiancheng_learning_started", data: { run_id: runId, content_script_version: CONTENT_SCRIPT_VERSION, total_steps: TOTAL_STEPS } });

    async function captureStep(stepIdx, stepId, prompt, storageKey) {
      updateQianchengLearningBanner(stepIdx, TOTAL_STEPS, prompt);
      if (localStorage.getItem(storageKey)) {
        emit({ type: "qiancheng_learning_step_skipped", data: { step: stepId, reason: "already_learned" } });
        return true;
      }
      const result = await captureNextQianchengClick();
      if (!result.ok) {
        emit({ type: "qiancheng_learning_step_failed", data: { step: stepId, reason: result.reason } });
        updateQianchengLearningBanner(stepIdx, TOTAL_STEPS, "等待点击超时，请刷新页面重试");
        return false;
      }
      const record = {
        selector: result.selector,
        text_hint: result.chain[0]?.text || "",
        tag: result.chain[0]?.tag || "",
        class: result.chain[0]?.cls || "",
        parent_chain: result.chain.slice(1, 4).map((p) => `${p.tag}.${(p.cls || "").split(/\s+/)[0]}`),
        chain_detail: result.chain,
        client_xy: result.client_xy,
        captured_at: new Date().toISOString(),
      };
      try { localStorage.setItem(storageKey, JSON.stringify(record)); } catch {}
      emit({ type: "qiancheng_learning_step_completed", data: { step: stepId, selector: record.selector, descriptor: record.text_hint } });
      return true;
    }

    // Step 1/7: 左侧"人才沟通"菜单
    if (!await captureStep(1, "nav_menu_chat", "请点击左侧菜单的「人才沟通」", keys.learned_nav_menu_chat)) return;
    await sleep(500);

    // Step 2/7: 顶部"沟通中"标签
    if (!await captureStep(2, "tab_chatting", "请点击顶部菜单的「沟通中」标签", keys.learned_tab_chatting)) return;
    await sleep(500);

    // Step 3/7: 候选人卡片
    if (!await captureStep(3, "candidate_card", "请点击左侧候选人列表中任意一个候选人", keys.learned_candidate_card)) return;
    await sleep(500);

    // Step 4/7: 个人信息区（让用户点姓名，selector 推导时往上找信息容器）
    if (!await captureStep(4, "profile_info", "请点击右上方个人信息区的「候选人姓名」（用于提取姓名/年龄/学历做去重）", keys.learned_profile_info)) return;
    await sleep(500);

    // Step 5/7: "附件简历"按钮 + Step 6/7 自动检测预览形态
    updateQianchengLearningBanner(5, TOTAL_STEPS, "请点击右上角的「附件简历」按钮");
    if (!localStorage.getItem(keys.learned_attachment_btn)) {
      const result = await captureNextQianchengClick();
      if (!result.ok) {
        emit({ type: "qiancheng_learning_step_failed", data: { step: "attachment_btn", reason: result.reason } });
        updateQianchengLearningBanner(5, TOTAL_STEPS, "等待点击超时，请刷新页面重试");
        return;
      }
      const record = {
        selector: result.selector,
        text_hint: result.chain[0]?.text || "",
        tag: result.chain[0]?.tag || "",
        class: result.chain[0]?.cls || "",
        parent_chain: result.chain.slice(1, 4).map((p) => `${p.tag}.${(p.cls || "").split(/\s+/)[0]}`),
        chain_detail: result.chain,
        client_xy: result.client_xy,
        captured_at: new Date().toISOString(),
      };
      try { localStorage.setItem(keys.learned_attachment_btn, JSON.stringify(record)); } catch {}
      emit({ type: "qiancheng_learning_step_completed", data: { step: "attachment_btn", selector: record.selector, descriptor: record.text_hint } });
    }

    // Step 6/7: 自动检测预览形态（无需用户点击）
    updateQianchengLearningBanner(6, TOTAL_STEPS, "等待预览出现，自动识别形态中...");
    if (!localStorage.getItem(keys.learned_preview_form)) {
      const detection = await detectQianchengPreviewKind(2500);
      const record = { ...detection, captured_at: new Date().toISOString() };
      try { localStorage.setItem(keys.learned_preview_form, JSON.stringify(record)); } catch {}
      emit({ type: "qiancheng_learning_step_completed", data: { step: "preview_form", kind: detection.kind, evidence: detection.evidence } });
      if (detection.kind === "unknown") {
        updateQianchengLearningBanner(6, TOTAL_STEPS, "⚠ 未识别到预览形态（点击「清除学习记录」后重做可重试）");
        await sleep(3000);
      }
    }

    // Step 6/7 续：预览页"下载"按钮
    updateQianchengLearningBanner(6, TOTAL_STEPS, "预览已出现，请在预览页里点击「下载/保存」按钮");
    if (!localStorage.getItem(keys.learned_click)) {
      const result = await captureNextQianchengClick();
      if (!result.ok) {
        emit({ type: "qiancheng_learning_step_failed", data: { step: "download_btn", reason: result.reason } });
        updateQianchengLearningBanner(6, TOTAL_STEPS, "等待点击超时，请刷新页面重试");
        return;
      }
      const record = {
        selector: result.selector,
        text_hint: result.chain[0]?.text || "",
        tag: result.chain[0]?.tag || "",
        class: result.chain[0]?.cls || "",
        parent_chain: result.chain.slice(1, 4).map((p) => `${p.tag}.${(p.cls || "").split(/\s+/)[0]}`),
        chain_detail: result.chain,
        client_xy: result.client_xy,
        captured_at: new Date().toISOString(),
      };
      try { localStorage.setItem(keys.learned_click, JSON.stringify(record)); } catch {}
      emit({ type: "qiancheng_learning_step_completed", data: { step: "download_btn", selector: record.selector, descriptor: record.text_hint } });
    }

    // Step 7/7: 关闭弹窗按钮
    if (!await captureStep(7, "close_preview", "下载已触发。请点击预览弹窗的「关闭/×」按钮", keys.learned_close_preview)) return;

    dismissQianchengLearningBanner("🎉 7 步学习完成，请把 8 个 localStorage 值发给开发者写入代码");
    emit({ type: "qiancheng_learning_finished", data: { run_id: runId, all_keys_captured: isQianchengLearningComplete() } });
  }

  // ============================================================
  // Qiancheng (51job ehire) 采集主循环 + 专用 selectors
  // ============================================================

  const QIANCHENG_SELECTORS = {
    nav_menu_chat: "#sensor_talentcommunicate",
    tab_chatting: "#sensor_Bchat_communication",
    candidate_list_container: "#conversation-list .content-list",
    candidate_card: ".list-item",
    candidate_card_click_target: ".item-content",
    profile_info_container: ".info-main",
    profile_name: ".info-main .im_userName .username-text",
    profile_link_info: ".info-main .person-info .link-info",
    attachment_btn_scope: ".chat-user-operate",
    attachment_btn_text: "附件简历",
    attachment_works_btn_text: "附件作品",
    preview_container: ".annex-resume",
    preview_ready_marker: ".annex-resume .container-options-item.item-download",
    download_btn_sensor: "#sensor_Bchatinfo_xiazai",
    download_btn_class: ".container-options-item.item-download",
    close_preview: ".annex-resume .container-close",
    // 附件作品预览页是独立 Vue modal（data-v-0cc45215），下载是原生 <a class="download_a" href="blob:...">。
    // 简历那套 .annex-resume / .item-download selector 在作品 modal 不存在；必须独立 selector。
    attachment_works_download_anchor: 'a.download_a[href^="blob:"]',
  };

  const QIANCHENG_EDUCATION_KEYWORDS = ["博士", "硕士", "本科", "大专", "专科", "高中", "中专", "中职", "初中"];

  function extractQianchengContactInfo() {
    const info = { name: "待识别", age: "待识别", education: "待识别", raw_text: "" };
    const root = document.querySelector(QIANCHENG_SELECTORS.profile_info_container);
    if (!root) return info;
    const nameEl = root.querySelector(QIANCHENG_SELECTORS.profile_name) || root.querySelector(".username-text");
    if (nameEl) info.name = (nameEl.getAttribute("title") || nameEl.innerText || nameEl.textContent || "").trim() || "待识别";
    const linkEl = root.querySelector(QIANCHENG_SELECTORS.profile_link_info) || root.querySelector(".link-info");
    let segments = [];
    if (linkEl) {
      const title = linkEl.getAttribute("title") || "";
      const text = title || linkEl.innerText || linkEl.textContent || "";
      info.raw_text = text;
      segments = text.split(/[|｜/、,，]/).map((s) => s.trim()).filter(Boolean);
    }
    for (const seg of segments) {
      if (/\d+\s*岁/.test(seg) && info.age === "待识别") {
        info.age = seg.replace(/\s+/g, "");
        continue;
      }
      for (const kw of QIANCHENG_EDUCATION_KEYWORDS) {
        if (seg.includes(kw) && info.education === "待识别") {
          info.education = kw;
          break;
        }
      }
    }
    return info;
  }

  function extractQianchengTalkingPosition() {
    // 51 沟通页"沟通职位"元素已确认：<div id="sensor_Bchatinfo_switch" class="change-position">游戏策划</div>
    // 元素 textContent.trim() 直接就是职位文本（不带"沟通职位："标签前缀）。
    // 主策略：单 selector 直命中；兜底：少量容器 innerText 严格正则（防 51 改版）。
    try {
      const el = document.querySelector("#sensor_Bchatinfo_switch")
              || document.querySelector(".change-position");
      if (el) {
        const text = (el.textContent || "").trim();
        if (text && /[一-龥a-zA-Z0-9]/.test(text) && !/^沟通职位[：:\s]*$/.test(text)) {
          return text;
        }
      }
      const fallbackContainers = [
        ".chat-user-info", ".info-main", ".user-info-main",
        ".user-info", ".candidate-info", ".right-content",
      ];
      const STRICT_RE = /沟通职位[\s\-—–]*[：:]\s*([^\n\r│|｜·、/／]{2,50})/;
      for (const sel of fallbackContainers) {
        const c = document.querySelector(sel);
        if (!c) continue;
        const m = (c.innerText || "").match(STRICT_RE);
        if (m && m[1] && /[一-龥a-zA-Z0-9]/.test(m[1])) {
          return m[1].trim();
        }
      }
      return "";
    } catch (e) {
      try { console.warn("[qiancheng] extractTalkingPosition error", e); } catch (_e) {}
      return "";
    }
  }

  function simplifyQianchengTalkingPosition(raw) {
    // 对齐 bridge 端 _simplify_talking_position 规则：剥括号 + 首段 + 限 12 字符。
    // 防御：纯标点 / 空白 / 单冒号必须返回空串，不能进入文件名。
    if (!raw) return "";
    const s0 = String(raw).trim();
    if (!s0) return "";
    if (!/[一-龥a-zA-Z0-9]/.test(s0)) return ""; // 纯标点 / 空白 / 单冒号 → 拒绝
    let s = s0.replace(/[（(][^）)]*[）)]/g, "");
    s = s.split(/[/／、|｜・·]/)[0];
    s = s.trim();
    if (!s || !/[一-龥a-zA-Z0-9]/.test(s)) return "";
    if (s.length > 12) s = s.slice(0, 12);
    return s;
  }

  function getQianchengCandidateItems() {
    const container = document.querySelector(QIANCHENG_SELECTORS.candidate_list_container);
    if (!container) return [];
    return Array.from(container.querySelectorAll(QIANCHENG_SELECTORS.candidate_card));
  }

  function findQianchengAttachmentButton() {
    const scope = document.querySelector(QIANCHENG_SELECTORS.attachment_btn_scope);
    if (!scope) return null;
    // 精确 class 锚定：.file-type-text 文本为"附件简历"
    const el = scope.querySelector(".file-type-text");
    if (el && (el.innerText || el.textContent || "").trim() === QIANCHENG_SELECTORS.attachment_btn_text) return el;
    // 兜底：通用标签遍历
    const candidates = Array.from(scope.querySelectorAll("span, div, button, a"));
    for (const c of candidates) {
      if ((c.innerText || c.textContent || "").trim() === QIANCHENG_SELECTORS.attachment_btn_text) return c;
    }
    return null;
  }

  function findQianchengAttachmentWorksButton() {
    // 与「附件简历」共用 .file-type-text class，仅文本不同。
    const scope = document.querySelector(QIANCHENG_SELECTORS.attachment_btn_scope);
    if (!scope) return null;
    const all = scope.querySelectorAll(".file-type-text");
    for (const el of all) {
      if ((el.innerText || el.textContent || "").trim() === QIANCHENG_SELECTORS.attachment_works_btn_text) return el;
    }
    const candidates = Array.from(scope.querySelectorAll("span, div, button, a"));
    for (const c of candidates) {
      if ((c.innerText || c.textContent || "").trim() === QIANCHENG_SELECTORS.attachment_works_btn_text) return c;
    }
    return null;
  }

  async function waitForQianchengPreviewReady(timeoutMs = 5000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const marker = document.querySelector(QIANCHENG_SELECTORS.preview_ready_marker);
      if (marker) {
        const rect = marker.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) return marker;
      }
      await sleep(200);
    }
    return null;
  }

  async function waitForQianchengCandidateDetailReady(prevName, timeoutMs = 1800) {
    // 点击候选人后右侧详情区从上一个候选人切换需要时间；
    // 在 .info-main 姓名节点的文本与 prevName 不同时认为已切换。
    // prevName 为空（首个候选人）则等到任意非空姓名出现即返回。
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const root = document.querySelector(QIANCHENG_SELECTORS.profile_info_container);
      const nameEl = root
        ? (root.querySelector(QIANCHENG_SELECTORS.profile_name) || root.querySelector(".username-text"))
        : null;
      const currName = nameEl ? (nameEl.innerText || nameEl.textContent || "").trim() : "";
      if (currName) {
        if (!prevName) return currName;
        if (currName !== prevName) return currName;
      }
      await sleep(120);
    }
    return "";
  }

  function findQianchengDownloadButton() {
    const sensor = document.querySelector(QIANCHENG_SELECTORS.download_btn_sensor);
    if (sensor) return sensor;
    return document.querySelector(QIANCHENG_SELECTORS.download_btn_class);
  }

  function findQianchengClosePreviewButton() {
    return document.querySelector(QIANCHENG_SELECTORS.close_preview);
  }

  function isQianchengTabActive(el) {
    if (!el) return false;
    return el.classList.contains("active") || el.getAttribute?.("aria-selected") === "true";
  }

  function looksLikeQianchengChattingRoute() {
    // URL / 标题 / 面包屑任一项含沟通中关键词即认为已在沟通中页面（用于辅助判定）。
    try {
      const url = String(location.href || "");
      if (/talent[-_]?communicate|chat|communication|chatting/i.test(url)) return true;
    } catch {}
    try {
      const title = String(document.title || "");
      if (title.includes("沟通中") || title.includes("人才沟通")) return true;
    } catch {}
    try {
      const breadcrumb = document.querySelector(".breadcrumb, .crumbs, .ant-breadcrumb");
      if (breadcrumb && (breadcrumb.innerText || "").includes("沟通中")) return true;
    } catch {}
    return false;
  }

  async function ensureQianchengOnChattingPage() {
    // v2.30.0：不再以"列表容器存在"作为短路条件 —— 因为 51 SPA 结构下"全部候选人"页面
    // 也可能存在同名容器，导致函数判定"已在沟通中"而跳过导航。改为每次都强制点击
    // 「人才沟通」菜单 → 「沟通中」tab，确保一定落在沟通中。重复点击对已在沟通中的
    // 用户只是无害的二次激活。
    try { console.log("[qiancheng nav] enter ensureQianchengOnChattingPage", { url: location.href, title: document.title }); } catch {}

    const navMenu = document.querySelector(QIANCHENG_SELECTORS.nav_menu_chat);
    if (navMenu) {
      try { console.log("[qiancheng nav] click talent menu #sensor_talentcommunicate"); } catch {}
      clickElementDirect(navMenu);
      await sleep(900);
    } else {
      try { console.warn("[qiancheng nav] talent menu (#sensor_talentcommunicate) not found"); } catch {}
    }

    let tab = document.querySelector(QIANCHENG_SELECTORS.tab_chatting);
    if (tab) {
      try { console.log("[qiancheng nav] click chat tab #sensor_Bchat_communication; activeBefore=", isQianchengTabActive(tab)); } catch {}
      clickElementDirect(tab);
      await sleep(700);
    } else {
      try { console.warn("[qiancheng nav] chat tab (#sensor_Bchat_communication) not found, will wait"); } catch {}
    }

    // 等到目标 tab 可见且呈激活态 + 列表容器渲染出来才算就绪。最长等 6 秒。
    const deadline = Date.now() + 6000;
    while (Date.now() < deadline) {
      tab = tab || document.querySelector(QIANCHENG_SELECTORS.tab_chatting);
      const active = isQianchengTabActive(tab);
      const listContainer = document.querySelector(QIANCHENG_SELECTORS.candidate_list_container);
      const items = listContainer ? listContainer.querySelectorAll(QIANCHENG_SELECTORS.candidate_card).length : 0;
      const routeHint = looksLikeQianchengChattingRoute();
      // 任意两条同时成立即视为已就绪（防 51 改版破坏单一信号）：
      //   1) tab 激活
      //   2) 列表容器内有 list-item
      //   3) URL/标题/面包屑命中沟通中关键词
      const signals = (active ? 1 : 0) + (items > 0 ? 1 : 0) + (routeHint ? 1 : 0);
      if (signals >= 2) {
        try { console.log("[qiancheng nav] on chatting page", { active, items, routeHint, url: location.href }); } catch {}
        emit({ type: "qiancheng_navigation_status", data: { status: "on_chatting_page", route: location.pathname || location.href, tab_active: active, list_items: items, route_hint: routeHint } });
        return true;
      }
      await sleep(250);
    }

    const finalTab = document.querySelector(QIANCHENG_SELECTORS.tab_chatting);
    const finalActive = isQianchengTabActive(finalTab);
    const finalListContainer = document.querySelector(QIANCHENG_SELECTORS.candidate_list_container);
    const finalItems = finalListContainer ? finalListContainer.querySelectorAll(QIANCHENG_SELECTORS.candidate_card).length : 0;
    try { console.warn("[qiancheng nav] navigation_failed", { tabFound: !!finalTab, active: finalActive, list_items: finalItems, url: location.href }); } catch {}
    emit({ type: "qiancheng_navigation_status", data: { status: "navigation_failed", reason: "timeout_waiting_chatting_page", tab_found: !!finalTab, tab_active: finalActive, list_items: finalItems, route: location.pathname || location.href } });
    return Boolean(finalListContainer);
  }

  function buildQianchengCandidateKey(info) {
    const name = (info.name || "").trim();
    const age = (info.age || "").trim();
    const education = (info.education || "").trim();
    if (!name || name === "待识别") return "";
    return `qiancheng|profile|${name}|${age}|${education}`;
  }

  async function waitForQianchengWorksDownloadAnchor(timeoutMs = 6000) {
    // 作品 modal 是异步渲染：href="blob:..." 通常要等几百 ms 才挂上。
    // 命中条件：a.download_a[href^="blob:"] 且 visible。
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const anchors = document.querySelectorAll(QIANCHENG_SELECTORS.attachment_works_download_anchor);
      for (const a of anchors) {
        const rect = a.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) return a;
      }
      await sleep(200);
    }
    return null;
  }

  async function tryDownloadQianchengAttachmentWorks(candidateId, signature, info) {
    // 简历下载并 persist_ack=saved 之后才会进来。整段是加分项：任何一步失败都
    // emit qiancheng_attachment_works_skipped、return false，不影响主流程。
    // 区别于简历分支的关键：download_intent 带 variant="attachment_works"，
    // background.js 通过 spread 透传给 resume_downloaded 事件，bridge 据此走 _save_attachment_works。
    const btn = findQianchengAttachmentWorksButton();
    if (!btn) {
      // 没有作品按钮属于多数情况，静默 console.log，不发事件占用日志条。
      try { console.debug("[qiancheng works] no attachment_works button", { sig: signature }); } catch {}
      return false;
    }
    emit({ type: "qiancheng_attachment_works_button_found", data: { candidate_id: candidateId, candidate_signature: signature } });

    clickElementReliably(btn);
    await sleep(400);

    // 注意：不能复用 waitForQianchengPreviewReady（那个等的是 .annex-resume 简历 modal 的 selector）。
    // 作品 modal 是独立 Vue scope，必须等它自己的下载锚点出现。
    const downloadAnchor = await waitForQianchengWorksDownloadAnchor(6000);
    if (!downloadAnchor) {
      emit({ type: "qiancheng_attachment_works_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "works_download_anchor_not_found" } });
      // 关闭对话框：作品 modal 通常也响应简历那套 close 按钮，不行就 ESC 兜底。
      const closeBtn = findQianchengClosePreviewButton();
      if (closeBtn) { clickElementReliably(closeBtn); await sleep(400); }
      else { document.body?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); }
      return false;
    }

    const downloadRequestId = makeDownloadRequestId(candidateId, signature);
    emit({
      type: "download_intent",
      data: {
        candidate_id: candidateId,
        candidate_signature: signature,
        candidate_info: info,
        expected_filename: `${signature}_works.pdf`,
        download_request_id: downloadRequestId,
        variant: "attachment_works",
      },
    });
    const resultPromise = waitForDownloadResult(downloadRequestId, 20000);
    // 原生 <a download href="blob:..."> 用 click() 即可触发 Chrome downloads；不要走 dispatchEvent，
    // 部分 Chromium 版本对合成 click 不下载 blob。
    try { downloadAnchor.click(); }
    catch (_e) { clickElementReliably(downloadAnchor); }
    const downloadResult = await resultPromise;
    if (downloadResult.ok) {
      // finalizeDownloadWithPersistAck 等的是 persist_ack；works 分支 bridge 也会回 ack（status="works_saved"）。
      await finalizeDownloadWithPersistAck(candidateId, signature, downloadRequestId, "qiancheng_attachment_works");
    } else {
      emit({ type: "qiancheng_attachment_works_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: downloadResult.reason || "download_failed" } });
    }

    const closeBtn = findQianchengClosePreviewButton();
    if (closeBtn) {
      clickElementReliably(closeBtn);
      await sleep(400);
    } else {
      document.body?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      await sleep(300);
    }
    return downloadResult.ok;
  }

  async function qianchengCollectLoop() {
    emit({ type: "page_ready", data: { url: location.href, platform: "qiancheng" } });
    const ready = await ensureQianchengOnChattingPage();
    if (!ready) {
      emit({ type: "error", data: { message: "未能进入沟通中页面，请手动点击「人才沟通」并切到「沟通中」标签后再开始采集", stage: "navigation" } });
      state = "idle";
      return;
    }

    const items = getQianchengCandidateItems();
    if (items.length === 0) {
      emit({ type: "error", data: { message: "未找到候选人列表（沟通中标签为空）", stage: "scan" } });
      state = "idle";
      return;
    }
    emit({ type: "collect_progress", data: { scanned_count: 0, current_index: 0, total_in_list: items.length } });

    const seenSignatures = new Set();
    const dedupSignatures = new Set(Array.isArray(config.boss_candidate_signatures) ? config.boss_candidate_signatures : []);
    const dedupKeys = new Set(Array.isArray(config.boss_candidate_keys) ? config.boss_candidate_keys : []);
    let prevCandidateName = "";

    for (let i = 0; i < items.length && results.completed < config.max_resumes; i++) {
      if (state === "stopped") break;
      await waitForPause();
      if (state === "stopped") break;

      results.currentIndex = i;
      const card = items[i];
      const clickTarget = card.querySelector(QIANCHENG_SELECTORS.candidate_card_click_target) || card;
      try {
        card.scrollIntoView({ block: "center" });
        await sleep(120);
        clickElementReliably(clickTarget);
        await sleep(600);
      } catch (error) {
        emit({ type: "candidate_skipped", data: { candidate_signature: `index_${i}`, reason: "click_failed", error: String(error) } });
        results.skipped++;
        emit({ type: "collect_progress", data: { scanned_count: i + 1, current_index: i, downloaded: results.completed, skipped: results.skipped } });
        continue;
      }

      // 等右侧详情面板真正切换到本候选人，再去取联系信息和沟通职位（前轮日志显示
      // 没等待时会抓到上一个候选人的"沟通职位"，且姓名仍是"待识别"）。
      const switchedName = await waitForQianchengCandidateDetailReady(prevCandidateName, 1800);
      // 给 Vue/React 子节点（沟通职位行常常异步加载）再补 350ms 渲染窗口。
      await sleep(350);

      const info = extractQianchengContactInfo();
      const signature = `${info.name}/${info.age}/${info.education}`;
      const candidateId = `${activeRunId || "run"}_${i}_${signature}`;
      if (info.name && info.name !== "待识别") {
        prevCandidateName = info.name;
      } else if (switchedName) {
        prevCandidateName = switchedName;
      }

      const talkingRaw = extractQianchengTalkingPosition();
      const talkingSimplified = simplifyQianchengTalkingPosition(talkingRaw);
      if (talkingSimplified) {
        emit({ type: "qiancheng_talking_position", data: { candidate_signature: signature, raw: talkingRaw, simplified: talkingSimplified } });
      } else {
        // 静默退出问题：raw 抓到但简化掉 / 完全没抓到都走这里，console.log 便于排查 DOM 结构。
        try { console.log("[qiancheng] talking_position not found", { sig: signature, raw: talkingRaw }); } catch (_e) {}
        emit({ type: "qiancheng_talking_position_skip", data: { candidate_signature: signature, raw: talkingRaw || "", reason: talkingRaw ? "simplified_empty" : "not_found" } });
      }
      info.talking_position = talkingSimplified;
      info.talking_position_raw = talkingRaw;

      emit({ type: "candidate_clicked", data: { ...info, candidate_id: candidateId, candidate_signature: signature, talking_position: talkingSimplified, talking_position_raw: talkingRaw, index: i } });

      if (signature === "待识别/待识别/待识别") {
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "candidate_info_unrecognized", raw_text: info.raw_text } });
        results.skipped++;
        continue;
      }

      if (seenSignatures.has(signature)) {
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "duplicate_in_run" } });
        results.skipped++;
        continue;
      }
      seenSignatures.add(signature);

      const normalized = normalizeBossCandidateSignature(signature);
      const candidateKey = buildQianchengCandidateKey(info);
      const sigHit = dedupSignatures.has(signature) || dedupSignatures.has(normalized);
      const keyHit = candidateKey && dedupKeys.has(candidateKey);
      emit({ type: "boss_pre_dedup_checked", data: { candidate_id: candidateId, candidate_signature: signature, normalized_signature: normalized, candidate_key: candidateKey, key_hit: keyHit, signature_hit: sigHit } });
      if (sigHit || keyHit) {
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "boss_dedup_hit", candidate_key: candidateKey } });
        results.skipped++;
        continue;
      }

      const attachmentBtn = findQianchengAttachmentButton();
      if (!attachmentBtn) {
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "no_resume_attachment" } });
        results.skipped++;
        continue;
      }

      emit({ type: "resume_attachment_click_dispatched", data: { candidate_id: candidateId, candidate_signature: signature, button_state: "bright", button_text: "附件简历" } });
      clickElementReliably(attachmentBtn);
      await sleep(400);

      const previewMarker = await waitForQianchengPreviewReady(5000);
      if (!previewMarker) {
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "resume_preview_not_found" } });
        results.skipped++;
        continue;
      }
      emit({ type: "resume_preview_detected", data: { candidate_id: candidateId, candidate_signature: signature, preview_source: "qiancheng_annex_resume" } });

      const downloadBtn = findQianchengDownloadButton();
      if (!downloadBtn) {
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "download_button_not_found" } });
        results.skipped++;
        const closeBtn = findQianchengClosePreviewButton();
        if (closeBtn) { clickElementReliably(closeBtn); await sleep(400); }
        continue;
      }

      const downloadRequestId = makeDownloadRequestId(candidateId, signature);
      emit({ type: "download_intent", data: { candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf`, download_request_id: downloadRequestId } });
      const resultPromise = waitForDownloadResult(downloadRequestId, 20000);
      clickElementReliably(downloadBtn);
      const downloadResult = await resultPromise;
      let resumeSaved = false;
      if (downloadResult.ok) {
        await finalizeDownloadWithPersistAck(candidateId, signature, downloadRequestId, "qiancheng_sensor_download");
        dedupSignatures.add(signature);
        if (candidateKey) dedupKeys.add(candidateKey);
        resumeSaved = true;
      } else {
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: downloadResult.reason || "download_failed" } });
        results.skipped++;
      }

      const closeBtn = findQianchengClosePreviewButton();
      if (closeBtn) {
        clickElementReliably(closeBtn);
        await sleep(500);
      }

      // 简历下载成功后再独立尝试附件作品。失败不阻断、不计入 skipped。
      if (resumeSaved) {
        try {
          await tryDownloadQianchengAttachmentWorks(candidateId, signature, info);
        } catch (error) {
          emit({ type: "qiancheng_attachment_works_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "exception", error: String(error) } });
        }
      }

      emit({ type: "collect_progress", data: { scanned_count: i + 1, current_index: i, downloaded: results.completed, skipped: results.skipped } });
      await sleep(Math.max(800, config.interval_ms || 1500));
    }

    state = "idle";
    if (!collectFinishedEmitted) {
      emit({ type: "collect_finished", data: { total_completed: results.completed, total_skipped: results.skipped } });
      collectFinishedEmitted = true;
    }
  }

  // ============================================================
  // Zhilian (智联招聘 rd5.zhaopin.com) 采集辅助函数 + 主循环
  // ============================================================
  // rd5.zhaopin.com 使用泛化 CSS class，因此依赖视口坐标区分左右面板：
  //   左侧候选人列表面板：x ~170-440
  //   右侧详情/聊天面板：x ≥ 420
  // ============================================================

  function zhilianIsVisible(el) {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none" && style.opacity !== "0";
  }

  function getZhilianCandidateTargets(seenSet) {
    // 智联沟通中候选人卡片 DOM 真实样本（DevTools console 采样 2026-05-23）：
    //   <div class="im-session-list__virtual--box">  L=180 T=226 W=247 H=4296（虚拟滚动容器, 30 子节点）
    //     <div class="im-session-item km-list__item">  L=180 T=226 W=247 H=72  ← 真候选人卡片
    //       <div class="im-session-item__box">          内含头像/姓名/职位/最近消息预览
    //         <div class="km-list-item__avatar">         L=188 W=40 H=40 内嵌未读红点
    //           <div class="km-badge im-session-item__unread">
    //             <sup class="km-badge__item km-badge__item--fixed">  数字角标
    //         <div class="km-list-item__title">         姓名 + 职位（最稳定文本）
    //   <div class="greeting-new-entry">  L=180 T=146  ← noise，"快速处理新招呼"卡片，靠 skip 正则排除
    //
    // 旧版基于 [class*="session"]/[class*="item"] 通配 + bbox 几何过滤，命中率脆弱；
    // 现在 class 已知，直接精确锚定 + 视口可见性判断即可。
    const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
    const skip = (text) => /快速处理|新招呼|99\+人|全部职位|筛选|批量/.test(text);
    const normalize = (text) => (text || "").replace(/\s+/g, " ").trim();

    const cards = Array.from(document.querySelectorAll(".im-session-item.km-list__item"));
    const output = [];
    const used = new Set();

    for (const el of cards) {
      if (!zhilianIsVisible(el)) continue;
      const rect = el.getBoundingClientRect();
      // 仅扫描视口内可见的卡片（虚拟滚动下方未渲染的卡片 rect.top > viewportH，跳过）。
      if (rect.bottom < 100 || rect.top > viewportH - 12) continue;
      const signature = normalize(el.innerText || el.textContent).slice(0, 220);
      if (!signature || signature.length < 4) continue;
      if (skip(signature)) continue;
      if (seenSet.has(signature) || used.has(signature)) continue;
      used.add(signature);
      // clickX 偏右一点（0.55），避开左侧头像区，落到主信息块。
      const clickX = rect.left + rect.width * 0.55;
      const clickY = rect.top + rect.height / 2;
      output.push({ element: el, name: signature, index: output.length, clickX, clickY });
      if (output.length >= 24) break;
    }
    return output;
  }

  function extractZhilianContactInfo() {
    const normalize = (text) => (text || "").replace(/\s+/g, " ").trim();
    const degreePattern = /博士|硕士|研究生|本科|大专|专科|高中|中专/;
    const info = { name: "待识别", age: "待识别", education: "待识别", job_title: "", phone: "", raw_text: "" };

    // 精确 class 优先提取
    const nameEl = document.querySelector("span.new-resume-basic__name");
    const infosEl = document.querySelector("div.new-resume-basic__infos");

    if (nameEl && infosEl) {
      info.name = (nameEl.getAttribute("title") || nameEl.textContent || "").trim();
      const infosText = normalize(infosEl.textContent);
      info.raw_text = `${info.name} ${infosText}`.slice(0, 200);
      const ageMatch = infosText.match(/(\d{2})\s*岁/);
      if (ageMatch) info.age = `${ageMatch[1]}岁`;
      const eduMatch = infosText.match(degreePattern);
      if (eduMatch) {
        let edu = eduMatch[0];
        if (edu === "研究生") edu = "硕士";
        if (edu === "专科") edu = "大专";
        info.education = edu;
      }
      // 精确 class 提取电话
      const phoneLabelEl = document.querySelector(".hover-resume-basic__phone--label");
      if (phoneLabelEl) {
        const phoneSibling = phoneLabelEl.nextElementSibling;
        const phoneText = normalize(phoneSibling ? phoneSibling.textContent : "");
        const pm = phoneText.match(/(?<!\d)(1[3-9]\d(?:\s*\d){8})(?!\d)/);
        if (pm) info.phone = pm[1].replace(/\s/g, "");
      }
      if (!info.phone) {
        const phoneMatch = infosText.match(/(?<!\d)(1[3-9]\d(?:\s*\d){8})(?!\d)/);
        if (phoneMatch) info.phone = phoneMatch[1].replace(/\s/g, "");
      }
      const expectMatch = infosText.match(/期望[:：]\s*([^·\n\r]{1,40})\s*·\s*([^·\n\r,，；;]{2,40})\s*·\s*([^·\n\r,，；;]{2,40})/);
      if (expectMatch) info.job_title = expectMatch[2].trim();
    } else {
      // 兜底：通用标签 + 坐标扫描
      const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 1440;
      const rightLeft = Math.max(420, viewportWidth * 0.42);
      const summaryCore = /\d{2}\s*岁|博士|硕士|研究生|本科|大专|专科|高中|中专|离职|在职|期望[:：]|1[3-9]\d(?:\s*\d){8}/;
      const excludes = /聊天记录|快捷回复|发送|表情|请输入|已读|未读|要附件简历|查看附件简历|下载简历|工作经历|项目经历|教育经历|自我评价|求职信/;

      const nodes = Array.from(document.querySelectorAll("aside, section, header, article, div"))
        .filter((el) => zhilianIsVisible(el))
        .map((el) => {
          const rect = el.getBoundingClientRect();
          const text = normalize(el.innerText || el.textContent || el.getAttribute("title") || el.getAttribute("aria-label"));
          const cls = String(el.className || "");
          const area = rect.width * rect.height;
          const topSummaryZone = rect.left >= rightLeft && rect.top >= 40 && rect.top <= 360 && rect.width >= 260 && rect.height >= 36 && rect.height <= 360;
          const classScore = /candidate|profile|detail|resume|user|person|talent|card|info|basic|summary/i.test(cls) ? 30 : 0;
          const textScore = (summaryCore.test(text) ? 50 : 0) + (/期望[:：].*·.*·/.test(text) ? 45 : 0) + (/1[3-9]\d(?:\s*\d){8}/.test(text) ? 35 : 0);
          return { el, rect, text, area, topSummaryZone, score: classScore + textScore + Math.max(0, 40 - rect.top / 10) + Math.min(area / 12000, 20) };
        })
        .filter((item) => item.topSummaryZone && item.text && item.text.length >= 4 && item.text.length <= 1200 && !excludes.test(item.text) && summaryCore.test(item.text));

      nodes.sort((a, b) => b.score - a.score || a.rect.top - b.rect.top || b.area - a.area);
      const best = nodes[0];
      if (!best) return { name: "待识别", age: "待识别", education: "待识别", job_title: "", phone: "", raw_text: "" };

      const parts = [];
      const seen = new Set();
      const add = (text) => {
        const line = normalize(text);
        if (!line || seen.has(line) || line.length > 260 || excludes.test(line)) return;
        seen.add(line);
        parts.push(line);
      };
      add(best.text);
      for (const child of Array.from(best.el.querySelectorAll("div, span, p, li")).filter(zhilianIsVisible)) {
        add(child.innerText || child.textContent || child.getAttribute("title") || child.getAttribute("aria-label"));
        if (parts.length >= 16) break;
      }
      const merged = parts.join(" ");
      info.raw_text = merged.slice(0, 200);

      const summaryMatch = merged.match(/([一-龥]{2,4}|[A-Za-z][A-Za-z .·-]{1,30})\s+(\d{2})\s*岁\s*(博士|硕士|研究生|本科|大专|专科|高中|中专)?/);
      if (summaryMatch) {
        info.name = summaryMatch[1].trim();
        info.age = `${summaryMatch[2]}岁`;
        let edu = summaryMatch[3] || "";
        if (edu === "研究生") edu = "硕士";
        if (edu === "专科") edu = "大专";
        if (edu) info.education = edu;
      } else {
        const ageMatch = merged.match(/(\d{2})\s*岁/);
        if (ageMatch) info.age = `${ageMatch[1]}岁`;
        const eduMatch = merged.match(degreePattern);
        if (eduMatch) {
          let edu = eduMatch[0];
          if (edu === "研究生") edu = "硕士";
          if (edu === "专科") edu = "大专";
          info.education = edu;
        }
        if (ageMatch) {
          const beforeAge = merged.slice(0, ageMatch.index).trim();
          const nameMatches = Array.from(beforeAge.matchAll(/[一-龥]{2,4}/g)).map((m) => m[0]);
          if (nameMatches.length > 0) info.name = nameMatches[nameMatches.length - 1];
        }
      }
      const expectMatch = merged.match(/期望[:：]\s*([^·\n\r]{1,40})\s*·\s*([^·\n\r,，；;]{2,40})\s*·\s*([^·\n\r,，；;]{2,40})/);
      if (expectMatch) info.job_title = expectMatch[2].trim();
      const phoneMatch = merged.match(/(?<!\d)(1[3-9]\d(?:\s*\d){8})(?!\d)/);
      if (phoneMatch) info.phone = phoneMatch[1].replace(/\s/g, "");
    }
    const jobTitleEl = document.querySelector(".im-three-list__panel--job--title");
    info.talking_position = jobTitleEl
      ? (jobTitleEl.getAttribute("title") || jobTitleEl.textContent || "").trim()
      : "";
    return info;
  }

  function findZhilianAttachmentButton() {
    // hover 链 + DOM 采样确认（2026-05-23）的两个稳定 class：
    //   首选：.session-new-action a.km-button —— 底部固定 footer 的"查看附件简历"按钮。
    //         位置稳定（永远在右下角 footer 工具栏），不会随聊天滚动消失。
    //   兜底：.im-attachment-card__button —— 聊天气泡内附件卡片按钮，会随聊天滚动浮动甚至滚出视口。
    // 旧版 getZhilianAttachmentButtonState 用 `[class*="button"], [class*="btn"], [class*="attach"]` 通配
    // 接近全 DOM 扫描，每节点 getComputedStyle + getBoundingClientRect，智联重 DOM 下耗时 30+s 卡死无 emit。
    const footerBtns = Array.from(document.querySelectorAll(".session-new-action a.km-button, .session-new-action button, .session-new-action--left a, .session-new-action--left button"));
    const cardBtns = Array.from(document.querySelectorAll(".im-attachment-card__button"));
    const all = [...footerBtns, ...cardBtns];

    let best = null;
    const priority = { view: 3, already_requested: 2, request: 1 };

    for (const el of all) {
      if (!zhilianIsVisible(el)) continue;
      const text = (el.innerText || el.textContent || "").trim();
      let kind = "";
      if (/查看附件简历|查看简历附件|下载附件简历|下载简历附件/.test(text)) kind = "view";
      else if (/已向对方要附件简历|已要附件简历|已索要|附件简历索要中/.test(text)) kind = "already_requested";
      else if (/要附件简历|索要附件简历|请求附件简历|获取附件简历|要简历/.test(text)) kind = "request";
      if (!kind) continue;

      // 内层 button / a 是 React onClick 真实挂载点；DIV.im-attachment-card__button 是包装层。
      const inner = el.matches("button, a") ? el : el.querySelector("button, a");
      const target = inner || el;

      if (!best || priority[kind] > priority[best.state]) {
        best = { element: target, state: kind, text: text.slice(0, 40), wrapperCls: String(el.className || "").slice(0, 60) };
      }
    }
    return best;
  }

  function getZhilianAttachmentButtonState() {
    const found = findZhilianAttachmentButton();
    if (!found) return "missing";
    const el = found.element;
    const cls = String(el.className || "").toLowerCase();
    const style = getComputedStyle(el);
    const disabled =
      el.disabled === true ||
      el.getAttribute("disabled") !== null ||
      el.getAttribute("aria-disabled") === "true" ||
      cls.includes("is-disabled") ||
      /(^|[-_\s])disabled($|[-_\s])/.test(cls) ||
      style.pointerEvents === "none";
    if (disabled && (found.state === "view" || found.state === "request")) return "missing";
    emit({
      type: "zhilian_attachment_button_found",
      data: {
        state: found.state,
        text: found.text,
        wrapper_cls: found.wrapperCls,
        target_tag: el.tagName,
        target_cls: String(el.className || "").slice(0, 60),
      },
    });
    return found.state;
  }

  function clickZhilianViewAttachment() {
    const found = findZhilianAttachmentButton();
    if (!found || found.state !== "view") return false;
    // 用 Direct 派发完整事件序列；clickElementReliably 会 closest 跳出，对气泡内/footer tab 群都是误触陷阱。
    clickElementDirect(found.element);
    return true;
  }

  function clickZhilianViewAttachmentButton() {
    // 智联"查看附件简历"按钮的 React onClick 会 window.open 弹出 attachment.zhaopin.com/...downloadFileTemporary
    // 新 tab，该 tab 的 URL 本身就是 PDF 直链。content.js 只负责点按钮 + 提前发 download_intent 占座，
    // background.js 用 chrome.tabs.onUpdated 监听新 tab URL，命中后调 chrome.downloads.download 直接下载。
    // 浏览器需在"允许弹出式窗口和重定向"白名单里加 rd5/rd6.zhaopin.com，否则弹窗被拦。
    const found = findZhilianAttachmentButton();
    if (!found || found.state !== "view") return { clicked: false };
    clickElementDirect(found.element);
    emit({ type: "zhilian_view_attachment_clicked", data: { wrapper_cls: found.wrapperCls, text: found.text } });
    return { clicked: true };
  }

  function clickZhilianAttachmentCard() {
    const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
    const chatLeft = Math.max(420, Math.round(viewportW * 0.34));
    const cards = document.querySelectorAll('[class*="file"], [class*="attach"], [class*="card"], [class*="message"]');
    for (const el of cards) {
      if (!zhilianIsVisible(el)) continue;
      const rect = el.getBoundingClientRect();
      if (rect.left < chatLeft || rect.width > 600 || rect.height > 200 || rect.height < 20) continue;
      const text = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
      if (/\.(pdf|doc|docx|zip|rar)/i.test(text) || /简历|resume/i.test(text)) {
        el.click();
        return true;
      }
    }
    return false;
  }

  function scrollZhilianCandidateList(dy) {
    // 虚拟滚动列表：从最后一张卡片反向走 parent 找真正的滚动容器，
    // 设置 scrollTop 后必须派发 scroll 事件，否则 React 虚拟滚动不会渲染新节点。
    const cards = document.querySelectorAll(".im-session-item.km-list__item");
    const anchor = cards.length ? cards[cards.length - 1] : null;
    if (!anchor) return false;
    let node = anchor.parentElement;
    while (node && node !== document.body) {
      if (node.scrollHeight > node.clientHeight + 10) {
        node.scrollTop = node.scrollTop + dy;
        node.dispatchEvent(new Event("scroll", { bubbles: true }));
        return true;
      }
      node = node.parentElement;
    }
    // 兜底：scrollIntoView 最后一张卡片底部 + wheel 事件模拟
    try {
      anchor.scrollIntoView({ block: "end" });
      anchor.dispatchEvent(new WheelEvent("wheel", { deltaY: dy, bubbles: true }));
      return true;
    } catch {}
    return false;
  }

  function scrollZhilianCandidateListToTop() {
    const firstCard = document.querySelector(".im-session-item.km-list__item");
    let node = firstCard ? firstCard.parentElement : null;
    while (node && node !== document.body) {
      if (node.scrollHeight > node.clientHeight + 10) {
        node.scrollTop = 0;
        return true;
      }
      node = node.parentElement;
    }
    return false;
  }

  async function waitZhilianDetailSwitch(previousName, timeoutMs) {
    const deadline = Date.now() + (timeoutMs || 3000);
    let lastInfo = null;
    while (Date.now() < deadline) {
      lastInfo = extractZhilianContactInfo();
      const nameOk = lastInfo.name && lastInfo.name !== previousName && lastInfo.name !== "未知";
      const ageOk = lastInfo.age && lastInfo.age !== "未知";
      if (nameOk && ageOk) return lastInfo;
      await sleep(80);
    }
    return lastInfo;
  }

  function findDeepestClickable(el) {
    // 智联左导菜单是 li > div > a > span 多层嵌套，外层 div 没有 React click handler。
    // 优先点最深的 a / button / [role=button]，否则回落到 el 本身。
    if (!el) return el;
    const inner = el.querySelector('a, button, [role="button"], [role="menuitem"]');
    return inner || el;
  }

  function findZhilianLeftNavChatEntry() {
    // 精确 class 锚定：.app-menu-item__label 文本为"聊天"
    const labels = document.querySelectorAll(".app-menu-item__label");
    for (const el of labels) {
      if ((el.textContent || "").trim() === "聊天") {
        const wrapper = el.closest(".app-menu-item-content-normal__label") || el;
        emit({ type: "zhilian_nav", data: { step: "left_chat_menu_search", found: 1, method: "precise_class" } });
        return wrapper;
      }
    }
    // 兜底：通用标签扫描 + 几何约束
    const NOISE = /(微信沟通|已获取微信|获取微信|打招呼)/;
    const candidates = [];
    const all = document.querySelectorAll("a, button, span, li, div, [role='menuitem'], [class*='menu'], [class*='nav'], [class*='side']");
    for (const el of all) {
      const ownText = (el.innerText || el.textContent || "").trim();
      const compact = ownText.replace(/\s/g, "");
      const label = (el.getAttribute("aria-label") || "").trim();
      const title = (el.getAttribute("title") || "").trim();
      const hit = /聊天/.test(compact) || /聊天/.test(label) || /聊天/.test(title);
      if (!hit) continue;
      if (compact.length > 16) continue;
      if (NOISE.test(ownText) || NOISE.test(label) || NOISE.test(title)) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      if (!(rect.left < 95 && rect.top > 60)) continue;
      if (!(rect.height >= 24 && rect.height <= 80)) continue;
      if (!(rect.width <= 200)) continue;
      candidates.push({ el, rect, ownText, label, title });
    }
    if (!candidates.length) {
      emit({ type: "zhilian_nav", data: { step: "left_chat_menu_search", found: 0 } });
      return null;
    }
    candidates.sort((a, b) => a.rect.left - b.rect.left || a.rect.width - b.rect.width || a.rect.top - b.rect.top);
    emit({
      type: "zhilian_nav",
      data: {
        step: "left_chat_menu_search",
        found: candidates.length,
        method: "fallback_geometry",
        sample: candidates.slice(0, 3).map((c) => ({
          tag: c.el.tagName,
          text: c.ownText.slice(0, 20),
          rect: { left: Math.round(c.rect.left), top: Math.round(c.rect.top), w: Math.round(c.rect.width), h: Math.round(c.rect.height) },
        })),
      },
    });
    return candidates[0].el;
  }

  function findZhilianTopChattingTab() {
    // hover 链采样确认：tab class 为 .im-custom-filter__item，文本"沟通中"。
    // 优先 class 锚定，bbox 仅作 sanity check（防 class 名漂移时整页搜出无关元素）。
    const NOISE = /(微信沟通|已获取微信|获取微信|打招呼)/;
    const candidates = [];
    const all = document.querySelectorAll(".im-custom-filter__item, [class*='filter__item']");
    for (const el of all) {
      const text = (el.textContent || "").trim();
      if (!text) continue;
      if (NOISE.test(text)) continue;
      const compact = text.replace(/\s/g, "");
      if (!/沟通中/.test(compact)) continue;
      if (compact.length > 8) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      candidates.push({ el, rect, text });
    }
    if (!candidates.length) {
      emit({ type: "zhilian_nav", data: { step: "top_chatting_tab_search", found: 0 } });
      return null;
    }
    candidates.sort((a, b) => a.rect.top - b.rect.top || a.rect.left - b.rect.left);
    emit({
      type: "zhilian_nav",
      data: {
        step: "top_chatting_tab_search",
        found: candidates.length,
        sample: candidates.slice(0, 3).map((c) => ({
          tag: c.el.tagName,
          cls: String(c.el.className || "").slice(0, 60),
          text: c.text.slice(0, 20),
          rect: { left: Math.round(c.rect.left), top: Math.round(c.rect.top), w: Math.round(c.rect.width), h: Math.round(c.rect.height) },
        })),
      },
    });
    return candidates[0].el;
  }

  function isZhilianChattingTabActive() {
    // hover 链采样确认：当前激活 tab 的 class 为 "im-custom-filter__item im-custom-filter__item--active"。
    // 只有当 --active 节点的文本含"沟通中"，才能视为已在正确 tab。
    const actives = document.querySelectorAll(".im-custom-filter__item--active, [class*='filter__item--active']");
    for (const el of actives) {
      const text = (el.textContent || "").trim();
      if (/沟通中/.test(text)) return true;
    }
    return false;
  }

  function isOnZhilianChatPage() {
    // 端口自老 Playwright `_is_probably_chat_page`：靠正文文本特征判断已进入聊天/沟通中视图。
    const t = (document.body && document.body.innerText) || "";
    return /要附件简历|查看附件简历|未联系|未读|在线沟通|候选人沟通|请从左侧列表中选择/.test(t);
  }

  function clickAtCoord(x, y) {
    // 兜底坐标点击：仅用于左侧聊天菜单（老 Playwright 验证过 (46, 146) 命中率高）。
    const el = document.elementFromPoint(x, y);
    if (!el) return null;
    const target = findDeepestClickable(el);
    try { target.scrollIntoView({ block: "center" }); } catch {}
    try { target.click(); } catch {}
    return target;
  }

  async function waitForZhilianCandidateListStable(timeoutMs = 3500) {
    // 智联 tab 切换：--active class 同步切，但 .im-session-list__virtual--box 列表数据是异步拉取/重渲染。
    // 期间扫描会拿到前一个 tab 残留的虚拟节点（虚拟滚动复用 DOM），导致 50% 概率点错人。
    // 解决：等"两次连续扫描签名列表完全相同"才视为稳定。
    const startedAt = Date.now();
    let prevSignatures = "";
    let stableHits = 0;
    while (Date.now() - startedAt < timeoutMs) {
      const targets = getZhilianCandidateTargets(new Set());
      const sigs = targets.slice(0, 8).map((t) => t.name).join("|");
      if (sigs && sigs === prevSignatures) {
        stableHits++;
        if (stableHits >= 1) {
          emit({ type: "zhilian_nav", data: { step: "candidate_list_stable", count: targets.length, elapsed_ms: Date.now() - startedAt } });
          return true;
        }
      } else {
        stableHits = 0;
        prevSignatures = sigs;
      }
      await sleep(350);
    }
    emit({ type: "zhilian_nav", data: { step: "candidate_list_stable_timeout", elapsed_ms: Date.now() - startedAt } });
    return false;
  }

  async function ensureZhilianOnChattingPage() {
    // 幂等：若已经在「聊天 → 沟通中」（active class + 候选人可见 + 文本特征），跳过整个导航。
    if (
      isZhilianChattingTabActive() &&
      getZhilianCandidateTargets(new Set()).length > 0 &&
      isOnZhilianChatPage()
    ) {
      emit({ type: "zhilian_nav", data: { step: "already_on_chat", action: "skip" } });
      return true;
    }

    // 第 1 步：点击左侧「聊天」菜单。
    const navEntry = findZhilianLeftNavChatEntry();
    if (navEntry) {
      const clickTarget = findDeepestClickable(navEntry);
      try { clickTarget.scrollIntoView({ block: "center" }); } catch {}
      // 用 Direct 而非 Reliably：智联左导是 li/a/span 兄弟拼装的菜单组，closest 上跳会跳到整个 nav 容器，
      // 再用容器中心坐标派发会落到错误菜单项上。Direct 完全锚定 clickTarget 自己。
      clickElementDirect(clickTarget);
      emit({ type: "zhilian_nav", data: { step: "left_chat_menu_clicked", target_tag: clickTarget.tagName } });
      await sleep(1800);
    } else {
      // DOM 扫描失败时启用坐标兜底（仅对左侧聊天菜单，顶部 tab 不允许兜底）。
      emit({ type: "zhilian_nav", data: { step: "left_chat_menu_missing", fallback: "coord_click" } });
      const fallback = clickAtCoord(46, 146);
      emit({
        type: "zhilian_nav",
        data: {
          step: "left_chat_menu_coord_fallback",
          hit_tag: fallback ? fallback.tagName : null,
          hit_text: fallback ? (fallback.textContent || "").trim().slice(0, 16) : null,
        },
      });
      await sleep(1800);
    }

    // 双确认：active tab 必须是「沟通中」才能跳过 tab 点击；
    // 仅候选人可见 + 文本特征不够——智联未联系/已获取微信 tab 下候选人列表 DOM 一样能扫到，class 区分不出来。
    // 等列表稳定再判定，否则虚拟滚动复用的旧节点会让 candidates_visible 假阳。
    await waitForZhilianCandidateListStable(2000);
    const afterLeft = {
      tab_active_is_chatting: isZhilianChattingTabActive(),
      candidates_visible: getZhilianCandidateTargets(new Set()).length > 0,
      chat_page_text: isOnZhilianChatPage(),
    };
    emit({ type: "zhilian_nav", data: { step: "after_left_click_check", ...afterLeft } });
    if (afterLeft.tab_active_is_chatting && afterLeft.candidates_visible) {
      emit({ type: "zhilian_nav", data: { step: "ensure_done_after_left", candidates_visible: true } });
      return true;
    }

    // 第 2 步：点击顶部「沟通中」tab（绝不允许坐标兜底，找不到就放弃这一步）。
    const tab = findZhilianTopChattingTab();
    if (tab) {
      try { tab.scrollIntoView({ block: "center" }); } catch {}
      // 用 Direct：tab 是 filter-tabs 容器内的兄弟节点，clickElementReliably 的 closest("[class*='filter']")
      // 会跳到外层 filter-tabs 容器，容器中心坐标常常落在默认 active 的"已获取微信"上，造成误点。
      clickElementDirect(tab);
      emit({ type: "zhilian_nav", data: { step: "top_chatting_tab_clicked", target_tag: tab.tagName } });
      await sleep(1200);
      // tab 切换是异步的：active class 同步，但虚拟滚动列表数据异步重渲染——等列表稳定后再扫，
      // 否则扫到的可能是前一个 tab 残留的虚拟节点。
      await waitForZhilianCandidateListStable(3500);
    } else {
      emit({ type: "zhilian_nav", data: { step: "top_chatting_tab_missing", fallback: "skip_no_coord" } });
    }

    await sleep(400);
    const finalCheck = {
      tab_active_is_chatting: isZhilianChattingTabActive(),
      candidates_visible: getZhilianCandidateTargets(new Set()).length > 0,
      chat_page_text: isOnZhilianChatPage(),
    };
    const finalReady = finalCheck.tab_active_is_chatting && finalCheck.candidates_visible;
    emit({ type: "zhilian_nav", data: { step: "ensure_done", ...finalCheck, ready: finalReady } });
    return finalReady;
  }

  async function zhilianCollectLoop() {
    // 智联 rd5/rd6 子域本身受平台认证保护，未登录的浏览器无法到达——
    // 移除继承自 BOSS 的 isAuthenticated() 文本标记探测，避免误报"未检测到登录态"。
    emit({ type: "page_ready", data: { url: location.href } });

    // 采集前先点「聊天」菜单 + 「沟通中」tab，确保候选人列表已渲染再开始扫描。
    await ensureZhilianOnChattingPage();
    scrollZhilianCandidateListToTop();
    await sleep(500);
    const dedupKeys = new Set(Array.isArray(config.boss_candidate_keys) ? config.boss_candidate_keys : []);
    const dedupSignatures = new Set(Array.isArray(config.boss_candidate_signatures) ? config.boss_candidate_signatures : []);
    emit({ type: "boss_content_script_collect_started", data: { content_script_version: CONTENT_SCRIPT_VERSION, key_count: dedupKeys.size, signature_count: dedupSignatures.size } });

    const seenSet = new Set();
    let scrollRetries = 0;
    const MAX_SCROLL_RETRIES = 10;
    let previousDetailName = "";

    while (state !== "stopped" && results.completed < config.max_resumes) {
      await waitForPause();
      if (state === "stopped") break;

      let targets = getZhilianCandidateTargets(seenSet);
      // diag: outer_scan
      emit({ type: "zhilian_loop_diag", data: { stage: "outer_scan", targets_count: targets.length, seen_size: seenSet.size, scroll_retries: scrollRetries, completed: results.completed, target_signatures_preview: targets.slice(0, 3).map(t => (t.name || "").slice(0, 30)), seen_size_at: Date.now() } });
      emit({ type: "candidate_list_scanned", data: { count: targets.length, scanned: results.scanned } });

      if (targets.length === 0) {
        scrollRetries++;
        if (scrollRetries > MAX_SCROLL_RETRIES) {
          emit({ type: "collect_finished", data: { reason: "no_more_candidates", completed: results.completed, skipped: results.skipped } });
          state = "idle";
          return;
        }
        // diag: empty_retry
        emit({ type: "zhilian_loop_diag", data: { stage: "empty_retry", scroll_retries: scrollRetries, max_retries: MAX_SCROLL_RETRIES, seen_size: seenSet.size } });
        scrollZhilianCandidateList(600);
        await sleep(1800);
        continue;
      }
      scrollRetries = 0;

      for (const target of targets) {
        if (state === "stopped") break;
        await waitForPause();
        if (state === "stopped") break;
        if (results.completed >= config.max_resumes) break;

        seenSet.add(target.name || `idx_${target.index}`);
        results.scanned++;
        results.currentIndex = target.index;
        const stepStart = Date.now();
        const phaseTimes = { iter_start: Date.now() }; // diag: iter phase timestamps

        try {
          target.element.scrollIntoView({ block: "center" });
          await sleep(100);
          phaseTimes.scroll_done = Date.now(); // diag
          // 智联候选人卡片是 React 组件，handler 挂在 .im-session-item.km-list__item 本身，
          // 直接 .click() 在 production React 下偶尔哑火（无 pointerdown/mouseup 完整事件链）。
          // 用 clickElementDirect 派发完整事件序列 + 锚定卡片自身（不 closest 上跳到 virtual--box 容器）。
          clickElementDirect(target.element);
          await sleep(150);
          phaseTimes.click_done = Date.now(); // diag
        } catch (err) {
          emit({ type: "candidate_skipped", data: { candidate_signature: target.name || `idx_${target.index}`, reason: "click_failed", error: String(err) } });
          results.skipped++;
          emitProgress();
          continue;
        }

        const info = await waitZhilianDetailSwitch(previousDetailName, 4000);
        phaseTimes.wait_done = Date.now(); // diag
        const candidateName = info.name || target.name || "未知";
        const candidateAge = info.age || "未知";
        const candidateEdu = info.education || "未知";
        const candidateSig = `${candidateName}/${candidateAge}/${candidateEdu}`;
        previousDetailName = candidateName;

        emit({ type: "candidate_clicked", data: { name: candidateName, age: candidateAge, education: candidateEdu, job_title: info.job_title || "", talking_position: info.talking_position || "", index: target.index, elapsed_ms: Date.now() - stepStart } });

        if (candidateName === "未知" && candidateAge === "未知" && candidateEdu === "未知") {
          emit({ type: "candidate_skipped", data: { candidate_signature: candidateSig, reason: "candidate_info_unrecognized" } });
          results.skipped++;
          emitProgress();
          continue;
        }

        const normalizedSig = normalizeBossCandidateSignature(candidateSig);
        const dedupKey = await buildBossCandidateKey(candidateSig, info);
        const sigHit = dedupSignatures.has(candidateSig) || dedupSignatures.has(normalizedSig);
        const keyHit = Boolean(dedupKey && dedupKeys.has(dedupKey));
        if (sigHit || keyHit) {
          phaseTimes.branch = "dedup_hit"; // diag
          emit({ type: "candidate_skipped", data: { candidate_signature: candidateSig, reason: "boss_dedup_hit", candidate_key: dedupKey } });
          results.skipped++;
          emitProgress();
          continue;
        }

        await sleep(300);
        const btnState = getZhilianAttachmentButtonState();
        emit({ type: "zhilian_attachment_button_state", data: { candidate_signature: candidateSig, state: btnState } });

        if (btnState === "view") {
          phaseTimes.branch = "download"; // diag
          // 智联流程：先发 download_intent 占座（背带 candidate_info / download_request_id），
          // 再点"查看附件简历"按钮；按钮触发 window.open 弹出 attachment.zhaopin.com 的 PDF 直链 tab，
          // background.js 用 chrome.tabs.onUpdated 监听该 tab，URL 命中后由 onDeterminingFilename
          // 劫持文件名 + 绑定到 pendingDownloads，下载完成通过 download_completed/download_failed
          // 消息回到 waitForDownloadResult。
          const downloadRequestId = makeDownloadRequestId("", candidateSig);

          chrome.runtime.sendMessage({
            target: "background",
            event: {
              type: "download_intent",
              data: {
                candidate_signature: candidateSig,
                candidate_info: info,
                download_request_id: downloadRequestId,
                platform_code: "zhilian",
                run_id: activeRunId,
              },
            },
          });

          const resultPromise = waitForDownloadResult(downloadRequestId, 30000);

          const clickResult = clickZhilianViewAttachmentButton();
          if (!clickResult.clicked) {
            const cardClicked = clickZhilianAttachmentCard();
            if (!cardClicked) {
              emit({ type: "candidate_skipped", data: { candidate_signature: candidateSig, reason: "download_button_not_found" } });
              results.skipped++;
              emitProgress();
              continue;
            }
          }

          const downloadResult = await resultPromise;
          const downloadData = downloadResult && downloadResult.data ? downloadResult.data : {};

          if (downloadResult && downloadResult.ok && downloadData.download_path) {
            const ack = await waitForPersistAck(downloadRequestId, candidateSig, 15000);
            if (!ack || !ack.ok) {
              results.skipped++;
            }
          } else {
            emit({ type: "candidate_skipped", data: { candidate_signature: candidateSig, reason: "download_failed", error: downloadResult && downloadResult.reason ? downloadResult.reason : "" } });
            results.skipped++;
          }
        } else if (btnState === "request") {
          phaseTimes.branch = "no_attachment_request"; // diag
          emit({ type: "candidate_skipped", data: { candidate_signature: candidateSig, reason: "no_resume_attachment" } });
          results.skipped++;
        } else if (btnState === "already_requested") {
          phaseTimes.branch = "no_attachment_other"; // diag
          emit({ type: "candidate_skipped", data: { candidate_signature: candidateSig, reason: "resume_request_already_sent" } });
          results.skipped++;
        } else {
          phaseTimes.branch = "no_attachment_other"; // diag
          emit({ type: "candidate_skipped", data: { candidate_signature: candidateSig, reason: "no_resume_attachment" } });
          results.skipped++;
        }

        emitProgress();
        phaseTimes.iter_end = Date.now(); // diag
        // diag: iter phase summary — 仅在慢迭代或 download 分支回报
        const iterTotalMs = phaseTimes.iter_end - phaseTimes.iter_start;
        if (iterTotalMs > 5000 || phaseTimes.branch === "download") {
          emit({ type: "zhilian_iter_diag", data: { candidate_signature: candidateSig, branch: phaseTimes.branch || "unknown", iter_total_ms: iterTotalMs, scroll_ms: (phaseTimes.scroll_done || 0) - phaseTimes.iter_start, click_ms: (phaseTimes.click_done || 0) - (phaseTimes.scroll_done || 0), wait_ms: (phaseTimes.wait_done || 0) - (phaseTimes.click_done || 0), post_wait_ms: phaseTimes.iter_end - (phaseTimes.wait_done || 0), element_still_in_dom: document.body.contains(target.element) } });
        }
        const interval = config.scan_interval_ms || config.interval_ms || 2000;
        await sleep(interval + Math.random() * 500);
      }

      // diag: inner_loop_finished
      emit({ type: "zhilian_loop_diag", data: { stage: "inner_loop_finished", processed_in_batch: targets.length, seen_size: seenSet.size, completed: results.completed, target_completed: config.max_resumes } });
      scrollZhilianCandidateList(500);
      await sleep(1500);
    }

    // diag: outer_loop_exit
    emit({ type: "zhilian_loop_diag", data: { stage: "outer_loop_exit", state: state, completed: results.completed, target_completed: config.max_resumes, seen_size: seenSet.size, scroll_retries: scrollRetries } });
    const stopped = state === "stopped";
    state = "idle";
    emit({ type: "collect_finished", data: { completed: results.completed, skipped: results.skipped, stopped } });
  }

  async function collectLoop() {
    if (!isActiveInstance()) return;
    if (activeCollectLoopRunId === activeRunId) return;
    activeCollectLoopRunId = activeRunId;
    try {
      if (needsQianchengLearning()) {
        await runQianchengLearningSession();
        return;
      }
      if (PLATFORM && PLATFORM.code === "qiancheng") {
        await qianchengCollectLoop();
        return;
      }
      if (PLATFORM && PLATFORM.code === "zhilian") {
        await zhilianCollectLoop();
        return;
      }
      // BOSS chat 页本身受平台认证保护，未登录会被 302 到登录页（content.js 也就不会被注入），
      // 历史上保留过基于 body.innerText 文本标记的 isAuthenticated() 检查作为兜底，
      // 但 React 异步渲染 + BOSS 时不时调整文案，会在已登录的真页面误报"未检测到登录态"，
      // 让 collect 立刻 fail（参考 2026-05-23 第一轮 BOSS 测试日志）。智联 v2.14.0 已移除同样的检查，
      // BOSS v2.15.0 一致跟进，依靠 manifest 路径匹配 + 平台 302 兜底足够保证只有已登录页才会进入采集。
      emit({ type: "page_ready", data: { url: location.href } });

    await clickBossChatMenu();
    await sleep(900);
    const chattingTabResult = await clickBossChattingTab();
    await sleep(900);
    emit({ type: "boss_diag", data: { step: "chatting_tab", result: chattingTabResult, url: location.href } });

    await resetCandidateListScroll();
    emit({ type: "boss_diag", data: { step: "scroll_reset", url: location.href } });

    let items = getCandidateItems();
    {
      const sample = items.slice(0, 5).map((el, idx) => {
        const r = el.getBoundingClientRect();
        const t = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 50);
        return {
          idx,
          top: Math.round(r.top),
          left: Math.round(r.left),
          w: Math.round(r.width),
          h: Math.round(r.height),
          tag: el.tagName,
          cls: String(el.className || "").slice(0, 60),
          text: t,
        };
      });
      emit({
        type: "boss_diag",
        data: {
          step: "candidate_scan",
          count: items.length,
          sample,
          hit: window.__bossLastCandidateScanDiag || null,
          viewport: { w: window.innerWidth, h: window.innerHeight },
        },
      });
    }
    if (items.length === 0) {
      // Retry: wait for DOM to render after tab click
      for (let retry = 0; retry < 3 && items.length === 0; retry++) {
        await sleep(1500);
        items = getCandidateItems();
        emit({ type: "boss_diag", data: { step: "retry_scan", retry: retry + 1, found: items.length } });
      }
    }
    if (items.length === 0) {
      const diagContainers = LIST_CONTAINER_SELECTORS.map(s => ({
        selector: s,
        count: document.querySelectorAll(s).length,
      })).filter(x => x.count > 0);
      const diagCandidates = CANDIDATE_SELECTORS.map(s => ({
        selector: s,
        count: document.querySelectorAll(s).length,
      })).filter(x => x.count > 0);

      // 收集完整 DOM 结构快照以诊断选择器失效原因
      const domSnapshot = collectDomSnapshot();
      emit({ type: "boss_diag", data: { step: "dom_snapshot_on_fail", snapshot: domSnapshot } });

      emit({ type: "error", data: {
        message: "未找到候选人列表",
        stage: "scan",
        diag: { containers: diagContainers, candidates: diagCandidates, chattingTab: chattingTabResult },
      } });
      state = "idle";
      return;
    }

    const seenSignatures = new Set();
    const processedElements = new WeakSet();
    const processedTexts = new Set();
    let processedCount = 0;
    let scrollRetries = 0;
    const MAX_SCROLL_RETRIES = 10;
    let consecutiveUnrecognized = 0;

    for (let i = results.currentIndex; i < items.length && results.completed < config.max_resumes; i++) {
      if (state === "stopped") break;
      await waitForPause();
      if (state === "stopped") break;

      results.currentIndex = i;
      const item = items[i];
      if (processedElements.has(item)) continue;
      const itemText = (item.innerText || item.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120);
      if (itemText && processedTexts.has(itemText)) continue;
      processedElements.add(item);
      if (itemText) processedTexts.add(itemText);
      processedCount++;

      const candidateStepStartedAt = Date.now();
      try {
        item.scrollIntoView({ block: "center" });
        await sleep(80);
        item.click();
        await sleep(450 + consecutiveUnrecognized * 300);
      } catch (error) {
        emit({ type: "candidate_skipped", data: { candidate_signature: `index_${i}`, reason: "click_failed", error: String(error), elapsed_ms: Date.now() - candidateStepStartedAt } });
        results.skipped++;
        emitProgress();
        continue;
      }

      const infoStartedAt = Date.now();
      let info = await waitForContactInfo(item, 3000);
      let infoElapsedMs = Date.now() - infoStartedAt;
      let signature = `${info.name}/${info.age}/${info.education}`;

      if (signature === "待识别/待识别/待识别") {
        item.click();
        await sleep(600);
        info = await waitForContactInfo(item, 3000);
        infoElapsedMs = Date.now() - infoStartedAt;
        signature = `${info.name}/${info.age}/${info.education}`;
      }

      const candidateId = `${activeRunId || "run"}_${i}_${signature}`;

      const talkingRaw = extractBossTalkingPosition();
      const talkingSimplified = simplifyBossTalkingPosition(talkingRaw);
      if (talkingSimplified) {
        emit({ type: "boss_talking_position", data: { candidate_signature: signature, raw: talkingRaw, simplified: talkingSimplified } });
      } else {
        emit({ type: "boss_talking_position_skip", data: { candidate_signature: signature, reason: "not_found" } });
      }

      emit({ type: "candidate_clicked", data: { ...info, candidate_id: candidateId, candidate_signature: signature, talking_position: talkingSimplified, talking_position_raw: talkingRaw, index: i, elapsed_ms: infoElapsedMs } });

      if (signature === "待识别/待识别/待识别") {
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "candidate_info_unrecognized", raw_text: info.raw_text || "" } });
        results.skipped++;
        consecutiveUnrecognized++;
        emitProgress();
        continue;
      }

      consecutiveUnrecognized = 0;

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

      // ── 白名单过滤：只处理目标候选人 ──
      if (Array.isArray(config.target_candidate_names) && config.target_candidate_names.length > 0) {
        const candidateName = (info.name || signature.split("/")[0] || "").trim();
        const isTarget = config.target_candidate_names.some(t => candidateName.includes(t) || t.includes(candidateName));
        if (!isTarget) {
          await skipCandidate(candidateId, signature, "target_whitelist_skip", { fast_skip: true });
          continue;
        }
      }

      const resumeButtonLookupStartedAt = Date.now();
      emit({ type: "boss_resume_button_lookup_started", data: { candidate_id: candidateId, candidate_signature: signature } });
      let btn = findResumeButton();
      if (!btn) {
        await skipCandidate(candidateId, signature, "no_resume_button", { fast_skip: true, elapsed_ms: Date.now() - resumeButtonLookupStartedAt });
        continue;
      }

      emit({ type: "resume_button_found", data: { candidate_id: candidateId, candidate_signature: signature, button_state: btn.state, button_state_label: btn.state_label, button_text: btn.text, elapsed_ms: Date.now() - resumeButtonLookupStartedAt } });

      if (btn.state === "dim") {
        const consentBtn = acceptResumeConsentIfNeeded();
        if (consentBtn) {
          const consentRect = consentBtn.getBoundingClientRect();
          const consentIsDisabled = isDisabled(consentBtn);
          const origClass = (consentBtn.className || "").toString();
          emit({ type: "resume_consent_found", data: {
            candidate_id: candidateId, candidate_signature: signature,
            consent_tag: consentBtn.tagName,
            consent_text: textOf(consentBtn),
            consent_class: origClass.slice(0, 200),
            consent_rect: { left: Math.round(consentRect.left), top: Math.round(consentRect.top), width: Math.round(consentRect.width), height: Math.round(consentRect.height) },
            consent_visible: isVisible(consentBtn),
            consent_disabled: consentIsDisabled,
            consent_parent_class: (consentBtn.parentElement?.className || "").toString().slice(0, 200),
            consent_pointer_events: getComputedStyle(consentBtn).pointerEvents,
            resume_btn_before: { state: btn.state, text: btn.text, descriptor: btn.el ? getElementDescriptor(btn.el).slice(0, 200) : "", opacity_chain: btn.el ? getOpacityChain(btn.el) : [] },
            click_method: "direct_click_then_cdp_fallback",
          } });
          // 第一步：直接 .click() — 与正常流程（非 disabled 按钮）走的是同一条路径
          // clickElementReliably 内部最终也是调用 node.click()，但它有 isDisabled 守卫会跳过 disabled 按钮
          // 这里绕过守卫，直接点击
          consentBtn.click();
          emit({ type: "resume_consent_clicked", data: { candidate_id: candidateId, candidate_signature: signature, method: "direct_click" } });
          let brightened = false;
          for (let i = 0; i < 20; i++) {
            await sleep(300);
            const refreshed = findResumeButton();
            const consentStill = acceptResumeConsentIfNeeded();
            emit({ type: "resume_consent_poll", data: {
              candidate_id: candidateId, candidate_signature: signature,
              poll_index: i,
              resume_btn_found: !!refreshed,
              resume_btn_state: refreshed ? refreshed.state : null,
              resume_btn_text: refreshed ? refreshed.text : null,
              resume_btn_opacity_chain: (refreshed && refreshed.el) ? getOpacityChain(refreshed.el) : [],
              resume_btn_disabled: (refreshed && refreshed.el) ? isDisabled(refreshed.el) : null,
              resume_btn_class: (refreshed && refreshed.el) ? (refreshed.el.className || "").toString().slice(0, 200) : "",
              consent_btn_still_present: !!consentStill,
              consent_still_text: consentStill ? textOf(consentStill) : null,
            } });
            if (refreshed && refreshed.state === "bright") {
              brightened = true;
              btn = refreshed;
              break;
            }
            // 直接 .click() 在第3次 poll 后仍未生效 → CDP 兜底（生成 isTrusted:true 的真实鼠标事件）
            if (i === 3 && consentIsDisabled) {
              const cdpBtn = acceptResumeConsentIfNeeded() || consentBtn;
              const cdpRect = cdpBtn.getBoundingClientRect();
              const clickX = Math.round(cdpRect.left + cdpRect.width / 2);
              const clickY = Math.round(cdpRect.top + cdpRect.height / 2);
              const bgResult = await new Promise((resolve) => {
                try {
                  chrome.runtime.sendMessage({ type: "click_consent_via_vue", x: clickX, y: clickY }, (resp) => {
                    resolve(resp || { ok: false, error: "no_response" });
                  });
                } catch (e) { resolve({ ok: false, error: String(e) }); }
              });
              emit({ type: "resume_consent_cdp_fallback", data: {
                candidate_id: candidateId, candidate_signature: signature,
                bg_result: bgResult, click_x: clickX, click_y: clickY,
              } });
            }
          }
          if (!brightened) {
            const finalBtn = findResumeButton();
            const finalConsent = acceptResumeConsentIfNeeded();
            await skipCandidate(candidateId, signature, "resume_consent_clicked_but_not_brightened", {
              elapsed_ms: 6000,
              final_resume_btn_found: !!finalBtn,
              final_resume_btn_state: finalBtn ? finalBtn.state : null,
              final_resume_btn_text: finalBtn ? finalBtn.text : null,
              final_resume_btn_opacity: (finalBtn && finalBtn.el) ? getOpacityChain(finalBtn.el) : [],
              final_resume_btn_class: (finalBtn && finalBtn.el) ? (finalBtn.el.className || "").toString().slice(0, 200) : "",
              final_resume_btn_descriptor: (finalBtn && finalBtn.el) ? getElementDescriptor(finalBtn.el).slice(0, 200) : "",
              final_consent_still_present: !!finalConsent,
              final_consent_text: finalConsent ? textOf(finalConsent) : null,
            });
            continue;
          }
          emit({ type: "resume_consent_accepted", data: { candidate_id: candidateId, candidate_signature: signature, button_state: btn.state } });
        } else {
          if (hasResumeRequestSent(getChatDetailRoot())) {
            await skipCandidate(candidateId, signature, "resume_request_already_sent", { fast_skip: true, button_state: btn.state, button_state_label: btn.state_label, button_text: btn.text });
          } else if (config.request_resume_if_missing) {
            await requestResumeAndSkip(btn, candidateId, signature);
          } else {
            await skipCandidate(candidateId, signature, "no_resume_attachment", { fast_skip: true, button_state: btn.state, button_state_label: btn.state_label, button_text: btn.text });
          }
          continue;
        }
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
      try {
        const _candidateResult = await Promise.race([
          (async () => {
            const preview = await startResumePreviewRecognition(candidateId, signature, info, btn, beforeUrl, stalePreview.closed, beforePreviewFingerprint);
            if (shouldAbortAsyncStep()) return "abort";
            if (!preview) {
              await skipCandidate(candidateId, signature, "resume_preview_not_found");
              return "skipped";
            }
            await tryDownloadResume(candidateId, signature, info, preview, true);
            await closeExistingResumePreview(candidateId, signature);
            forceRemoveStalePdfPreviewFrames(signature);
            await sleep(500);
            if (!shouldAbortAsyncStep()) {
              await tryDownloadBossAttachmentWorks(candidateId, signature, info);
            }
            return "done";
          })(),
          new Promise(resolve => setTimeout(() => resolve("timeout"), 60000)),
        ]);
        if (_candidateResult === "abort") break;
        if (_candidateResult === "timeout") {
          emit({ type: "candidate_processing_timeout", data: { candidate_id: candidateId, candidate_signature: signature, timeout_ms: 60000 } });
          await skipCandidate(candidateId, signature, "per_candidate_timeout");
        }
      } catch (perCandidateErr) {
        emit({ type: "candidate_processing_error", data: { candidate_id: candidateId, candidate_signature: signature, message: String(perCandidateErr?.message || perCandidateErr), stack: String(perCandidateErr?.stack || "").slice(0, 800) } });
        await skipCandidate(candidateId, signature, "per_candidate_exception");
      }
    }

    emit({ type: "boss_scroll_phase_enter", data: { completed: results.completed, target: config.max_resumes, processed_count: processedCount, processed_texts_size: processedTexts.size, state } });

    while (results.completed < config.max_resumes && state !== "stopped" && scrollRetries < MAX_SCROLL_RETRIES) {
      const beforeCount = getCandidateItems().length;
      const scrollContainer = findBestListContainer();
      const scrollResult = scrollBossCandidateList(Math.max(400, Math.floor((scrollContainer?.clientHeight || 600) * 0.8)));
      emit({ type: "boss_list_scroll_attempt", data: { mode: scrollResult.mode, ok: scrollResult.ok, before_count: beforeCount, before_scroll_top: scrollResult.before, after_scroll_top: scrollResult.after, retries: scrollRetries } });

      if (!scrollResult.ok && scrollContainer) {
        // Vue API 兜底：用 scrollToBottom 触发虚拟列表加载到底
        const vue = scrollContainer.__vue__;
        if (vue && typeof vue.scrollToBottom === "function") {
          vue.scrollToBottom();
          scrollContainer.dispatchEvent(new Event("scroll", { bubbles: true }));
        }
      }
      // 补发 wheel 事件，部分虚拟列表仅响应 wheel 而非 scrollTop 变化
      if (scrollContainer) {
        scrollContainer.dispatchEvent(new WheelEvent("wheel", { deltaY: 300, bubbles: true }));
      }
      await sleep(1500);
      const newItems = getCandidateItems();
      const isFreshItem = (el) => {
        const t = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120);
        if (t && processedTexts.has(t)) return false;
        return true;
      };
      const freshItems = newItems.filter(isFreshItem);
      if (freshItems.length === 0) {
        // 用 Vue scrollToIndex 尝试跳到更后面的条目
        if (scrollContainer) {
          const vue = scrollContainer.__vue__;
          if (vue && typeof vue.scrollToIndex === "function") {
            const totalProcessed = processedCount;
            vue.scrollToIndex(Math.min(totalProcessed + 5, 99));
            scrollContainer.dispatchEvent(new Event("scroll", { bubbles: true }));
          } else {
            scrollContainer.scrollTop = scrollContainer.scrollHeight;
            scrollContainer.dispatchEvent(new Event("scroll", { bubbles: true }));
          }
        }
        const allItems = getCandidateItems();
        const last = allItems[allItems.length - 1];
        try { last?.scrollIntoView({ block: "end" }); } catch {}
        await sleep(2000);
        const retryItems = getCandidateItems();
        const retryFresh = retryItems.filter(isFreshItem);
        if (retryFresh.length === 0) {
          scrollRetries++;
          if (scrollRetries >= MAX_SCROLL_RETRIES) {
            emit({ type: "boss_list_scroll_exhausted", data: { total_items: newItems.length, retries: scrollRetries, processed_count: processedCount, processed_texts_size: processedTexts.size, completed: results.completed, target: config.max_resumes } });
            break;
          }
          continue;
        }
        items = retryFresh;
      } else {
        items = freshItems;
      }
      scrollRetries = 0;
      for (let i = 0; i < items.length && results.completed < config.max_resumes; i++) {
        if (state === "stopped") break;
        await waitForPause();
        if (state === "stopped") break;

        results.currentIndex++;
        const item = items[i];
        const itemText = (item.innerText || item.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120);
        if (itemText && processedTexts.has(itemText)) continue;
        processedElements.add(item);
        if (itemText) processedTexts.add(itemText);
        processedCount++;

        const candidateStepStartedAt = Date.now();
        try {
          item.scrollIntoView({ block: "center" });
          await sleep(80);
          item.click();
          await sleep(450);
        } catch (error) {
          emit({ type: "candidate_skipped", data: { candidate_signature: `scroll_${results.currentIndex}`, reason: "click_failed", error: String(error), elapsed_ms: Date.now() - candidateStepStartedAt } });
          results.skipped++;
          emitProgress();
          continue;
        }

        const infoStartedAt = Date.now();
        let info = await waitForContactInfo(item, 3000);
        let infoElapsedMs = Date.now() - infoStartedAt;
        let signature = `${info.name}/${info.age}/${info.education}`;

        if (signature === "待识别/待识别/待识别") {
          item.click();
          await sleep(600);
          info = await waitForContactInfo(item, 3000);
          infoElapsedMs = Date.now() - infoStartedAt;
          signature = `${info.name}/${info.age}/${info.education}`;
        }

        const candidateId = `${activeRunId || "run"}_${results.currentIndex}_${signature}`;

        const talkingRaw = extractBossTalkingPosition();
        const talkingSimplified = simplifyBossTalkingPosition(talkingRaw);
        if (talkingSimplified) {
          emit({ type: "boss_talking_position", data: { candidate_signature: signature, raw: talkingRaw, simplified: talkingSimplified } });
        } else {
          emit({ type: "boss_talking_position_skip", data: { candidate_signature: signature, reason: "not_found" } });
        }

        emit({ type: "candidate_clicked", data: { ...info, candidate_id: candidateId, candidate_signature: signature, talking_position: talkingSimplified, talking_position_raw: talkingRaw, index: results.currentIndex, elapsed_ms: infoElapsedMs } });

        if (signature === "待识别/待识别/待识别") {
          emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "candidate_info_unrecognized", raw_text: info.raw_text || "" } });
          results.skipped++;
          emitProgress();
          continue;
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
        const keyHit = Boolean(candidateKey && Array.isArray(config.boss_candidate_keys) && config.boss_candidate_keys.includes(candidateKey));
        const signatureHit = candidateSignatures.has(signature) || candidateSignatures.has(normalizedSignature);
        emit({ type: "boss_pre_dedup_checked", data: { candidate_id: candidateId, candidate_signature: signature, candidate_key: candidateKey, key_hit: keyHit, signature_hit: signatureHit, elapsed_ms: Date.now() - dedupStartedAt } });
        if (keyHit || signatureHit) {
          emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "boss_dedup_hit" } });
          results.skipped++;
          emitProgress();
          continue;
        }

        // ── 白名单过滤：只处理目标候选人 ──
        if (Array.isArray(config.target_candidate_names) && config.target_candidate_names.length > 0) {
          const candidateName = (info.name || signature.split("/")[0] || "").trim();
          const isTarget = config.target_candidate_names.some(t => candidateName.includes(t) || t.includes(candidateName));
          if (!isTarget) {
            await skipCandidate(candidateId, signature, "target_whitelist_skip", { fast_skip: true });
            continue;
          }
        }

        const btnStartedAt = Date.now();
        const btn = findResumeButton();
        const btnElapsedMs = Date.now() - btnStartedAt;
        emit({ type: "resume_button_found", data: { candidate_id: candidateId, candidate_signature: signature, button_state: btn?.state, button_state_label: btn?.state_label, button_text: btn?.text, elapsed_ms: btnElapsedMs } });

        if (!btn || btn.state === "dim") {
          await skipCandidate(candidateId, signature, btn ? "no_downloadable_attachment" : "button_not_found");
          continue;
        }

        if (!guardResumeAttachmentClick(candidateId, signature)) {
          await skipCandidate(candidateId, signature, "resume_attachment_click_guarded", { fast_skip: true });
          continue;
        }
        const stalePreview = await closeExistingResumePreview(candidateId, signature);
        const beforePreviewFingerprint = stalePreview.before_fingerprint || getResumePreviewFingerprint();
        const beforeUrl = location.href;
        const clickOk = clickElementReliably(btn.el);
        emit({ type: "resume_attachment_click_dispatched", data: { candidate_id: candidateId, candidate_signature: signature, click_ok: clickOk, button_state: btn.state } });
        await sleep(350);
        try {
          const _candidateResult = await Promise.race([
            (async () => {
              const preview = await startResumePreviewRecognition(candidateId, signature, info, btn, beforeUrl, stalePreview.closed, beforePreviewFingerprint);
              if (shouldAbortAsyncStep()) return "abort";
              if (!preview) {
                await skipCandidate(candidateId, signature, "resume_preview_not_found");
                return "skipped";
              }
              await tryDownloadResume(candidateId, signature, info, preview, true);
              await closeExistingResumePreview(candidateId, signature);
              forceRemoveStalePdfPreviewFrames(signature);
              if (!shouldAbortAsyncStep()) {
                await tryDownloadBossAttachmentWorks(candidateId, signature, info);
              }
              return "done";
            })(),
            new Promise(resolve => setTimeout(() => resolve("timeout"), 60000)),
          ]);
          if (_candidateResult === "abort") break;
          if (_candidateResult === "timeout") {
            emit({ type: "candidate_processing_timeout", data: { candidate_id: candidateId, candidate_signature: signature, timeout_ms: 60000 } });
            await skipCandidate(candidateId, signature, "per_candidate_timeout");
          }
        } catch (perCandidateErr) {
          emit({ type: "candidate_processing_error", data: { candidate_id: candidateId, candidate_signature: signature, message: String(perCandidateErr?.message || perCandidateErr), stack: String(perCandidateErr?.stack || "").slice(0, 800) } });
          await skipCandidate(candidateId, signature, "per_candidate_exception");
        }
      }
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
    // BOSS / 51 / 智联三平台的 chat 路径本身受平台认证保护，能注入 content.js 即视为已登录已就绪，
    // 不再依赖 body.innerText 的文本标记（容易因平台改文案 / 异步渲染时机被误判，
    // 参考 2026-05-23 第一轮 BOSS 测试日志里的 "未检测到登录态" 误报）。
    const hostnameMatch = PLATFORM.hostnames.includes(location.hostname);
    emit({
      type: hostnameMatch ? "page_ready" : "page_detected",
      data: {
        url: location.href,
        title: document.title,
        authenticated: hostnameMatch,
        detected: hostnameMatch,
        trigger,
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
        // qiancheng 平台首次采集前先走 4 步学习引导
        if (PLATFORM && PLATFORM.code === "qiancheng" && !isQianchengLearningComplete()) {
          state = "collecting";
          emit({ type: "qiancheng_learning_required", data: { run_id: activeRunId, content_script_version: CONTENT_SCRIPT_VERSION } });
          runQianchengLearningSession(activeRunId).catch((err) => {
            emit({ type: "qiancheng_learning_step_failed", data: { step: "session_error", reason: String(err) } });
          }).finally(() => {
            state = "idle";
            emit({ type: "collect_finished", data: { reason: "learning_session_ended", total: 0 } });
          });
          break;
        }
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
        persistAckCreditedRequests.clear();
        persistAckCreditedSignatures.clear();
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
        if (resumePreviewLearnState._cancelCapture) resumePreviewLearnState._cancelCapture();
        if (pauseResolve) { pauseResolve(); pauseResolve = null; }
        break;
      case "skip_current_candidate": {
        const data = msg.data || {};
        const targetCid = data.candidate_id || "";
        const reason = data.reason || "watchdog";
        emit({
          type: "watchdog_skip_received",
          data: { candidate_id: targetCid, reason, run_id: activeRunId, content_script_version: CONTENT_SCRIPT_VERSION },
        });
        // 让所有 pending await 立刻解开，collectLoop 的当前迭代会自然走到下一个候选人
        pendingDownloadWaiters.forEach((resolve) => resolve({ ok: false, reason: "watchdog_skip" }));
        pendingDownloadWaiters.clear();
        pendingPersistAcks.forEach((resolve) => resolve({ ok: false, status: "watchdog_skip" }));
        pendingPersistAcks.clear();
        // 主动 emit candidate_skipped，桥端 WatchdogState 凭此把该 cid 标记为终态
        emit({
          type: "candidate_skipped",
          data: {
            candidate_id: targetCid,
            candidate_signature: "",
            reason: "watchdog_timeout",
            watchdog_reason: reason,
          },
        });
        break;
      }
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
        const received_at_ms = Date.now();  // diag: persist ack timing
        const ack_sent_at_ms = Number(data.ack_sent_at_ms) || 0;
        const ws_chain_ms = ack_sent_at_ms > 0 ? (received_at_ms - ack_sent_at_ms) : -1;
        emit({
          type: "persist_ack_timing",
          data: {
            candidate_signature: data.candidate_signature || "",
            download_request_id: data.download_request_id || "",
            save_duration_ms: Number(data.save_duration_ms) || -1,
            ack_sent_at_ms,
            received_at_ms,
            ws_chain_ms,
            status: data.status || "",
          },
        });
        const key = data.download_request_id || data.candidate_signature || "";
        // 状态白名单：saved=简历归档成功；works_saved=附件作品归档成功（同样视为正向 ack）。
        const ackOk = data.status === "saved" || data.status === "works_saved";
        const ackResult = { ok: ackOk, status: data.status || "unknown", reason: data.reason || "", data };
        const resolver = pendingPersistAcks.get(key);
        if (resolver) {
          pendingPersistAcks.delete(key);
          resolver(ackResult);
        } else {
          // ack 早于 waiter 到达：缓存供后续 waitForPersistAck 入口消费（同时落 sig 副本，因为 finalize 同时挂双键）
          if (data.download_request_id) receivedPersistAcks.set(data.download_request_id, ackResult);
          if (data.candidate_signature) receivedPersistAcks.set(data.candidate_signature, ackResult);
          // 缓存自动过期（45s 内若没人消费就丢弃，防止内存泄漏）
          setTimeout(() => {
            if (data.download_request_id) receivedPersistAcks.delete(data.download_request_id);
            if (data.candidate_signature) receivedPersistAcks.delete(data.candidate_signature);
          }, 45000);
        }
        // 桥侧 saved 是权威信号；只在无 waiter 时兜底回写 completed（有 waiter 时由 finalizeDownloadWithPersistAck 统一 credit）。
        if (!resolver && data.status === "saved" && state === "collecting") {
          creditPersistCompletion(data.download_request_id || "", data.candidate_signature || "", "ack_late");
        }
        break;
      }
    }
    sendResponse({ ok: true });
    return true;
  });

  emitPageStatus("load");
})();
