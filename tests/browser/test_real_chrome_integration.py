"""Real integration tests executing against actual Google Chrome binary on Windows with dedicated test profile."""

import os
import shutil
import subprocess
import tempfile
import time
import pytest
import requests

from openjarvis.browser.cdp_bridge import CDPBridge
from openjarvis.browser.windows_detector import WindowsBrowserDetector


def _get_installed_chrome_path():
    detector = WindowsBrowserDetector()
    installed = detector.get_installed_browsers()
    chrome_path = installed.get("chrome")
    if chrome_path and os.path.exists(chrome_path):
        return chrome_path
    return None


@pytest.fixture(scope="module")
def real_chrome_instance():
    """Launch a real Chrome instance with an isolated temporary user-data-dir and remote debugging."""
    chrome_path = _get_installed_chrome_path()
    if not chrome_path:
        pytest.skip("Google Chrome executable not found on Windows host")

    tmp_user_data_dir = tempfile.mkdtemp(prefix="jarvis_chrome_profile_test_")
    test_cdp_port = 9333

    cmd = [
        chrome_path,
        f"--user-data-dir={tmp_user_data_dir}",
        f"--remote-debugging-port={test_cdp_port}",
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "about:blank",
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Wait for CDP endpoint to become ready
    cdp_ready = False
    for _ in range(30):
        try:
            r = requests.get(f"http://127.0.0.1:{test_cdp_port}/json/version", timeout=1.0)
            if r.status_code == 200:
                cdp_ready = True
                break
        except Exception:
            time.sleep(0.2)

    if not cdp_ready:
        proc.terminate()
        shutil.rmtree(tmp_user_data_dir, ignore_errors=True)
        pytest.skip("Chrome did not start CDP endpoint in time")

    bridge = CDPBridge(port=test_cdp_port)

    yield {"proc": proc, "bridge": bridge, "port": test_cdp_port, "tmp_dir": tmp_user_data_dir}

    # Teardown: terminate Chrome and clean up temp profile
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()

    time.sleep(0.5)
    shutil.rmtree(tmp_user_data_dir, ignore_errors=True)


def test_real_chrome_cdp_connection(real_chrome_instance):
    """Verify CDP connection to the real Chrome process."""
    bridge = real_chrome_instance["bridge"]
    assert bridge.is_available() is True


def test_real_chrome_list_tabs(real_chrome_instance):
    """Verify listing live tabs from the real Chrome process."""
    bridge = real_chrome_instance["bridge"]
    tabs = bridge.list_tabs()
    assert isinstance(tabs, list)
    assert len(tabs) >= 1
    assert any("about:blank" in (t.url or "") for t in tabs)


def test_real_chrome_tab_creation_and_closure(real_chrome_instance):
    """Verify creating and closing a real tab in the real Chrome instance."""
    bridge = real_chrome_instance["bridge"]
    new_tab = bridge.create_tab("https://example.com")
    assert new_tab is not None
    assert new_tab.tab_id != ""

    time.sleep(0.5)
    # Check that example.com is in live tabs
    tabs = bridge.list_tabs()
    assert any("example.com" in (t.url or "") for t in tabs)

    # Activate
    ok = bridge.activate_tab(str(new_tab.tab_id))
    assert ok is True

    # Close
    closed = bridge.close_tab(str(new_tab.tab_id))
    assert closed is True
