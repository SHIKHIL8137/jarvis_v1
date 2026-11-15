"""Run:  python -m openjarvis.wake [--install | --uninstall | --threshold 0.5]

Listens on the default microphone for "hey jarvis". On detection it starts
Jarvis if it is not running, then opens the UI straight into voice mode.
Audio is processed locally and discarded; nothing leaves the machine.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

LOG = logging.getLogger("jarvis.wake")
UI_PORT = 5173
UI_URL = f"http://localhost:{UI_PORT}/?voice=1"
REPO = Path(__file__).resolve().parents[3]  # shortcut: assumes an editable install
STARTUP_VBS = (
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
    / "jarvis-wake.vbs"
)


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wake_jarvis() -> None:
    """Start Jarvis if needed, then open the UI in voice mode."""
    if not _port_open(UI_PORT):
        LOG.info("Jarvis is off - starting it")
        script = REPO / "scripts" / "start-jarvis.ps1"
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-File", str(script),
             "--no-browser"],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        for _ in range(120):  # up to ~2 min for the server + Vite
            if _port_open(UI_PORT) and _port_open(8000):
                time.sleep(2)
                break
            time.sleep(1)
    webbrowser.open(UI_URL)


def listen(threshold: float, cooldown: float = 15.0) -> None:
    import numpy as np
    import sounddevice as sd
    from openwakeword.model import Model

    model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    last = 0.0
    LOG.info("Listening for 'hey jarvis' (threshold %.2f)", threshold)
    with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=1280) as mic:
        while True:
            frame, _ = mic.read(1280)
            score = model.predict(np.squeeze(frame)).get("hey_jarvis", 0.0)
            if score >= threshold and time.time() - last > cooldown:
                last = time.time()
                LOG.info("Wake word heard (score %.2f)", score)
                try:
                    import winsound

                    winsound.Beep(880, 150)
                except Exception:  # noqa: BLE001 - chime is optional
                    pass
                wake_jarvis()
                model.reset()


def install() -> None:
    py = Path(sys.executable).with_name("pythonw.exe")
    cmd = f'"{py}" -m openjarvis.wake'
    STARTUP_VBS.write_text(
        'CreateObject("WScript.Shell").Run "' + cmd.replace('"', '""') + '", 0, False\r\n'
    )
    print(f"Installed: {STARTUP_VBS}\nIt starts at your next login. Start now with: {cmd}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--install", action="store_true")
    p.add_argument("--uninstall", action="store_true")
    p.add_argument("--threshold", type=float, default=0.5)
    a = p.parse_args()
    if a.install:
        return install()
    if a.uninstall:
        STARTUP_VBS.unlink(missing_ok=True)
        print("Removed from startup.")
        return
    log_file = Path.home() / ".openjarvis" / "wake.log"
    logging.basicConfig(level=logging.INFO, filename=log_file,
                        format="%(asctime)s %(message)s")
    while True:  # keep going if the mic is unplugged or a driver hiccups
        try:
            listen(a.threshold)
        except Exception:  # noqa: BLE001
            LOG.exception("listener crashed; retrying in 10s")
            time.sleep(10)


if __name__ == "__main__":
    main()
