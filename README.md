# NovaView Datalogger

Streamlit application for reading NovaIOT datalogger `.db` files, packaged as a
standalone Windows `.exe`.

## Project layout

| Path                          | Purpose                                                        |
| ----------------------------- | ------------------------------------------------------------- |
| `app.py`                      | Streamlit application (UI, parsing, PDF/Excel export).       |
| `run_app.py`                  | Launcher / entry point used by the frozen `.exe`.            |
| `requirements.txt`            | Python dependencies.                                          |
| `novaiot_datalogger.exe.spec` | PyInstaller build spec.                                       |
| `build_windows.bat`           | One-click Windows build script (produces `dist/`).           |
| `clean.ps1`                   | Removes build artifacts, caches and local runtime data.      |
| `img/`                        | `novaview_datalogger_logo.png` (runtime logo) and `logo2026.png` (source for the `.exe` icon). |
| `.streamlit/config.toml`      | Streamlit theme config, bundled into the `.exe`.            |
| `.devcontainer/`              | Dev container definition for Codespaces / VS Code.          |

Everything else (`build/`, `dist/`, `__pycache__/`, `.runtime_home/`, `temp/`,
`*.db`, `img/logo2026.ico`) is generated and is ignored by git.

## Run from source

```powershell
pip install -r requirements.txt
streamlit run app.py
```

## Build the Windows .exe

Requires Python on `PATH`. From the project folder:

```bat
build_windows.bat
```

The script installs dependencies, regenerates `img/logo2026.ico` from
`img/logo2026.png`, clears any previous `build/` and `dist/`, then runs
PyInstaller against `novaiot_datalogger.exe.spec`.

Output: `dist/novaiot_datalogger.exe` (single-file, no console window).

## Clean

```powershell
./clean.ps1
```
