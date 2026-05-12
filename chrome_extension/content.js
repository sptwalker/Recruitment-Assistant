(function () {
  "use strict";

  const CANDIDATE_SELECTORS = [
    ".chat-list li",
    ".chat-list .item",
    ".user-list li",
    ".friend-list li",
    "[class*='chat-list'] [class*='item']",
    "[class*='friend-list'] [class*='item']",
    "[class*='user-list'] [class*='item']",
  ];

  const AUTH_MARKERS = ["沟通中", "新招呼", "联系人", "附件简历", "牛人"];
  const RESUME_VIEW_TEXT = ["查看附件简历", "查看简历附件", "下载附件简历", "下载简历附件"];
  const RESUME_REQUEST_TEXT = ["要附件简历", "索要附件简历", "获取附件简历"];
  const RESUME_REQUESTED_TEXT = ["已向对方要附件简历", "已索要附件简历", "等待对方上传"];

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

  function scoreCandidateItem(el) {
    if (!isVisible(el)) return -1;
    const text = textOf(el);
    if (text.length < 2 || text.length > 260) return -1;
    if (/附件简历|下载|发送|表情|请输入|系统消息/.test(text)) return -1;
    let score = 0;
    if (/\d{2}\s*岁/.test(text)) score += 3;
    if (/本科|大专|硕士|博士|研究生|专科|高中|中专/.test(text)) score += 3;
    if (/在线|沟通|新招呼|今日|昨天|\d{1,2}:\d{2}/.test(text)) score += 1;
    if (el.matches("li, [class*='item']")) score += 1;
    return score;
  }

  function getCandidateItems() {
    const seen = new Set();
    const items = [];
    for (const selector of CANDIDATE_SELECTORS) {
      for (const el of document.querySelectorAll(selector)) {
        if (seen.has(el)) continue;
        seen.add(el);
        const score = scoreCandidateItem(el);
        if (score >= 2) items.push({ el, score });
      }
      if (items.length > 0) break;
    }
    return items.sort((a, b) => b.score - a.score).map((x) => x.el);
  }

  function extractContactInfo() {
    const candidates = document.querySelectorAll(
      "[class*='chat'] [class*='header'], [class*='user'] [class*='info'], [class*='card'], header, .name-box, .base-info"
    );
    let text = "";
    for (const el of candidates) {
      const value = textOf(el);
      if (value.length >= 2 && value.length <= 300 && /岁|本科|大专|硕士|博士|年|经验|先生|女士/.test(value)) {
        text = value;
        break;
      }
    }
    if (!text) text = (document.body?.innerText || "").slice(0, 300);

    const nameMatch = text.match(/([\u4e00-\u9fa5]{2,4})(?:先生|女士)?/);
    const ageMatch = text.match(/(\d{2})\s*岁/);
    const eduMatch = text.match(/博士|硕士|研究生|本科|大专|专科|高中|中专/);

    const name = nameMatch ? nameMatch[1] : "待识别";
    const age = ageMatch ? ageMatch[1] + "岁" : "待识别";
    let education = eduMatch ? eduMatch[0] : "待识别";
    if (education === "研究生") education = "硕士";
    if (education === "专科") education = "大专";

    return { name, age, education };
  }

  function classifyResumeButtonText(text) {
    if (RESUME_VIEW_TEXT.some((k) => text.includes(k))) return "view";
    if (RESUME_REQUESTED_TEXT.some((k) => text.includes(k))) return "requested";
    if (RESUME_REQUEST_TEXT.some((k) => text.includes(k))) return "request";
    if (text.includes("附件简历")) return "unknown_resume";
    return "none";
  }

  function findResumeButton() {
    const nodes = document.querySelectorAll("button, a, div, span");
    const matches = [];
    for (const el of nodes) {
      const text = textOf(el);
      const stateName = classifyResumeButtonText(text);
      if (stateName === "none") continue;
      if (!isVisible(el)) continue;
      matches.push({ el, text, state: stateName, enabled: !isDisabled(el) });
    }
    const priority = { view: 1, request: 2, requested: 3, unknown_resume: 4 };
    matches.sort((a, b) => priority[a.state] - priority[b.state]);
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
    const nodes = document.querySelectorAll("button, a, div, span, i");
    const keywords = ["下载附件", "下载简历", "下载", "download", "Download"];
    for (const el of nodes) {
      const text = `${textOf(el)} ${el.className || ""}`;
      if (!keywords.some((k) => text.includes(k))) continue;
      if (!isVisible(el) || isDisabled(el)) continue;
      const areaText = textOf(el.closest("[class*='resume'], [class*='attachment'], [class*='dialog'], [class*='modal']") || el);
      if (!/简历|附件|resume|download/i.test(`${text} ${areaText}`)) continue;
      return el;
    }
    return null;
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
    emit({ type: "candidate_list_scanned", data: { count: items.length } });
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

      const info = extractContactInfo();
      const signature = `${info.name}/${info.age}/${info.education}`;
      const candidateId = `${activeRunId || "run"}_${i}_${signature}`;

      if (seenSignatures.has(signature)) {
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: "duplicate" } });
        results.skipped++;
        continue;
      }
      seenSignatures.add(signature);

      emit({ type: "candidate_clicked", data: { ...info, candidate_id: candidateId, candidate_signature: signature, index: i } });

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
        emit({ type: "candidate_skipped", data: { candidate_id: candidateId, candidate_signature: signature, reason: `button_disabled:${btn.state}` } });
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

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.run_id) activeRunId = msg.run_id;
    switch (msg.type) {
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

  if (isAuthenticated()) {
    emit({ type: "page_ready", data: { url: location.href } });
  }
})();
