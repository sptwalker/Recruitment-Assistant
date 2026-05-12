const WS_URL = "ws://localhost:8765";
let ws = null;
let reconnectDelay = 1000;
let collectState = "idle";

function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    reconnectDelay = 1000;
    ws.send(JSON.stringify({ type: "extension_connected", data: { version: "1.0.0" } }));
    updateBadge("on");
  };

  ws.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    handleServerCommand(msg);
  };

  ws.onclose = () => {
    ws = null;
    updateBadge("off");
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 30000);
  };

  ws.onerror = () => { ws?.close(); };
}

function handleServerCommand(msg) {
  const { type, config } = msg;
  switch (type) {
    case "start_collect":
      collectState = "collecting";
      sendToContentScript({ type: "start_collect", config });
      break;
    case "pause_collect":
      collectState = "paused";
      sendToContentScript({ type: "pause_collect" });
      break;
    case "resume_collect":
      collectState = "collecting";
      sendToContentScript({ type: "resume_collect" });
      break;
    case "stop_collect":
      collectState = "idle";
      sendToContentScript({ type: "stop_collect" });
      break;
  }
}

function sendToContentScript(msg) {
  chrome.tabs.query({ url: "https://www.zhipin.com/web/*chat*" }, (tabs) => {
    for (const tab of tabs) {
      chrome.tabs.sendMessage(tab.id, msg);
    }
  });
}

function sendToServer(event) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(event));
  }
}

function updateBadge(state) {
  const text = state === "on" ? "" : "!";
  const color = state === "on" ? "#4A90E2" : "#999";
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.target === "background") {
    sendToServer(msg.event);
    sendResponse({ ok: true });
  } else if (msg.type === "get_state") {
    sendResponse({ connected: ws?.readyState === WebSocket.OPEN, collectState });
  }
  return true;
});

connect();
