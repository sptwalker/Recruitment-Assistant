(function () {
  "use strict";

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
  let config = { max_resumes: 5, interval_ms: 5000 };
  let results = { downloaded: 0, skipped: 0, currentIndex: 0 };
  let pauseResolve = null;
  let activeRunId = "";

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  function textOf(el) {
    return `${el?.innerText || ""} ${el?.getAttribute?.("title") || ""} ${el?.getAttribute?.("aria-label") || ""}`.replace(/\s+/g, " ").trim();
  }

  function emit(event) {
    const payload = { ...event, data: { ...(event.data || {}), run_id: activeRunId } };
    chrome.runtime.sendMessage({ target: "background", event: payload });
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

  function getCandidateItems() {
    const seen = new Set();
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
        if (score >= 2) items.push({ el, score, top: el.getBoundingClientRect().top });
      }
      if (items.length > 0) break;
    }

    if (items.length === 0) {
      for (const selector of CANDIDATE_SELECTORS) {
        for (const el of document.querySelectorAll(selector)) {
          if (seen.has(el)) continue;
          seen.add(el);
          const score = scoreCandidateItem(el);
          if (score >= 2) items.push({ el, score, top: el.getBoundingClientRect().top });
        }
        if (items.length > 0) break;
      }
    }

    return items.sort((a, b) => a.top - b.top || b.score - a.score).map((x) => x.el);
  }

  function stripActivityText(text) {
    return (text || "")
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
    const beforeAge = ageMatch ? clean.slice(0, ageMatch.index).trim() : clean.slice(0, 40).trim();
    const nameMatches = Array.from(beforeAge.matchAll(/[\u4e00-\u9fa5]{2,4}(?:先生|女士)?/g)).map((m) => m[0]);
    const blacklist = new Set(["沟通", "在线", "附件简历", "交换微信", "常用语", "招聘者", "职位管理", "推荐牛人", "搜索", "刚刚", "刚刚活跃", "今日", "今日活跃", "昨日", "昨日活跃", "活跃"]);
    for (let i = nameMatches.length - 1; i >= 0; i--) {
      const value = nameMatches[i].replace(/先生|女士/g, "");
      if (!blacklist.has(value)) {
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

  function hasResumeRequestSent() {
    return (document.body?.innerText || "").includes("简历请求已发送");
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

  async function confirmRequestIfNeeded() {
    await sleep(500);
    const nodes = document.querySelectorAll("button, a, div, span");
    for (const el of nodes) {
      const text = textOf(el);
      if (!/确定|确认|发送|索要/.test(text)) continue;
      if (!isVisible(el) || isDisabled(el)) continue;
      el.click();
      return true;
    }
    return false;
  }

  function findDownloadButton() {
    const nodes = document.querySelectorAll("button, a, [role='button'], div, span, i, svg");
    const keywords = ["下载附件", "下载简历", "下载", "download", "Download"];
    for (const el of nodes) {
      const text = `${textOf(el)} ${el.className || ""}`;
      if (!keywords.some((k) => text.includes(k))) continue;
      if (!isVisible(el) || isDisabled(el)) continue;
      const areaText = textOf(el.closest("[class*='resume'], [class*='attachment'], [class*='dialog'], [class*='modal']") || el);
      if (!/简历|附件|resume|download/i.test(`${text} ${areaText}`)) continue;
      return el.closest("button, a, [role='button']") || el;
    }

    const topButtons = Array.from(document.querySelectorAll("button, a, [role='button'], i, svg"))
      .filter((el) => isVisible(el) && !isDisabled(el))
      .map((el) => {
        const rect = el.getBoundingClientRect();
        const text = `${textOf(el)} ${el.className || ""}`;
        return { el: el.closest("button, a, [role='button']") || el, rect, text };
      })
      .filter(({ rect, text }) => rect.top >= 0 && rect.top <= 95 && rect.left > window.innerWidth * 0.6 && !/关闭|close|取消|×|✕/i.test(text));

    topButtons.sort((a, b) => b.rect.left - a.rect.left);
    return topButtons[0]?.el || null;
  }

  async function waitForDownloadButton(timeoutMs = 6000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const btn = findDownloadButton();
      if (btn) return btn;
      await sleep(300);
    }
    return null;
  }

  async function waitForPause() {
    if (state !== "paused") return;
    await new Promise((resolve) => { pauseResolve = resolve; });
  }

  async function collectLoop() {
    if (!isAuthenticated()) {
      emit({ type: "error", data: { message: "未检测到登录态", stage: "pre_check" } });
      state = "idle";
      return;
    }

    emit({ type: "page_ready", data: { url: location.href } });

    const items = getCandidateItems();
    emit({ type: "candidate_list_scanned", data: { count: items.length, samples: items.slice(0, 5).map((el) => textOf(el).slice(0, 80)) } });
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
        await sleep(300);
        item.click();
        await sleep(1200);
      } catch (error) {
        emit({ type: "candidate_skipped", data: { candidate_signature: `index_${i}`, reason: "click_failed", error: String(error) } });
        results.skipped++;
        continue;
      }

      const info = extractContactInfo(item);
      const signature = `${info.name}/${info.age}/${info.education}`;
      const candidateId = `${activeRunId || "run"}_${i}_${signature}`;

      if (seenSignatures.has(signature)) {
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "duplicate" } });
        results.skipped++;
        continue;
      }
      seenSignatures.add(signature);

      emit({ type: "candidate_clicked", data: { ...info, candidate_id: candidateId, candidate_signature: signature, index: i } });

      if (signature === "待识别/待识别/待识别") {
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "candidate_info_unrecognized", raw_text: info.raw_text || "" } });
        results.skipped++;
        emitProgress();
        await sleep(config.interval_ms);
        continue;
      }

      const btn = findResumeButton();
      if (!btn) {
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "no_resume_button" } });
        results.skipped++;
        emitProgress();
        await sleep(config.interval_ms);
        continue;
      }

      emit({ type: "resume_button_found", data: { candidate_id: candidateId, candidate_signature: signature, button_state: btn.state, button_text: btn.text } });

      if (!btn.enabled) {
        if (hasResumeRequestSent()) {
          emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "resume_request_already_sent" } });
        } else {
          emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: `button_disabled:${btn.state}` } });
        }
        results.skipped++;
        emitProgress();
        await sleep(config.interval_ms);
        continue;
      }

      if (btn.state === "requested") {
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "resume_already_requested" } });
        results.skipped++;
        emitProgress();
        await sleep(config.interval_ms);
        continue;
      }

      btn.el.click();
      if (btn.state === "request") {
        const confirmed = await confirmRequestIfNeeded();
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: confirmed ? "resume_requested" : "resume_request_clicked" } });
        results.skipped++;
        emitProgress();
        await sleep(config.interval_ms);
        continue;
      }

      const downloadButton = await waitForDownloadButton();
      if (downloadButton) {
        emit({ type: "download_intent", data: { candidate_id: candidateId, candidate_signature: signature, candidate_info: info, expected_filename: `${signature}.pdf` } });
        downloadButton.click();
        results.downloaded++;
      } else {
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "download_button_not_found" } });
        results.skipped++;
      }

      emitProgress();
      await sleep(config.interval_ms);
    }

    state = "idle";
    emit({ type: "collect_finished", data: { total_downloaded: results.downloaded, total_skipped: results.skipped } });
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
    if (msg.run_id) activeRunId = msg.run_id;
    switch (msg.type) {
      case "probe_page":
        emitPageStatus("probe");
        break;
      case "start_collect":
        if (state === "collecting") break;
        state = "collecting";
        config = { ...config, ...msg.config };
        results = { downloaded: 0, skipped: 0, currentIndex: 0 };
        collectLoop();
        break;
      case "pause_collect":
        state = "paused";
        break;
      case "resume_collect":
        state = "collecting";
        if (pauseResolve) { pauseResolve(); pauseResolve = null; }
        break;
      case "stop_collect":
        state = "stopped";
        if (pauseResolve) { pauseResolve(); pauseResolve = null; }
        break;
    }
    sendResponse({ ok: true });
    return true;
  });

  emitPageStatus("load");
})();
