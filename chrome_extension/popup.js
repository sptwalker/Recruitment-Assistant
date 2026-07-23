chrome.runtime.sendMessage({ type: "get_state" }, (response) => {
  const stateMap = {
    idle: "空闲",
    collecting: "采集中",
    paused: "已暂停",
  };

  const platformCodes = ["boss", "qiancheng", "zhilian"];
  for (const code of platformCodes) {
    const wsDot = document.getElementById(`${code}-wsDot`);
    const wsStatus = document.getElementById(`${code}-wsStatus`);
    const pageDot = document.getElementById(`${code}-pageDot`);
    const pageStatus = document.getElementById(`${code}-pageStatus`);

    const pState = response?.platform_states?.[code];
    if (pState?.connected) {
      wsDot.className = "dot on";
      wsStatus.textContent = "服务端已连接";
    } else {
      wsDot.className = "dot off";
      wsStatus.textContent = "服务端未连接";
    }

    const collectState = pState?.collectState || "idle";
    pageStatus.textContent = stateMap[collectState] || "空闲";
    pageDot.className = collectState === "collecting" ? "dot on" : "dot off";
  }

  // 回填已保存的服务端配置
  document.getElementById("serverUrl").value = response?.server_ws_url || "";
  document.getElementById("serverToken").value = response?.server_token || "";
});

// 保存服务端配置 → 写 storage → 通知 background 热重连
document.getElementById("saveBtn").addEventListener("click", () => {
  const server_ws_url = document.getElementById("serverUrl").value.trim();
  const server_token = document.getElementById("serverToken").value.trim();
  const msg = document.getElementById("saveMsg");
  chrome.storage.local.set({ server_ws_url, server_token }, () => {
    chrome.runtime.sendMessage({ type: "update_server_config" }, () => {
      msg.textContent = "已保存，正在重连…";
      setTimeout(() => { msg.textContent = ""; }, 2500);
    });
  });
});
