from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import token_urlsafe
from urllib.parse import parse_qs, urlparse

from streamlit.web import cli as stcli


def _get_data_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        local_appdata = os.environ.get("LOCALAPPDATA")
        base_dir = Path(local_appdata) / "NovaView" if local_appdata else Path.home() / ".novaview"
    else:
        base_dir = Path(__file__).resolve().parent
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


DATA_BASE_DIR = _get_data_base_dir()
TEMP_DIR = DATA_BASE_DIR / "temp"
DB_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def _resolve_app_path() -> Path:
    if getattr(sys, "frozen", False):
        base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base_dir = Path(__file__).resolve().parent
    return base_dir / "app.py"


def _get_log_path() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        log_dir = Path(local_appdata) / "NovaView"
    else:
        log_dir = Path.cwd()
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "run_error.log"


def _append_runtime_log(message: str) -> None:
    try:
        log_path = _get_log_path().with_name("run_runtime.log")
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def _cleanup_temp_database_files(reason: str) -> None:
    removed = 0
    if not TEMP_DIR.exists():
        _append_runtime_log(f"Temp cleanup ({reason}): pasta inexistente.")
        return

    for path in TEMP_DIR.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in DB_SUFFIXES:
            continue
        try:
            path.unlink()
            removed += 1
        except Exception as e:
            _append_runtime_log(f"Temp cleanup ({reason}): falha ao remover {path.name}: {e}")

    _append_runtime_log(f"Temp cleanup ({reason}): {removed} arquivo(s) removido(s).")


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_streamlit(url: str, timeout_s: float = 30.0) -> bool:
    health_url = f"{url}/_stcore/health"
    elapsed = 0.0
    step = 0.3
    while elapsed < timeout_s:
        try:
            with urllib.request.urlopen(health_url, timeout=1.5) as response:
                if response.status == 200:
                    _append_runtime_log("Health endpoint ready (200).")
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            pass

        threading.Event().wait(step)
        elapsed += step
    _append_runtime_log("Timeout waiting for health endpoint.")
    return False


def _discover_app_url(base_url: str) -> str:
    host_config_url = f"{base_url}/_stcore/host-config"
    try:
        with urllib.request.urlopen(host_config_url, timeout=2.0) as response:
            payload = response.read().decode("utf-8", errors="replace")
        config = json.loads(payload)
        base_path = config.get("baseUrlPath") or config.get("baseUriPath") or ""
        if isinstance(base_path, str) and base_path.strip("/"):
            normalized = "/" + base_path.strip("/")
            discovered = f"{base_url}{normalized}"
            _append_runtime_log(f"Discovered base path from host-config: {normalized}")
            return discovered
    except Exception as e:
        _append_runtime_log(f"Host-config discovery failed: {e}")

    return base_url


def _show_error_dialog(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "NovaView Datalogger", 0x10)
    except Exception:
        pass


def _run_startup_splash(stop_event: threading.Event) -> None:
    try:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title("NovaView Datalogger")
        root.geometry("420x140")
        root.resizable(False, False)
        root.attributes("-topmost", True)

        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Iniciando sistema...", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Preparando servidor local e carregando modulos.", font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 12))

        bar = ttk.Progressbar(frame, mode="indeterminate", length=380)
        bar.pack(anchor="w")
        bar.start(12)

        while not stop_event.is_set():
            root.update_idletasks()
            root.update()
            time.sleep(0.05)

        bar.stop()
        root.destroy()
    except Exception:
        return


class _HeartbeatState:
    def __init__(self, token: str):
        self.token = token
        self.lock = threading.Lock()
        self.last_ping = 0.0
        self.close_requested = False


def _start_heartbeat_server() -> tuple[_HeartbeatState, ThreadingHTTPServer, str]:
    state = _HeartbeatState(token=token_urlsafe(18))

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def _send_headers(self, status_code: int) -> None:
            self.send_response(status_code)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.end_headers()

        def _update_heartbeat(self, path: str) -> None:
            with state.lock:
                state.last_ping = time.monotonic()
                if path == "/close":
                    state.close_requested = True

        def _handle_ping_or_close(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path not in {"/ping", "/close"}:
                self._send_headers(404)
                return

            token = parse_qs(parsed.query).get("token", [""])[0]
            if token != state.token:
                self._send_headers(403)
                return

            self._update_heartbeat(parsed.path)
            self._send_headers(204)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send_headers(204)

        def do_GET(self) -> None:  # noqa: N802
            self._handle_ping_or_close()

        def do_POST(self) -> None:  # noqa: N802
            self._handle_ping_or_close()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint_base = f"http://127.0.0.1:{server.server_address[1]}"
    return state, server, endpoint_base


def _shutdown_when_heartbeat_stops(
    state: _HeartbeatState,
    startup_timeout_s: float = 90.0,
) -> None:
    started = time.monotonic()
    while time.monotonic() - started < startup_timeout_s:
        with state.lock:
            if state.last_ping > 0:
                break
        time.sleep(0.5)

    with state.lock:
        first_ping = state.last_ping

    if first_ping <= 0:
        _append_runtime_log("No web heartbeat detected; auto-shutdown monitor disabled.")
        return

    _append_runtime_log("Web heartbeat detected. Close monitor started.")

    while True:
        with state.lock:
            last_ping = state.last_ping
            close_requested = state.close_requested

        idle = time.monotonic() - last_ping
        if close_requested and idle >= 2.0:
            _cleanup_temp_database_files("close_signal")
            _append_runtime_log("Close signal received from browser. Exiting launcher process.")
            os._exit(0)

        time.sleep(1.0)


def _show_manual_open_dialog(url: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            "Nao foi possivel abrir o navegador automaticamente.\n\n"
            f"Abra manualmente: {url}",
            "NovaView Datalogger",
            0x40,
        )
    except Exception:
        pass


def _open_browser_with_fallback(url: str) -> bool:
    for _ in range(4):
        try:
            if webbrowser.open(url):
                return True
        except Exception:
            pass
        time.sleep(0.7)

    try:
        os.startfile(url)  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


def main() -> None:
    splash_stop_event = threading.Event()
    heartbeat_server: ThreadingHTTPServer | None = None

    try:
        _cleanup_temp_database_files("startup")
        _append_runtime_log("Launcher started.")
        app_path = _resolve_app_path()
        if not app_path.exists():
            raise FileNotFoundError(f"Arquivo da aplicacao nao encontrado: {app_path}")
        _append_runtime_log(f"App path resolved: {app_path}")

        heartbeat_state, heartbeat_server, heartbeat_base = _start_heartbeat_server()
        os.environ["NOVAIOT_HEARTBEAT_URL"] = heartbeat_base
        os.environ["NOVAIOT_HEARTBEAT_TOKEN"] = heartbeat_state.token
        _append_runtime_log(f"Heartbeat endpoint: {heartbeat_base}")

        port = _pick_free_port()
        url = f"http://127.0.0.1:{port}"
        _append_runtime_log(f"Selected URL: {url}")

        splash_thread = threading.Thread(
            target=_run_startup_splash,
            args=(splash_stop_event,),
            daemon=True,
        )
        splash_thread.start()

        def _open_when_ready() -> None:
            if _wait_for_streamlit(url):
                target_url = _discover_app_url(url)
                _append_runtime_log("Server ready. Opening browser...")
                _append_runtime_log(f"Browser target URL: {target_url}")
                opened = _open_browser_with_fallback(target_url)
                if not opened:
                    _append_runtime_log("Automatic browser open failed.")
                    _show_manual_open_dialog(target_url)
                threading.Thread(
                    target=_shutdown_when_heartbeat_stops,
                    args=(heartbeat_state,),
                    daemon=True,
                ).start()
                splash_stop_event.set()
            else:
                _append_runtime_log("Server did not become ready in time.")
                splash_stop_event.set()

        threading.Thread(target=_open_when_ready, daemon=True).start()

        sys.argv = [
            "streamlit",
            "run",
            str(app_path),
            "--global.developmentMode=false",
            "--server.headless=true",
            f"--server.port={port}",
            "--server.address=127.0.0.1",
            "--server.baseUrlPath=",
            "--browser.serverAddress=127.0.0.1",
            "--browser.gatherUsageStats=false",
            "--server.fileWatcherType=none",
        ]
        _append_runtime_log("Starting Streamlit CLI.")
        raise SystemExit(stcli.main())
    except Exception:
        splash_stop_event.set()
        log_path = _get_log_path()
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
        _show_error_dialog(
            "Falha ao iniciar o NovaView Datalogger.\n\n"
            f"Log: {log_path}"
        )
        raise
    finally:
        try:
            if heartbeat_server is not None:
                heartbeat_server.shutdown()
                heartbeat_server.server_close()
        except Exception:
            pass
        _cleanup_temp_database_files("finalize")


if __name__ == "__main__":
    main()
