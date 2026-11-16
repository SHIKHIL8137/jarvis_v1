/**
 * Jarvis Desktop Browser Companion - Service Worker (background.js)
 * Enables Jarvis to detect, reuse, and activate desktop browser tabs without duplicate tab creation.
 */

let socket = null;
let reconnectTimer = null;
let heartbeatTimer = null;
let currentConfig = {
  serverUrl: "http://127.0.0.1:8000",
  wsUrl: "ws://127.0.0.1:8000/v1/browser/extension/ws",
  token: ""
};

/**
 * Load stored configuration from storage, or discover automatically from local Jarvis server.
 */
async function loadConfig() {
  try {
    const stored = await chrome.storage.local.get(["serverUrl", "wsUrl", "token"]);
    if (stored.serverUrl) currentConfig.serverUrl = stored.serverUrl;
    if (stored.wsUrl) currentConfig.wsUrl = stored.wsUrl;
    if (stored.token) currentConfig.token = stored.token;

    // If no token stored, attempt auto-pairing with local Jarvis server
    if (!currentConfig.token) {
      const resp = await fetch(`${currentConfig.serverUrl}/v1/browser/extension/token`);
      if (resp.ok) {
        const data = await resp.json();
        if (data.token) {
          currentConfig.token = data.token;
          await chrome.storage.local.set({ token: data.token });
          console.log("[Jarvis Extension] Auto-discovered pairing token from Jarvis server.");
        }
      }
    }
  } catch (err) {
    console.debug("[Jarvis Extension] Config loading or token fetch notice:", err.message);
  }
}

/**
 * Format and return all open browser tabs across all windows.
 */
async function collectTabs() {
  try {
    const tabs = await chrome.tabs.query({});
    return tabs.map(t => ({
      id: t.id,
      windowId: t.windowId,
      url: t.url || "",
      title: t.title || "",
      active: !!t.active,
      favIconUrl: t.favIconUrl || ""
    }));
  } catch (err) {
    console.warn("[Jarvis Extension] Failed to query tabs:", err);
    return [];
  }
}

/**
 * Send full tabs report to Jarvis over WebSocket.
 */
async function reportTabs() {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  const tabs = await collectTabs();
  try {
    socket.send(JSON.stringify({
      type: "tabs_report",
      tabs: tabs
    }));
  } catch (err) {
    console.warn("[Jarvis Extension] Failed to send tab report:", err);
  }
}

/**
 * Handle commands sent from Jarvis.
 */
async function handleJarvisCommand(msg) {
  const { id: cmdId, action, params } = msg;
  console.log(`[Jarvis Extension] Received command: ${action}`, params);

  try {
    let result = null;

    if (action === "activate_tab") {
      const tabId = parseInt(params.tab_id, 10);
      if (isNaN(tabId)) throw new Error(`Invalid tab_id: ${params.tab_id}`);
      
      const tab = await chrome.tabs.update(tabId, { active: true });
      if (tab.windowId) {
        await chrome.windows.update(tab.windowId, { focused: true });
      }
      result = { tab_id: tabId, window_id: tab.windowId };
    }
    else if (action === "navigate_tab") {
      const tabId = parseInt(params.tab_id, 10);
      if (isNaN(tabId)) throw new Error(`Invalid tab_id: ${params.tab_id}`);

      const tab = await chrome.tabs.update(tabId, { url: params.url, active: true });
      if (tab.windowId) {
        await chrome.windows.update(tab.windowId, { focused: true });
      }
      result = { tab_id: tabId, url: params.url };
    }
    else if (action === "create_tab") {
      const createProps = {
        url: params.url || "about:blank",
        active: params.active !== false
      };
      if (params.window_id && !isNaN(parseInt(params.window_id, 10))) {
        createProps.windowId = parseInt(params.window_id, 10);
      }
      const newTab = await chrome.tabs.create(createProps);
      if (newTab.windowId && createProps.active) {
        await chrome.windows.update(newTab.windowId, { focused: true });
      }
      result = { tab_id: newTab.id, window_id: newTab.windowId, url: newTab.url };
    }
    else if (action === "close_tab") {
      const tabId = parseInt(params.tab_id, 10);
      if (isNaN(tabId)) throw new Error(`Invalid tab_id: ${params.tab_id}`);
      await chrome.tabs.remove(tabId);
      result = { tab_id: tabId, closed: true };
    }
    else if (action === "get_tabs") {
      result = await collectTabs();
    }
    else {
      throw new Error(`Unsupported action: ${action}`);
    }

    // Collect fresh tabs immediately so the backend cache is 100% reconciled in the same turn
    let freshTabs = [];
    try {
      freshTabs = await collectTabs();
    } catch (_) {}

    // Send successful response with fresh tabs
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({
        type: "command_response",
        cmd_id: cmdId,
        success: true,
        result: result,
        tabs: freshTabs
      }));
    }

  } catch (err) {
    console.error(`[Jarvis Extension] Error handling action ${action}:`, err);
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({
        type: "command_response",
        cmd_id: cmdId,
        success: false,
        error: err.message || String(err),
        stale_tab_id: (action === "activate_tab" || action === "navigate_tab" || action === "close_tab") ? params?.tab_id : null
      }));
    }
  }
}

/**
 * Establish WebSocket connection to Jarvis.
 */
async function connectToJarvis() {
  if (socket && (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN)) {
    return;
  }

  await loadConfig();

  const tokenParam = currentConfig.token ? `?token=${encodeURIComponent(currentConfig.token)}` : "";
  const targetWs = `${currentConfig.wsUrl}${tokenParam}`;

  console.log(`[Jarvis Extension] Connecting to Jarvis at ${currentConfig.wsUrl}...`);

  try {
    socket = new WebSocket(targetWs);

    socket.onopen = () => {
      console.log("[Jarvis Extension] Connected to Jarvis Desktop Server.");
      updateExtensionBadge(true);
      reportTabs();

      if (heartbeatTimer) clearInterval(heartbeatTimer);
      heartbeatTimer = setInterval(reportTabs, 15000);
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.action && data.id) {
          handleJarvisCommand(data);
        }
      } catch (err) {
        console.warn("[Jarvis Extension] Error parsing WebSocket message:", err);
      }
    };

    socket.onclose = (event) => {
      console.log(`[Jarvis Extension] Disconnected from Jarvis (code ${event.code}). Retrying in 5s...`);
      updateExtensionBadge(false);
      if (heartbeatTimer) clearInterval(heartbeatTimer);
      scheduleReconnect();
    };

    socket.onerror = (err) => {
      console.debug("[Jarvis Extension] WebSocket error notice:", err);
      // onclose will trigger reconnect
    };

  } catch (err) {
    console.debug("[Jarvis Extension] Connection exception:", err.message);
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connectToJarvis, 5000);
}

function updateExtensionBadge(connected) {
  try {
    if (connected) {
      chrome.action.setBadgeText({ text: "ON" });
      chrome.action.setBadgeBackgroundColor({ color: "#22c55e" }); // green
    } else {
      chrome.action.setBadgeText({ text: "OFF" });
      chrome.action.setBadgeBackgroundColor({ color: "#64748b" }); // gray
    }
  } catch (err) {
    // Badge might not be available in all contexts
  }
}

// Tab lifecycle event listeners for instantaneous synchronization
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === "complete" || changeInfo.url || changeInfo.title) {
    reportTabs();
  }
});

chrome.tabs.onActivated.addListener((activeInfo) => {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({
      type: "tab_activated",
      tab_id: activeInfo.tabId,
      window_id: activeInfo.windowId
    }));
  }
  reportTabs();
});

chrome.tabs.onCreated.addListener(() => {
  reportTabs();
});

chrome.tabs.onRemoved.addListener(() => {
  reportTabs();
});

// Listener for messages from popup UI
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === "get_status") {
    sendResponse({
      connected: socket && socket.readyState === WebSocket.OPEN,
      config: currentConfig
    });
  } else if (request.type === "reconnect") {
    if (socket) {
      try { socket.close(); } catch (_) {}
    }
    connectToJarvis().then(() => {
      sendResponse({ status: "reconnecting" });
    });
    return true; // Keep message channel open for async response
  } else if (request.type === "save_config") {
    chrome.storage.local.set(request.config).then(async () => {
      if (socket) {
        try { socket.close(); } catch (_) {}
      }
      await connectToJarvis();
      sendResponse({ status: "saved" });
    });
    return true;
  }
});

// Start connection on service worker launch
connectToJarvis();
