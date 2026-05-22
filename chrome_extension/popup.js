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
});
