document.addEventListener("DOMContentLoaded", () => {
  const statusBadge = document.getElementById("status-badge");
  const serverUrlInput = document.getElementById("server-url");
  const authTokenInput = document.getElementById("auth-token");
  const saveBtn = document.getElementById("save-btn");
  const statusMsg = document.getElementById("status-msg");

  function updateUI(status) {
    if (status.connected) {
      statusBadge.textContent = "Connected";
      statusBadge.className = "badge connected";
    } else {
      statusBadge.textContent = "Disconnected";
      statusBadge.className = "badge disconnected";
    }

    if (status.config) {
      if (status.config.serverUrl && !serverUrlInput.value) {
        serverUrlInput.value = status.config.serverUrl;
      }
      if (status.config.token && !authTokenInput.value) {
        authTokenInput.value = status.config.token;
      }
    }
  }

  // Poll status from background script
  chrome.runtime.sendMessage({ type: "get_status" }, (response) => {
    if (response) {
      updateUI(response);
    }
  });

  saveBtn.addEventListener("click", () => {
    const serverUrl = serverUrlInput.value.trim() || "http://127.0.0.1:8000";
    const token = authTokenInput.value.trim();
    
    // Compute wsUrl
    let wsUrl = serverUrl.replace(/^http/, "ws");
    if (!wsUrl.endsWith("/v1/browser/extension/ws")) {
      wsUrl = `${wsUrl.replace(/\/$/, "")}/v1/browser/extension/ws`;
    }

    statusMsg.textContent = "Connecting...";
    chrome.runtime.sendMessage({
      type: "save_config",
      config: { serverUrl, wsUrl, token }
    }, (res) => {
      setTimeout(() => {
        chrome.runtime.sendMessage({ type: "get_status" }, (status) => {
          if (status) {
            updateUI(status);
            statusMsg.textContent = status.connected ? "Connected to Jarvis!" : "Disconnected. Check server.";
          }
        });
      }, 800);
    });
  });
});
