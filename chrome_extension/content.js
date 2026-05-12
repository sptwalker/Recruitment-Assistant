(function () {
  "use strict";

  const CANDIDATE_SELECTORS = [
    ".chat-list li",
    ".chat-list .item",
    ".user-list li",
    ".friend-list li",
    "[class*='chat'] [class*='item']",
    "[class*='user'] [class*='item']",
  ];

  const AUTH_MARKERS = ["沟通中", "新招呼", "联系人", "附件简历", "牛人"];

  let state = "idle";
  let config = { max_resumes: 5, interval_ms: 5000 };
  let results = { downloaded: 0, skipped: 0, currentIndex: 0 };
  let pauseResolve = null;

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  function emit(event) {
    chrome.runtime.sendMessage({ target: "background", event });
  }

  function isAuthenticated() {
    const text = document.body?.innerText || "";
    return AUTH_MARKERS.filter((m) => text.includes(m)).length >= 2;
  }

  function getCandidateItems() {
    for (const selector of CANDIDATE_SELECTORS) {
      const items = document.querySelectorAll(selector);
      if (items.length > 0) return Array.from(items);
    }
    return [];
  }

  function extractContactInfo() {
    const candidates = document.querySelectorAll(
      "[class*='chat'] [class*='header'], [class*='user'] [class*='info'], [class*='card'], header, .name-box, .base-info"
    );
    let text = "";
    for (const el of candidates) {
      const value = (el.innerText || "").replace(/\s+/g, " ").trim();
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

  function findResumeButton() {
    const nodes = document.querySelectorAll("button, a, div, span");
    for (const el of nodes) {
      const text = `${el.innerText || ""} ${el.getAttribute("title") || ""}`;
      if (!text.includes("附件简历")) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      const style = getComputedStyle(el);
      const disabled =
        el.disabled ||
        el.getAttribute("aria-disabled") === "true" ||
        (el.className || "").includes("disabled") ||
        style.pointerEvents === "none" ||
        parseFloat(style.opacity || "1") < 0.55;
      return { el, enabled: !disabled };
    }
    return null;
  }

  function clickDownloadButton() {
    const nodes = document.querySelectorAll("button, a, div, span, i");
    const keywords = ["下载", "download", "Download"];
    for (const el of nodes) {
      const text = `${el.innerText || ""} ${el.getAttribute("title") || ""} ${el.className || ""}`;
      if (!keywords.some((k) => text.includes(k))) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      el.click();
      return true;
    }
    return false;
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
      } catch {
        continue;
      }

      const info = extractContactInfo();
      const signature = `${info.name}/${info.age}/${info.education}`;

      if (seenSignatures.has(signature)) {
        emit({ type: "candidate_skipped", data: { candidate_signature: signature, reason: "duplicate" } });
        results.skipped++;
        continue;
      }
      seenSignatures.add(signature);

      emit({ type: "candidate_clicked", data: { ...info, index: i } });

      const btn = findResumeButton();
      if (!btn) {
        emit({ type: "candidate_skipped", data: { candidate_signature: signature, reason: "no_resume_button" } });
        results.skipped++;
        emitProgress();
        await sleep(config.interval_ms);
        continue;
      }

      if (!btn.enabled) {
        emit({ type: "candidate_skipped", data: { candidate_signature: signature, reason: "button_disabled" } });
        results.skipped++;
        emitProgress();
        await sleep(config.interval_ms);
        continue;
      }

      btn.el.click();
      await sleep(1500);

      const downloaded = clickDownloadButton();
      await sleep(2000);

      if (downloaded) {
        results.downloaded++;
        emit({
          type: "resume_downloaded",
          data: { candidate_signature: signature, candidate_info: info, filename: `${signature}.pdf` },
        });
      } else {
        emit({ type: "candidate_skipped", data: { candidate_signature: signature, reason: "download_failed" } });
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
