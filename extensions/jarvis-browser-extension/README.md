# Jarvis Desktop Browser Companion Extension

Enables Jarvis V1 to have persistent, intelligent control of your existing desktop browser (Google Chrome, Microsoft Edge, Brave) without repeatedly opening duplicate tabs.

## Features

- **Intelligent Tab Reuse**: When you ask Jarvis to open a website (e.g. LinkedIn, YouTube, GitHub), Jarvis detects if that website is already open in an existing tab and activates/focuses that tab instead of opening duplicate tabs.
- **Instant Activation & Navigation**: Focuses the correct browser window and tab immediately on Windows.
- **Privacy & Security First**:
  - Requires **only the `tabs` permission** (needed solely to query tab titles/URLs and activate/navigate tabs).
  - No invasive `<all_urls>` web request interception, page scraping, or content script injection.
  - Authenticates securely with Jarvis using a local pairing secret token over `127.0.0.1`.

---

## Installation Instructions (Takes 30 seconds)

### For Google Chrome & Brave:
1. Open your browser and navigate to `chrome://extensions/` (or `brave://extensions/`).
2. Turn **ON** the **Developer mode** toggle in the top-right corner.
3. Click the **Load unpacked** button in the top-left corner.
4. Select the folder:
   `D:\projects\oprn-jarvis\extensions\jarvis-browser-extension`
5. The extension will install instantly and auto-pair with your running Jarvis server.
6. The extension icon badge will turn green (**ON**) indicating Jarvis is actively connected!

### For Microsoft Edge:
1. Open Edge and navigate to `edge://extensions/`.
2. Turn **ON** the **Developer mode** toggle in the left sidebar.
3. Click **Load unpacked**.
4. Select the folder `D:\projects\oprn-jarvis\extensions\jarvis-browser-extension`.

---

## Configuration & Pairing

The extension automatically discovers the local Jarvis pairing token from `http://127.0.0.1:8000/v1/browser/extension/token`.

If you are running Jarvis on a non-default port:
1. Click the Jarvis Companion icon in your browser toolbar.
2. Enter your Jarvis server URL (e.g., `http://127.0.0.1:8000`).
3. Click **Connect & Save**.
