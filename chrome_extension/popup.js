chrome.runtime.sendMessage({ type: "get_state" }, (response) => {
  const wsDot = document.getElementById("wsDot");
  const wsStatus = document.getElementById("wsStatus");
  const pageDot = document.getElementById("pageDot");
  const pageStatus = document.getElementById("pageStatus");

  if (response?.connected) {
    wsDot.className = "dot on";
    wsStatus.textContent = "服务端已连接";
  } else {
    wsDot.className = "dot off";
    wsStatus.textContent = "服务端未连接";
  }

  const stateMap = {
    idle: "空闲",
    collecting: "采集中",
    paused: "已暂停",
  };
  pageStatus.textContent = stateMap[response?.collectState] || "空闲";
  pageDot.className = response?.collectState === "collecting" ? "dot on" : "dot off";
});
