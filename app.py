import base64
import os
import sqlite3
import sys
from io import BytesIO
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _get_app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return Path(__file__).resolve().parent


def _get_data_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        local_appdata = os.environ.get("LOCALAPPDATA")
        base_dir = Path(local_appdata) / "NovaIoT" if local_appdata else Path.home() / ".novaiot"
    else:
        base_dir = Path(__file__).resolve().parent
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


APP_BASE_DIR = _get_app_base_dir()
DATA_BASE_DIR = _get_data_base_dir()
TEMP_DIR = DATA_BASE_DIR / "temp"

TIMEZONE = "America/Sao_Paulo"
LOGO_PATH = APP_BASE_DIR / "img" / "logo2026.png"
CHART_PAGE_SIZE = 250
TEMP_HIGH_ALARM_LIMIT = 8
TEMP_LOW_ALARM_LIMIT = 1
RAW_TIME_COLUMNS = ["CollectTime", "StartTime", "EndTime", "Timestamp", "Time"]
DISPLAY_TIME_COLUMNS = {
    "CollectTime_dt": "Data e hora",
    "StartTime_dt": "Início",
    "EndTime_dt": "Fim",
    "Timestamp_dt": "Data e hora",
    "Time_dt": "Data e hora",
}


def render_launcher_heartbeat() -> None:
    heartbeat_url = os.environ.get("NOVAIOT_HEARTBEAT_URL", "").strip()
    heartbeat_token = os.environ.get("NOVAIOT_HEARTBEAT_TOKEN", "").strip()
    if not heartbeat_url or not heartbeat_token:
        return

    components.html(
        f"""
        <script>
        (() => {{
            const base = {heartbeat_url!r};
            const token = {heartbeat_token!r};
            const mk = (path) => `${{base}}${{path}}?token=${{encodeURIComponent(token)}}&t=${{Date.now()}}`;

            const ping = () => {{
                const img = new Image();
                img.src = mk('/ping');
            }};

            const closeSignal = () => {{
                try {{
                    navigator.sendBeacon(mk('/close'));
                }} catch (e) {{
                    const img = new Image();
                    img.src = mk('/close');
                }}
            }};

            ping();
            const id = setInterval(ping, 3000);
            window.addEventListener('beforeunload', closeSignal);
            window.addEventListener('pagehide', closeSignal);
            window.addEventListener('visibilitychange', () => {{
                if (document.visibilityState === 'visible') {{
                    ping();
                }}
            }});

            window.addEventListener('unload', () => clearInterval(id));
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def is_running_in_streamlit_runtime() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False


def get_latest_temp_database() -> Path | None:
    if not TEMP_DIR.exists():
        return None

    db_files = [
        path for path in TEMP_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
    ]
    if not db_files:
        return None
    return max(db_files, key=lambda path: path.stat().st_mtime)


def save_uploaded_database(uploaded_file) -> tuple[Path, str]:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = TEMP_DIR / uploaded_file.name
    temp_path.write_bytes(uploaded_file.getbuffer())
    return temp_path, uploaded_file.name


@st.cache_data(show_spinner=False)
def list_tables(db_path: str) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    return [row[0] for row in rows if not row[0].startswith("sqlite_")]


@st.cache_data(show_spinner=False)
def read_table(db_path: str, table_name: str) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        return pd.read_sql_query(f'SELECT * FROM "{table_name}"', conn)


def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    # Detecta e converte colunas de tempo comuns
    for col in RAW_TIME_COLUMNS:
        if col in out.columns:
            try:
                out[col + "_dt"] = (
                    pd.to_datetime(out[col], unit="ms", utc=True)
                    .dt.tz_convert(TIMEZONE)
                    .dt.tz_localize(None)
                )
            except Exception:
                try:
                    out[col + "_dt"] = pd.to_datetime(out[col], errors="coerce")
                except Exception:
                    pass

    return out


def format_metric(value) -> str:
    if pd.isna(value):
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return str(value)


def format_datetime(value) -> str:
    if pd.isna(value):
        return "-"
    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y %H:%M:%S")
    return str(value)


def get_main_time_column(df: pd.DataFrame) -> str | None:
    for col in ["CollectTime_dt", "StartTime_dt", "EndTime_dt", "Timestamp_dt", "Time_dt"]:
        if col in df.columns and df[col].notna().any():
            return col
    return None


@st.cache_data(show_spinner=False)
def get_logo_data_uri(logo_path: str) -> str:
    path = Path(logo_path)
    if not path.exists():
        return ""

    mime_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    encoded_logo = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded_logo}"


def get_preferred_plot_columns(df: pd.DataFrame) -> list[str]:
    preferred_cols = ["Tprincipal", "Setpoint"]
    numeric_cols = [
        col for col in df.select_dtypes(include="number").columns
        if not col.endswith("_dt") and col not in RAW_TIME_COLUMNS and col != "indexId"
    ]
    ordered = [col for col in preferred_cols if col in numeric_cols]
    ordered.extend(col for col in numeric_cols if col not in ordered)
    return ordered


def prepare_display_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    display_df = df.copy()

    for raw_col in RAW_TIME_COLUMNS:
        converted_col = f"{raw_col}_dt"
        if raw_col in display_df.columns and converted_col in display_df.columns:
            display_df = display_df.drop(columns=[raw_col])

    return display_df.rename(columns=DISPLAY_TIME_COLUMNS)


def filter_by_period(
    df: pd.DataFrame,
    start_date,
    start_time,
    end_date,
    end_time,
) -> pd.DataFrame:
    time_col = get_main_time_column(df)
    if not time_col:
        return df

    if start_date is None or start_time is None or end_date is None or end_time is None:
        return df

    start_dt = pd.Timestamp.combine(start_date, start_time)
    end_dt = pd.Timestamp.combine(end_date, end_time)
    if start_dt > end_dt:
        st.warning("A data/hora inicial precisa ser menor que a data/hora final.")
        return df.iloc[0:0]

    return df[(df[time_col] >= start_dt) & (df[time_col] <= end_dt)]


def render_period_filter(df: pd.DataFrame) -> pd.DataFrame:
    time_col = get_main_time_column(df)
    if not time_col:
        return df

    min_datetime = df[time_col].min()
    max_datetime = df[time_col].max()
    period_key = f"{time_col}_{len(df)}_{min_datetime.value}_{max_datetime.value}"
    applied_period_key = f"applied_period_{period_key}"

    if applied_period_key not in st.session_state:
        st.session_state[applied_period_key] = (
            min_datetime.to_pydatetime(),
            max_datetime.to_pydatetime(),
        )

    with st.container(key="filter-strip"):
        st.markdown("<div class='filter-strip-title'>Período</div>", unsafe_allow_html=True)
        with st.form(key=f"period_form_{period_key}", clear_on_submit=False):
            applied_start_dt, applied_end_dt = st.session_state[applied_period_key]
            c1, c2, c3, c4, c5 = st.columns([2.05, 1, 2.05, 1, 1.2], gap="small")
            start_date = c1.date_input(
                "Data inicial",
                value=applied_start_dt.date(),
                min_value=min_datetime.date(),
                max_value=max_datetime.date(),
                key=f"start_date_{period_key}",
            )
            start_time = c2.time_input(
                "Hora inicial",
                value=applied_start_dt.time().replace(microsecond=0),
                key=f"start_time_{period_key}",
            )
            end_date = c3.date_input(
                "Data final",
                value=applied_end_dt.date(),
                min_value=min_datetime.date(),
                max_value=max_datetime.date(),
                key=f"end_date_{period_key}",
            )
            end_time = c4.time_input(
                "Hora final",
                value=applied_end_dt.time().replace(microsecond=0),
                key=f"end_time_{period_key}",
            )
            confirmed = c5.form_submit_button("Confirmar período", use_container_width=True)

        if confirmed:
            candidate_start = pd.Timestamp.combine(start_date, start_time)
            candidate_end = pd.Timestamp.combine(end_date, end_time)
            if candidate_start > candidate_end:
                st.warning("A data/hora inicial precisa ser menor que a data/hora final.")
            else:
                st.session_state[applied_period_key] = (
                    candidate_start.to_pydatetime(),
                    candidate_end.to_pydatetime(),
                )

    applied_start_dt, applied_end_dt = st.session_state[applied_period_key]
    return filter_by_period(
        df,
        applied_start_dt.date(),
        applied_start_dt.time().replace(microsecond=0),
        applied_end_dt.date(),
        applied_end_dt.time().replace(microsecond=0),
    )


def apply_custom_style() -> None:
    css = """
        <style>
            /* ---------- Design tokens (slate + teal, auto from system) ---------- */
            .stApp {
                --bg: #f8fafc;
                --bg-grad-1: rgba(13, 148, 136, 0.10);
                --bg-grad-2: rgba(15, 23, 42, 0.06);
                --surface: #ffffff;
                --surface-2: #f1f5f9;
                --surface-elev: #ffffff;
                --border: #e2e8f0;
                --border-strong: #cbd5e1;
                --text: #0f172a;
                --text-muted: #475569;
                --primary: #0b1e3a;
                --primary-hover: #102a52;
                --accent: #0d9488;
                --accent-hover: #0f766e;
                --on-accent: #ffffff;
                --ring: rgba(13, 148, 136, 0.45);
                --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06);
                --shadow-md: 0 8px 24px rgba(15, 23, 42, 0.08);
                --shadow-lg: 0 16px 40px rgba(15, 23, 42, 0.10);
                --radius-sm: 8px;
                --radius: 12px;
                --radius-lg: 16px;
                --radius-pill: 999px;
                --on-primary: #ffffff;
                --control-h: 2.5rem;
                --page-max: 1400px;
                --page-pad: clamp(1rem, 3vw, 2.25rem);
                --header-h: 6.25rem;
                --sidebar-bg: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
                --sidebar-text: #f1f5f9;
                --uploader-bg: #ffffff;
                --uploader-text: #0f172a;
                --alarm-high: #dc2626;
                --alarm-low: #2563eb;
                --alarm-door: #d97706;
            }
            .stApp[data-theme="dark"] {
                    --bg: #07101e;
                    --bg-grad-1: rgba(45, 212, 191, 0.14);
                    --bg-grad-2: rgba(148, 163, 184, 0.06);
                    --surface: #121c2e;
                    --surface-2: #1a2540;
                    --surface-elev: #15203a;
                    --border: #2a3a55;
                    --border-strong: #475569;
                    --text: #f1f5f9;
                    --text-muted: #cbd5e1;
                    --primary: #f1f5f9;
                    --primary-hover: #e2e8f0;
                    --accent: #2dd4bf;
                    --accent-hover: #5eead4;
                    --on-accent: #0f172a;
                    --ring: rgba(45, 212, 191, 0.65);
                    --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.55), inset 0 1px 0 rgba(255, 255, 255, 0.04);
                    --shadow-md: 0 12px 28px rgba(0, 0, 0, 0.55), inset 0 1px 0 rgba(255, 255, 255, 0.05);
                    --shadow-lg: 0 18px 44px rgba(0, 0, 0, 0.65), inset 0 1px 0 rgba(255, 255, 255, 0.06);
                    --on-primary: #0f172a;
                    --sidebar-bg: linear-gradient(180deg, #020617 0%, #0f172a 100%);
                    --sidebar-text: #f1f5f9;
                    --uploader-bg: #1a2540;
                    --uploader-text: #f1f5f9;
                    --alarm-high: #f87171;
                    --alarm-low: #60a5fa;
                    --alarm-door: #fbbf24;
            }
            /* ---------- Base layout ---------- */
            .stApp {
                background:
                    radial-gradient(circle at 0% 0%, var(--bg-grad-1), transparent 40%),
                    radial-gradient(circle at 100% 100%, var(--bg-grad-2), transparent 40%),
                    var(--bg);
                color: var(--text);
                font-size: 28px;
            }
            /* ---------- Page width: centered, capped, with edge gutter + space for fixed header ---------- */
            .main .block-container,
            [data-testid="stMainBlockContainer"] {
                max-width: var(--page-max) !important;
                margin-left: auto !important;
                margin-right: auto !important;
                padding-left: var(--page-pad) !important;
                padding-right: var(--page-pad) !important;
                padding-top: calc(var(--header-h) + 1rem) !important;
            }
            /* ---------- Hide chart "view as table" toolbar button (keep fullscreen) ---------- */
            [data-testid="stElementToolbarButton"][aria-label*="table" i],
            [data-testid="stElementToolbarButton"][aria-label*="tabela" i],
            [data-testid="stElementToolbarButton"][aria-label*="data" i]:not([aria-label*="fullscreen" i]):not([aria-label*="tela" i]),
            [data-testid="stElementToolbarButton"][aria-label*="dados" i],
            button[data-testid="stBaseButton-elementToolbar"][aria-label*="table" i],
            button[data-testid="stBaseButton-elementToolbar"][aria-label*="tabela" i],
            button[data-testid="stBaseButton-elementToolbar"][aria-label*="show data" i],
            button[data-testid="stBaseButton-elementToolbar"][aria-label*="mostrar dados" i],
            button[title*="View as table" i],
            button[title*="Show data" i],
            button[title*="mostrar dados" i],
            button[title*="tabela" i] {
                display: none !important;
            }
            .main .block-container {
                padding-top: .75rem;
                padding-bottom: 2rem;
            }
            section[data-testid="stSidebar"],
            div[data-testid="stSidebarCollapsedControl"] {
                display: none !important;
            }
            .stApp h1,
            .stApp h2,
            .stApp h3,
            .stApp h4,
            .stApp h5,
            .stApp h6,
            .stApp p,
            .stApp label,
            .stApp span,
            .stApp div {
                color: var(--text);
            }
            .stApp h1 {
                font-size: 3.35rem !important;
            }
            .stApp h2 {
                font-size: 2.75rem !important;
            }
            .stApp h3 {
                font-size: 2.35rem !important;
            }
            .stApp h4 {
                font-size: 2rem !important;
            }
            .stApp h5,
            .stApp h6 {
                font-size: 1.75rem !important;
            }
            .stApp p,
            .stApp label,
            .stApp span,
            .stApp div {
                font-size: 1.38rem !important;
            }
            [data-testid="stWidgetLabel"] p,
            [data-testid="stWidgetLabel"] label,
            [data-testid="stFileUploaderDropzone"] p,
            [data-testid="stFileUploaderDropzone"] small,
            [data-testid="stMarkdownContainer"] p,
            [data-testid="stMarkdownContainer"] li,
            [data-testid="stCaptionContainer"] {
                font-size: 1.7rem !important;
            }
            .main [data-testid="stWidgetLabel"] p,
            .main [data-testid="stWidgetLabel"] label,
            .main [data-testid="stMarkdownContainer"] p,
            .main [data-testid="stCaptionContainer"],
            .main label,
            .main caption {
                font-size: 1.8rem !important;
                line-height: 1.25 !important;
            }
            .stButton button,
            .stDownloadButton button {
                font-size: 1.35rem !important;
                min-height: var(--control-h) !important;
                height: var(--control-h);
                padding-top: 0 !important;
                padding-bottom: 0 !important;
                box-sizing: border-box !important;
            }
            .stTextInput input,
            .stDateInput input,
            .stTimeInput input,
            .stTimeInput div[data-baseweb="select"] > div,
            .stTimeInput div[data-baseweb="input"],
            .stSelectbox div[data-baseweb="select"] > div,
            .stMultiSelect div[data-baseweb="select"] > div,
            .stNumberInput input {
                font-size: 1.3rem !important;
                min-height: var(--control-h) !important;
                height: var(--control-h) !important;
                box-sizing: border-box !important;
                border-radius: var(--radius-sm) !important;
                border: 1px solid var(--border) !important;
                background: var(--surface) !important;
                color: var(--text) !important;
                transition: border-color .15s ease, box-shadow .15s ease;
                align-items: center !important;
            }
            .stTimeInput div[data-baseweb="select"],
            .stTimeInput div[data-baseweb="input"] {
                background: var(--surface) !important;
                border-radius: var(--radius-sm) !important;
            }
            .stTimeInput div[data-baseweb="select"] input,
            .stTimeInput div[data-baseweb="input"] input {
                background: transparent !important;
                border: 0 !important;
                min-height: 0 !important;
                height: auto !important;
                color: var(--text) !important;
                padding-left: .9rem !important;
                font-variant-numeric: tabular-nums;
            }
            .stTimeInput svg {
                fill: var(--text-muted) !important;
                color: var(--text-muted) !important;
            }
            .stTextInput input:focus-visible,
            .stDateInput input:focus-visible,
            .stTimeInput input:focus-visible,
            .stNumberInput input:focus-visible,
            .stTimeInput div[data-baseweb="select"]:focus-within > div,
            .stTimeInput div[data-baseweb="input"]:focus-within,
            .stSelectbox div[data-baseweb="select"]:focus-within > div,
            .stMultiSelect div[data-baseweb="select"]:focus-within > div {
                border-color: var(--accent) !important;
                box-shadow: 0 0 0 3px var(--ring) !important;
                outline: none !important;
            }
            .stButton button:focus-visible,
            .stDownloadButton button:focus-visible {
                outline: 3px solid var(--ring) !important;
                outline-offset: 2px !important;
            }
            div[data-baseweb="select"] span,
            div[data-baseweb="popover"] *,
            div[data-baseweb="menu"] *,
            div[role="option"] {
                font-size: 1.45rem !important;
            }
            section[data-testid="stSidebar"] {
                background: var(--sidebar-bg);
                border-right: 1px solid rgba(255, 255, 255, 0.12);
            }
            section[data-testid="stSidebar"] * {
                color: var(--sidebar-text);
                font-size: 1.45rem !important;
            }
            section[data-testid="stSidebar"] div[data-baseweb="select"] > div,
            section[data-testid="stSidebar"] div[data-baseweb="base-input"] > div {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: var(--radius-sm);
            }
            section[data-testid="stSidebar"] div[data-baseweb="select"] span,
            section[data-testid="stSidebar"] div[data-baseweb="base-input"] input {
                color: var(--sidebar-text) !important;
            }
            section[data-testid="stSidebar"] button[kind="secondary"] {
                background: rgba(255, 255, 255, 0.12);
                border: 1px solid rgba(255, 255, 255, 0.24);
                border-radius: var(--radius-sm);
                min-height: 62px;
            }
            section[data-testid="stSidebar"] button[kind="secondary"] p {
                color: var(--sidebar-text) !important;
                font-size: 1.45rem !important;
                line-height: 1;
            }
            section[data-testid="stSidebar"] hr {
                border-color: rgba(255, 255, 255, 0.18);
                margin: 1rem 0;
            }
            section[data-testid="stSidebar"] [data-testid="stFileUploader"] {
                background: var(--uploader-bg);
                border: 1px solid var(--border);
                border-radius: var(--radius);
                padding: 8px;
            }
            section[data-testid="stSidebar"] [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzone"] {
                background: var(--uploader-bg);
                border: 1px dashed var(--border-strong);
            }
            section[data-testid="stSidebar"] [data-testid="stFileUploader"] small,
            section[data-testid="stSidebar"] [data-testid="stFileUploader"] span,
            section[data-testid="stSidebar"] [data-testid="stFileUploader"] p,
            section[data-testid="stSidebar"] [data-testid="stFileUploader"] label {
                color: var(--uploader-text) !important;
            }
            section[data-testid="stSidebar"] [data-testid="stFileUploader"] button {
                background: var(--primary);
                color: var(--on-primary) !important;
                border: 1px solid var(--primary);
                border-radius: var(--radius-sm);
            }
            section[data-testid="stSidebar"] [data-testid="stFileUploader"] button:hover {
                background: var(--primary-hover);
            }
            section[data-testid="stSidebar"] [data-testid="stFileUploader"] input[type="file"] {
                color: var(--uploader-text) !important;
            }
            section[data-testid="stSidebar"] [data-testid="stFileUploaderFileName"],
            section[data-testid="stSidebar"] [data-testid="stFileUploader"] div[role="status"],
            section[data-testid="stSidebar"] [data-testid="stFileUploader"] div,
            section[data-testid="stSidebar"] [data-testid="stFileUploader"] .uploadedFileName {
                color: var(--uploader-text) !important;
            }
            section[data-testid="stSidebar"] .stAlert {
                background: rgba(255, 255, 255, 0.1);
                border: 1px solid rgba(255, 255, 255, 0.2);
                color: var(--sidebar-text);
            }
            div[data-testid="stMetric"] {
                position: relative;
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: var(--radius);
                padding: 18px 20px 18px 24px;
                box-shadow: var(--shadow-sm);
                transition: border-color .18s ease, box-shadow .18s ease, transform .18s ease;
                overflow: hidden;
            }
            div[data-testid="stMetric"]::before {
                content: "";
                position: absolute;
                left: 0;
                top: 14%;
                bottom: 14%;
                width: 4px;
                background: var(--border-strong);
                border-radius: 0 var(--radius-pill) var(--radius-pill) 0;
                opacity: .85;
            }
            .st-key-kpi-accent div[data-testid="stMetric"]::before { background: var(--accent); }
            div[data-testid="stMetric"]:hover {
                border-color: var(--accent);
                box-shadow: var(--shadow-md);
                transform: translateY(-1px);
            }
            div[data-testid="stMetric"] label,
            div[data-testid="stMetric"] div {
                color: var(--text) !important;
            }
            div[data-testid="stMetricLabel"] p {
                color: var(--text-muted) !important;
                font-size: 1.55rem !important;
                font-weight: 600 !important;
                text-transform: uppercase;
                letter-spacing: 0.04em;
            }
            div[data-testid="stMetricValue"] {
                font-size: 2.8rem !important;
                font-weight: 700 !important;
                line-height: 1.15 !important;
                letter-spacing: -0.01em;
            }
            div[data-testid="stMetricDelta"] * {
                font-size: 1.3rem !important;
            }
            div[data-testid="stDataFrame"] {
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: var(--radius);
                overflow: hidden;
            }
            div[data-testid="stDataFrame"] *,
            div[data-testid="stTable"] * {
                font-size: 1.35rem !important;
            }
            .app-hero {
                background: transparent;
                border: 0;
                border-radius: 0;
                padding: .35rem 0 .25rem 0;
                margin-bottom: 0;
                box-shadow: none;
            }
            /* ---------- Fixed top header (full viewport width) ---------- */
            .st-key-sticky_header_menu {
                position: fixed !important;
                top: 0 !important;
                left: 0 !important;
                right: 0 !important;
                width: 100% !important;
                max-width: none !important;
                margin: 0 !important;
                padding: 0 !important;
                z-index: 1200 !important;
                background: var(--surface) !important;
                border: 0 !important;
                border-bottom: 1px solid var(--border) !important;
                border-radius: 0 !important;
                box-shadow: var(--shadow-sm) !important;
                overflow: visible !important;
            }
            /* The inner wrapper (our own container key) — this is what gets centered + capped */
            .st-key-header-inner {
                max-width: var(--page-max);
                margin: 0 auto !important;
                padding: .5rem var(--page-pad) !important;
            }
            .st-key-header-inner [data-testid="stVerticalBlock"] {
                gap: .35rem !important;
            }
            .st-key-header-inner [data-testid="stHorizontalBlock"] {
                align-items: center;
            }
            /* Push the tab row a bit down from the hero */
            .st-key-header-inner div[data-testid="stSegmentedControl"],
            .st-key-header-inner div[data-testid="stButtonGroup"] {
                margin-top: 8px !important;
            }
            .st-key-sticky_header_menu [data-testid="stVerticalBlock"] {
                gap: 0 !important;
            }
            .st-key-sticky_header_menu [data-testid="stVerticalBlock"] > div {
                margin-top: 0 !important;
                margin-bottom: 0 !important;
                padding-top: 0 !important;
                padding-bottom: 0 !important;
            }
            .app-header {
                display: flex;
                align-items: center;
                gap: 1.25rem;
            }
            .app-logo {
                height: 2.4rem;
                width: auto;
                flex: 0 0 auto;
            }
            .app-header-text {
                min-width: 0;
            }
            div[data-testid="stButtonGroup"] {
                background: transparent !important;
                margin-top: 0 !important;
                margin-bottom: 0 !important;
                padding: 0 !important;
            }
            .st-key-sticky_header_menu div[data-testid="stSegmentedControl"] {
                background: transparent !important;
                margin-top: 0 !important;
                margin-bottom: 0 !important;
                padding: 0 1rem !important;
                border: 0 !important;
                outline: 0 !important;
                box-shadow: none !important;
            }
            .st-key-sticky_header_menu div[data-testid="stSegmentedControl"] [role="radiogroup"] {
                margin-top: 0 !important;
                margin-bottom: 0 !important;
                border: 0 !important;
                outline: 0 !important;
                box-shadow: none !important;
                padding-top: 0 !important;
                padding-bottom: 0 !important;
            }
            .st-key-sticky_header_menu div[data-testid="stSegmentedControl"] * {
                border-top: initial !important;
            }
            .app-hero {
                border-bottom: 0 !important;
            }
            .st-key-sticky_header_menu [role="radiogroup"] {
                border-top: 0 !important;
                box-shadow: none !important;
            }
            .st-key-sticky_header_menu [role="radiogroup"]::before,
            .st-key-sticky_header_menu [role="radiogroup"]::after {
                display: none !important;
            }
            div[data-testid="stButtonGroup"] > div,
            div[data-testid="stButtonGroup"] [data-baseweb="button-group"] {
                gap: 0 !important;
                width: 100% !important;
            }
            div[data-testid="stButtonGroup"] [data-baseweb="button-group"] {
                display: flex !important;
                flex-wrap: wrap !important;
            }
            div[data-testid="stButtonGroup"] button {
                background: transparent !important;
                border: 0 !important;
                border-bottom: 3px solid transparent !important;
                border-radius: 0 !important;
                box-shadow: none !important;
                min-height: 38px !important;
                height: 38px !important;
                padding: 6px 14px !important;
                transition: background .15s ease, border-color .15s ease, color .15s ease;
            }
            div[data-testid="stButtonGroup"] button:hover {
                background: color-mix(in srgb, var(--primary) 6%, transparent) !important;
                border-bottom-color: var(--border-strong) !important;
                box-shadow: none !important;
                transform: none !important;
            }
            div[data-testid="stButtonGroup"] button:focus-visible {
                outline: 3px solid var(--ring) !important;
                outline-offset: -3px !important;
            }
            div[data-testid="stButtonGroup"] button[aria-checked="true"],
            div[data-testid="stButtonGroup"] button[data-selected="true"] {
                background: var(--primary) !important;
                border-bottom-color: transparent !important;
                box-shadow: none !important;
            }
            div[data-testid="stButtonGroup"] button p,
            div[data-testid="stButtonGroup"] button span {
                color: var(--text-muted) !important;
                font-size: 1.05rem !important;
                font-weight: 700 !important;
                letter-spacing: 0.01em;
                line-height: 1.15 !important;
                white-space: nowrap !important;
            }
            div[data-testid="stButtonGroup"] button:hover p,
            div[data-testid="stButtonGroup"] button:hover span {
                color: var(--text) !important;
            }
            div[data-testid="stButtonGroup"] button[aria-checked="true"] p,
            div[data-testid="stButtonGroup"] button[aria-checked="true"] span,
            div[data-testid="stButtonGroup"] button[data-selected="true"] p,
            div[data-testid="stButtonGroup"] button[data-selected="true"] span {
                color: var(--on-primary) !important;
            }
            .app-hero h1,
            .app-hero h1 *,
            .app-hero h1 span,
            .app-hero h1 div {
                margin: 0;
                font-size: 1.5rem !important;
                font-weight: 800 !important;
                letter-spacing: -0.02em;
                line-height: 1.1 !important;
                overflow-wrap: anywhere;
            }
            .welcome-card {
                position: relative;
                background: var(--surface);
                background-image:
                    radial-gradient(circle at 100% 0%, color-mix(in srgb, var(--accent) 14%, transparent), transparent 45%),
                    radial-gradient(circle at 0% 100%, color-mix(in srgb, var(--primary) 8%, transparent), transparent 50%);
                border: 1px solid var(--border);
                border-left: 6px solid var(--primary);
                border-radius: var(--radius);
                box-shadow: var(--shadow-lg);
                margin: 1.5rem 0;
                max-width: none;
                padding: 2.25rem 2.25rem 1.75rem 2.25rem;
                width: 100%;
            }
            .welcome-card h1,
            .welcome-card h1 * {
                font-size: 3.6rem !important;
                font-weight: 800 !important;
                letter-spacing: -0.02em;
                line-height: 1.05 !important;
                margin: 0 0 .5rem 0;
            }
            .welcome-card p {
                color: var(--text-muted) !important;
                font-size: 1.55rem !important;
                margin: 0 0 1.25rem 0;
                max-width: 60ch;
            }
            .welcome-logo {
                display: block;
                height: auto;
                margin-bottom: 1rem;
                max-width: 280px;
                width: 36%;
            }
            .welcome-features {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                gap: .85rem;
                margin: 1.5rem 0 .25rem 0;
                padding: 0;
                list-style: none;
            }
            .welcome-features li {
                display: flex;
                align-items: flex-start;
                gap: .6rem;
                background: var(--surface-2);
                border: 1px solid var(--border);
                border-radius: var(--radius-sm);
                padding: .75rem .9rem;
                color: var(--text) !important;
                font-size: 1.3rem !important;
                font-weight: 600;
                line-height: 1.25;
            }
            .welcome-features li::before {
                content: "✓";
                color: var(--accent) !important;
                font-weight: 800;
                font-size: 1.4rem;
                line-height: 1;
                flex: 0 0 auto;
                padding-top: .05rem;
            }
            .welcome-upload-hint {
                color: var(--accent) !important;
                font-size: 1.25rem !important;
                margin-top: 1rem;
                margin-bottom: .25rem;
                font-weight: 700;
                letter-spacing: 0.02em;
                text-transform: uppercase;
            }
            /* Main-area uploader (welcome page) */
            .main [data-testid="stFileUploader"] {
                background: var(--surface) !important;
                border: 1px solid var(--border) !important;
                border-radius: var(--radius) !important;
                padding: .5rem !important;
                box-shadow: var(--shadow-sm);
            }
            .main [data-testid="stFileUploader"] label,
            .main [data-testid="stFileUploader"] label * {
                color: var(--text) !important;
            }
            .main [data-testid="stFileUploader"] section,
            .main [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzone"] {
                background: var(--surface-2) !important;
                border: 1.5px dashed var(--border-strong) !important;
                border-radius: var(--radius-sm) !important;
                padding: 1.75rem 1.25rem !important;
                transition: border-color .15s ease, background .15s ease;
                color: var(--text) !important;
            }
            .main [data-testid="stFileUploader"] section *,
            .main [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzone"] *,
            .main [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzoneInstructions"],
            .main [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzoneInstructions"] * {
                color: var(--text) !important;
            }
            .main [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzoneInstructions"] small,
            .main [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzoneInstructions"] span:not(:first-child) {
                color: var(--text-muted) !important;
            }
            .main [data-testid="stFileUploader"] section:hover,
            .main [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzone"]:hover {
                border-color: var(--accent) !important;
                background: color-mix(in srgb, var(--accent) 7%, var(--surface-2)) !important;
            }
            .main [data-testid="stFileUploader"] button {
                background: var(--accent) !important;
                color: var(--on-accent) !important;
                border: 1px solid var(--accent) !important;
                border-radius: var(--radius-sm) !important;
                font-weight: 700 !important;
                min-height: 3rem !important;
                padding: .55rem 1.25rem !important;
            }
            .main [data-testid="stFileUploader"] button * {
                color: var(--on-accent) !important;
            }
            .main [data-testid="stFileUploader"] button:hover {
                background: var(--accent-hover) !important;
                border-color: var(--accent-hover) !important;
            }
            .main [data-testid="stFileUploader"] svg {
                fill: var(--text-muted) !important;
                color: var(--text-muted) !important;
            }
            @media (max-width: 640px) {
                .st-key-sticky_header_menu,
                div[data-testid="stVerticalBlock"] > div:has(.app-header) {
                    top: 0 !important;
                }
                .app-header {
                    align-items: center;
                    gap: .75rem;
                }
                .app-logo {
                    height: 2.4rem;
                }
                .app-hero {
                    padding: .65rem;
                }
                .app-hero h1,
                .app-hero h1 *,
                .app-hero h1 span,
                .app-hero h1 div {
                    font-size: 2.6rem !important;
                }
                .app-hero p {
                    display: none;
                }
                div[data-testid="stButtonGroup"] button {
                    min-height: 48px !important;
                    padding: 10px 16px !important;
                }
                div[data-testid="stButtonGroup"] button p,
                div[data-testid="stButtonGroup"] button span {
                    font-size: 1.2rem !important;
                }
            }
            .section-card {
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: var(--radius);
                padding: 1rem;
                margin: .75rem 0 1rem 0;
            }
            .section-card strong,
            .section-card span,
            .section-card p {
                color: var(--text) !important;
                font-size: 1.65rem !important;
            }
            .panel-section-title {
                color: var(--text) !important;
                font-size: 2.4rem !important;
                font-weight: 700 !important;
                letter-spacing: -0.01em;
                line-height: 1.2 !important;
                margin: 1.75rem 0 .75rem 0 !important;
                display: flex;
                align-items: center;
                gap: .75rem;
            }
            .panel-section-title::before {
                content: "";
                display: inline-block;
                width: 6px;
                height: 1.5rem;
                background: var(--primary);
                border-radius: var(--radius-pill);
            }
            div[data-baseweb="tab-list"] {
                gap: 14px;
                margin-top: .75rem;
                margin-bottom: .75rem;
            }
            button[data-baseweb="tab"] {
                border-radius: var(--radius-pill);
                padding: 14px 24px;
                border: 1px solid var(--border);
                background: var(--surface);
                min-height: 56px;
            }
            button[data-baseweb="tab"] p {
                color: var(--text) !important;
                font-size: 1.45rem !important;
                font-weight: 700 !important;
                line-height: 1.15 !important;
            }
            div[data-baseweb="popover"],
            div[data-baseweb="menu"] {
                background: var(--surface) !important;
                color: var(--text) !important;
            }
            div[data-baseweb="popover"] *,
            div[data-baseweb="menu"] * {
                color: var(--text) !important;
            }
            /* ---------- Alarms ---------- */
            .st-key-kpi-high div[data-testid="stMetric"]::before { background: var(--alarm-high); }
            .st-key-kpi-low  div[data-testid="stMetric"]::before { background: var(--alarm-low); }
            .st-key-kpi-door div[data-testid="stMetric"]::before { background: var(--alarm-door); }
            .latest-alarm-card {
                display: flex;
                align-items: center;
                gap: 1.25rem;
                background: var(--surface);
                border: 1px solid var(--border);
                border-left: 8px solid var(--text-muted);
                border-radius: var(--radius);
                box-shadow: var(--shadow-md);
                padding: 1.4rem 1.6rem;
                margin: 1rem 0 1.5rem 0;
            }
            .latest-alarm-card.high   { border-left-color: var(--alarm-high); }
            .latest-alarm-card.low    { border-left-color: var(--alarm-low); }
            .latest-alarm-card.door   { border-left-color: var(--alarm-door); }
            .latest-alarm-icon {
                font-size: 3.6rem;
                line-height: 1;
                flex: 0 0 auto;
                color: var(--text-muted);
            }
            .latest-alarm-card.high   .latest-alarm-icon { color: var(--alarm-high); }
            .latest-alarm-card.low    .latest-alarm-icon { color: var(--alarm-low); }
            .latest-alarm-card.door   .latest-alarm-icon { color: var(--alarm-door); }
            .latest-alarm-label {
                color: var(--text-muted) !important;
                font-size: 1.1rem !important;
                font-weight: 600 !important;
                text-transform: uppercase;
                letter-spacing: 0.05em;
            }
            .latest-alarm-title {
                color: var(--text) !important;
                font-size: 2.1rem !important;
                font-weight: 700 !important;
                line-height: 1.15;
                margin-top: .15rem;
            }
            .latest-alarm-meta {
                color: var(--text-muted) !important;
                font-size: 1.3rem !important;
                margin-top: .2rem;
            }
            .alarm-badge {
                display: inline-flex;
                align-items: center;
                gap: .4rem;
                background: var(--surface-2);
                color: var(--text) !important;
                font-size: 1.15rem !important;
                font-weight: 700;
                padding: .25rem .8rem;
                border-radius: var(--radius-pill);
                border: 1px solid var(--border);
                white-space: nowrap;
            }
            .alarm-badge .glyph { font-size: 1.15rem; line-height: 1; }
            .alarm-badge.high { background: color-mix(in srgb, var(--alarm-high) 16%, var(--surface)); border-color: var(--alarm-high); }
            .alarm-badge.high .glyph,
            .alarm-badge.high * { color: var(--alarm-high) !important; }
            .alarm-badge.low  { background: color-mix(in srgb, var(--alarm-low) 16%, var(--surface));  border-color: var(--alarm-low);  }
            .alarm-badge.low  .glyph,
            .alarm-badge.low  * { color: var(--alarm-low) !important; }
            .alarm-badge.door { background: color-mix(in srgb, var(--alarm-door) 18%, var(--surface)); border-color: var(--alarm-door); }
            .alarm-badge.door .glyph,
            .alarm-badge.door * { color: var(--alarm-door) !important; }
            .alarm-history {
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: var(--radius);
                overflow: hidden;
                box-shadow: var(--shadow-sm);
                max-height: 540px;
                overflow-y: auto;
            }
            .alarm-history-head,
            .alarm-row {
                display: grid;
                grid-template-columns: 1.4fr 2.2fr 1fr 1fr;
                gap: 1rem;
                align-items: center;
                padding: .75rem 1.25rem;
            }
            .alarm-history-head {
                background: var(--surface-2);
                border-bottom: 1px solid var(--border-strong);
                position: sticky;
                top: 0;
                z-index: 2;
            }
            .alarm-history-head span {
                color: var(--text-muted) !important;
                font-size: 1.1rem !important;
                font-weight: 700 !important;
                text-transform: uppercase;
                letter-spacing: 0.04em;
            }
            .alarm-row {
                border-bottom: 1px solid var(--border);
            }
            .alarm-row:nth-child(even) { background: var(--surface-2); }
            .alarm-row:last-child { border-bottom: 0; }
            .alarm-row span,
            .alarm-row time {
                color: var(--text) !important;
                font-size: 1.25rem !important;
                font-variant-numeric: tabular-nums;
            }
            .alarm-when { color: var(--text-muted) !important; }
            .empty-state {
                background: var(--surface);
                border: 1px dashed var(--border-strong);
                border-radius: var(--radius);
                padding: 2rem 1.5rem;
                text-align: center;
                margin: 1rem 0;
            }
            .empty-state .glyph {
                font-size: 2.8rem;
                color: var(--accent);
                line-height: 1;
            }
            .empty-state h3 {
                color: var(--text) !important;
                font-size: 1.9rem !important;
                font-weight: 700 !important;
                margin: .65rem 0 .25rem 0 !important;
            }
            .empty-state p {
                color: var(--text-muted) !important;
                font-size: 1.3rem !important;
                margin: 0;
            }
            .last-events-row {
                display: flex;
                gap: .75rem;
                flex-wrap: wrap;
                margin-top: 1rem;
                margin-bottom: .25rem;
            }
            /* ---------- Header tabs: shrink + left-align, force horizontal row ---------- */
            .st-key-sticky_header_menu div[data-testid="stButtonGroup"] > div,
            .st-key-sticky_header_menu div[data-testid="stButtonGroup"] [data-baseweb="button-group"] {
                display: flex !important;
                flex-direction: row !important;
                flex-wrap: nowrap !important;
                width: auto !important;
                justify-content: flex-start !important;
                gap: 0 !important;
            }
            .st-key-sticky_header_menu div[data-testid="stButtonGroup"] button {
                flex: 0 0 auto !important;
                width: auto !important;
            }
            /* ---------- Header popover (kept for any future use) ---------- */
            [data-testid="stPopover"] > div > button,
            div[data-testid="stPopover"] button:first-of-type {
                background: var(--primary) !important;
                color: var(--on-primary) !important;
                border: 1px solid var(--primary) !important;
                border-radius: var(--radius-sm) !important;
                font-weight: 700 !important;
                box-shadow: var(--shadow-sm);
            }
            [data-testid="stPopover"] > div > button *,
            [data-testid="stPopover"] > div > button span,
            [data-testid="stPopover"] > div > button p {
                color: var(--on-primary) !important;
            }
            [data-testid="stPopover"] > div > button:hover {
                background: var(--primary-hover) !important;
                border-color: var(--primary-hover) !important;
                box-shadow: var(--shadow-md);
            }
            /* Popover body (portaled) */
            div[data-baseweb="popover"] [role="dialog"],
            div[data-baseweb="popover"] div[data-testid="stPopoverBody"] {
                background: var(--surface) !important;
                border: 1px solid var(--border) !important;
                border-radius: var(--radius) !important;
                box-shadow: var(--shadow-lg);
                padding: 1.25rem !important;
                min-width: 360px;
            }
            div[data-baseweb="popover"] [data-testid="stFileUploader"],
            [role="dialog"] [data-testid="stFileUploader"] {
                background: var(--surface) !important;
                border: 0 !important;
                padding: 0 !important;
                box-shadow: none;
            }
            div[data-baseweb="popover"] [data-testid="stFileUploaderDropzone"],
            [role="dialog"] [data-testid="stFileUploaderDropzone"] {
                background: var(--surface-2) !important;
                padding: 1rem !important;
            }
            /* ---------- Filter strip: compact, subordinate ---------- */
            .st-key-filter-strip {
                background: var(--surface-2);
                border: 1px solid var(--border);
                border-radius: var(--radius);
                padding: .85rem 1.1rem 1rem 1.1rem;
                margin: .5rem 0 1.5rem 0;
                width: 100%;
                max-width: none;
                box-sizing: border-box;
            }
            .filter-strip-title {
                color: var(--text-muted) !important;
                font-size: 1rem !important;
                font-weight: 700 !important;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                margin: 0 0 .55rem 0 !important;
            }
            .st-key-filter-strip [data-testid="stWidgetLabel"] p,
            .st-key-filter-strip [data-testid="stWidgetLabel"] label,
            .st-key-filter-strip label {
                font-size: 1.05rem !important;
                color: var(--text-muted) !important;
                font-weight: 600 !important;
                margin-bottom: .2rem !important;
            }
            .st-key-filter-strip .stDateInput input,
            .st-key-filter-strip .stTimeInput input,
            .st-key-filter-strip .stTimeInput div[data-baseweb="select"] > div,
            .st-key-filter-strip .stTimeInput div[data-baseweb="input"],
            .st-key-filter-strip .stTextInput input,
            .st-key-filter-strip .stNumberInput input {
                min-height: 2.6rem !important;
                height: 2.6rem !important;
                font-size: 1.2rem !important;
                background: var(--surface) !important;
            }
            /* ---------- Layout helper: align label-less control with a labeled one ---------- */
            .button-row-spacer {
                height: 2.95rem;
                line-height: 0;
            }
            /* ---------- Expander: strip default chrome ---------- */
            [data-testid="stExpander"],
            [data-testid="stExpander"] details,
            [data-testid="stExpander"] summary,
            details[data-testid="stExpander"],
            details[data-testid="stExpander"] > summary {
                background: transparent !important;
                border: 0 !important;
                box-shadow: none !important;
                border-radius: 0 !important;
            }
            [data-testid="stExpander"] summary {
                padding: .25rem 0 !important;
            }
            [data-testid="stExpander"] summary:hover {
                background: transparent !important;
            }
            [data-testid="stExpander"] summary p {
                color: var(--text-muted) !important;
                font-size: 1.4rem !important;
                font-weight: 600 !important;
            }
            /* ---------- Multiselect chips ---------- */
            .stMultiSelect [data-baseweb="tag"] {
                background: var(--surface-2) !important;
                border: 1px solid var(--border-strong) !important;
                color: var(--text) !important;
                border-radius: var(--radius-pill) !important;
            }
            .stMultiSelect [data-baseweb="tag"] *,
            .stMultiSelect [data-baseweb="tag"] span,
            .stMultiSelect [data-baseweb="tag"] svg {
                color: var(--text) !important;
                fill: var(--text) !important;
            }
            /* ---------- Buttons: primary variant ---------- */
            .stDownloadButton button[kind="primary"],
            .stButton button[kind="primary"] {
                background: var(--accent) !important;
                color: var(--on-accent) !important;
                border: 1px solid var(--accent) !important;
                box-shadow: var(--shadow-sm);
                font-weight: 700 !important;
            }
            .stDownloadButton button[kind="primary"] *,
            .stButton button[kind="primary"] * {
                color: var(--on-accent) !important;
            }
            .stDownloadButton button[kind="primary"]:hover,
            .stButton button[kind="primary"]:hover {
                background: var(--accent-hover) !important;
                border-color: var(--accent-hover) !important;
                box-shadow: var(--shadow-md);
                transform: translateY(-1px);
            }
            /* ---------- Dados completos: result chip + toolbar ---------- */
            .result-chip {
                display: inline-flex;
                align-items: center;
                gap: .5rem;
                background: var(--surface-2);
                color: var(--text) !important;
                border: 1px solid var(--border);
                border-radius: var(--radius-pill);
                padding: .5rem 1.1rem;
                font-size: 1.3rem !important;
                font-weight: 600;
                line-height: 1.2;
            }
            .result-chip strong {
                color: var(--primary) !important;
                font-weight: 800 !important;
            }
            .result-chip.filtered {
                border-color: var(--accent);
            }
            .result-chip.filtered strong {
                color: var(--accent) !important;
            }
            /* ---------- Hide Streamlit deploy/menu chrome ---------- */
            [data-testid="stHeader"],
            [data-testid="stToolbar"],
            [data-testid="stDecoration"],
            #MainMenu,
            footer {
                display: none !important;
                visibility: hidden !important;
                height: 0 !important;
            }
            /* ---------- Accessibility ---------- */
            @media (prefers-reduced-motion: reduce) {
                *, *::before, *::after {
                    animation-duration: 0.01ms !important;
                    animation-iteration-count: 1 !important;
                    transition-duration: 0.01ms !important;
                    scroll-behavior: auto !important;
                }
                div[data-testid="stMetric"]:hover,
                div[data-testid="stButtonGroup"] button:hover,
                .stDownloadButton button[kind="primary"]:hover,
                .stButton button[kind="primary"]:hover {
                    transform: none !important;
                }
            }
            /* Visually-hidden helper (kept available for assistive tech) */
            .visually-hidden {
                position: absolute !important;
                width: 1px !important;
                height: 1px !important;
                padding: 0 !important;
                margin: -1px !important;
                overflow: hidden !important;
                clip: rect(0, 0, 0, 0) !important;
                white-space: nowrap !important;
                border: 0 !important;
            }
        </style>
        """
    st.markdown(css, unsafe_allow_html=True)


def render_header() -> None:
    logo_uri = get_logo_data_uri(str(LOGO_PATH))
    logo_html = f'<img class="app-logo" src="{logo_uri}" alt="Logo">' if logo_uri else ""
    st.markdown(
        f"""
            <div class="app-hero app-header">
                {logo_html}
                <h1>IoT Datalogger</h1>
            </div>
        """,
        unsafe_allow_html=True,
    )


def render_welcome_page() -> None:
    logo_uri = get_logo_data_uri(str(LOGO_PATH))
    logo_html = f'<img class="welcome-logo" src="{logo_uri}" alt="Logo">' if logo_uri else ""
    st.markdown(
        f"""
            <div class="welcome-card">
                {logo_html}
                <h1>Bem-vindo ao IoT Datalogger</h1>
                <p>Importe o banco de dados do datalogger para visualizar medições, alarmes e histórico completo.</p>
                <ul class="welcome-features">
                    <li>Indicadores e gráficos das medições em tempo real</li>
                    <li>Monitor de alarmes com histórico classificado</li>
                    <li>Exportação dos dados completos para Excel ou PDF</li>
                </ul>
            </div>
            <div class="welcome-upload-hint">Arraste o arquivo do datalogger ou clique abaixo</div>
        """,
        unsafe_allow_html=True,
    )


def render_table_overview(df: pd.DataFrame, source_name: str) -> None:
    time_col = get_main_time_column(df)

    c1, c2, c4, c5 = st.columns(4)
    c1.metric("Arquivo", source_name)
    c2.metric("Registros", len(df))

    if time_col:
        try:
            c4.metric("Início", df[time_col].min().strftime("%d/%m/%Y %H:%M"))
            c5.metric("Fim", df[time_col].max().strftime("%d/%m/%Y %H:%M"))
        except Exception:
            c4.metric("Início", "-")
            c5.metric("Fim", "-")
    else:
        c4.metric("Início", "-")
        c5.metric("Fim", "-")


def build_temperature_trend_areas(
    plot_df: pd.DataFrame,
    time_col: str,
    temperature_col: str,
) -> pd.DataFrame:
    trend_df = (
        plot_df[[time_col, temperature_col]]
        .dropna()
        .sort_values(time_col)
        .reset_index(drop=True)
    )

    if len(trend_df) < 2:
        return pd.DataFrame(columns=["DataHora", "Temperatura", "Area", "Segmento"])

    trend_df["Delta"] = trend_df[temperature_col].diff()
    trend_df["TrendRaw"] = trend_df["Delta"].apply(
        lambda value: "up" if value > 0 else ("down" if value < 0 else None)
    )

    if trend_df["TrendRaw"].dropna().empty:
        # Sem variação no período inteiro: divide em metade quente/metade fria.
        split_idx = len(trend_df) // 2
        if split_idx <= 0 or split_idx >= len(trend_df):
            return pd.DataFrame(columns=["DataHora", "Temperatura", "Area", "Segmento"])

        first_half = trend_df.iloc[: split_idx + 1].copy()
        second_half = trend_df.iloc[split_idx:].copy()
        first_half["Area"] = "Área quente"
        first_half["Segmento"] = "Área quente_1"
        second_half["Area"] = "Área fria"
        second_half["Segmento"] = "Área fria_1"

        return (
            pd.concat(
                [
                    first_half.rename(columns={time_col: "DataHora", temperature_col: "Temperatura"})[
                        ["DataHora", "Temperatura", "Area", "Segmento"]
                    ],
                    second_half.rename(columns={time_col: "DataHora", temperature_col: "Temperatura"})[
                        ["DataHora", "Temperatura", "Area", "Segmento"]
                    ],
                ],
                ignore_index=True,
            )
            .sort_values("DataHora")
            .reset_index(drop=True)
        )

    # Em cada trecho estável (delta=0), aplica divisão 50/50 (quente/fria).
    trend_df["Trend"] = trend_df["TrendRaw"].copy()
    stable_indices = [
        idx for idx in range(1, len(trend_df))
        if trend_df.loc[idx, "Trend"] is None
    ]
    run_start = None
    prev_idx = None
    stable_runs: list[tuple[int, int]] = []
    for idx in stable_indices:
        if run_start is None:
            run_start = idx
            prev_idx = idx
            continue
        if idx != prev_idx + 1:
            stable_runs.append((run_start, prev_idx))
            run_start = idx
        prev_idx = idx
    if run_start is not None:
        stable_runs.append((run_start, prev_idx))

    for start_idx, end_idx in stable_runs:
        run_len = end_idx - start_idx + 1
        split = run_len // 2
        for offset, idx in enumerate(range(start_idx, end_idx + 1)):
            trend_df.loc[idx, "Trend"] = "up" if offset < split else "down"

    points: list[dict[str, object]] = []
    active_trend = None
    segment_idx = 0

    for idx in range(1, len(trend_df)):
        current_trend = trend_df.loc[idx, "Trend"]
        if current_trend is None:
            continue

        if active_trend is None:
            active_trend = current_trend
            segment_idx += 1
        elif current_trend != active_trend:
            active_trend = current_trend
            segment_idx += 1

        area_name = "Área quente" if active_trend == "up" else "Área fria"
        segment_name = f"{area_name}_{segment_idx}"

        previous_point = trend_df.loc[idx - 1]
        current_point = trend_df.loc[idx]
        points.append(
            {
                "DataHora": previous_point[time_col],
                "Temperatura": previous_point[temperature_col],
                "Area": area_name,
                "Segmento": segment_name,
            }
        )
        points.append(
            {
                "DataHora": current_point[time_col],
                "Temperatura": current_point[temperature_col],
                "Area": area_name,
                "Segmento": segment_name,
            }
        )

    if not points:
        return pd.DataFrame(columns=["DataHora", "Temperatura", "Area", "Segmento"])

    return (
        pd.DataFrame(points)
        .drop_duplicates(subset=["DataHora", "Area", "Segmento"])
        .sort_values("DataHora")
        .reset_index(drop=True)
    )


def build_streamlit_temperature_areas(
    plot_df: pd.DataFrame,
    time_col: str,
    temperature_col: str,
) -> pd.DataFrame:
    trend_df = (
        plot_df[[time_col, temperature_col]]
        .dropna()
        .sort_values(time_col)
        .reset_index(drop=True)
    )

    if trend_df.empty:
        return pd.DataFrame(columns=[time_col, "Área quente", "Área fria"])

    trend = trend_df[temperature_col].diff().apply(
        lambda value: "up" if value > 0 else ("down" if value < 0 else None)
    )
    trend = trend.ffill().bfill()

    streamlit_area_df = trend_df[[time_col, temperature_col]].copy()
    streamlit_area_df["Área quente"] = streamlit_area_df[temperature_col].where(trend.eq("up"))
    streamlit_area_df["Área fria"] = streamlit_area_df[temperature_col].where(trend.eq("down"))
    return streamlit_area_df[[time_col, "Área quente", "Área fria"]]


def compute_temperature_segment_durations(
    plot_df: pd.DataFrame,
    time_col: str,
    temperature_col: str,
) -> pd.DataFrame:
    trend_df = (
        plot_df[[time_col, temperature_col]]
        .dropna()
        .sort_values(time_col)
        .reset_index(drop=True)
    )

    if len(trend_df) < 2:
        return pd.DataFrame(columns=["Area", "Inicio", "Fim", "DuracaoSegundos", "Segmento"])

    trend_df["Delta"] = trend_df[temperature_col].diff()
    trend_df["Trend"] = trend_df["Delta"].apply(
        lambda value: "up" if value > 0 else ("down" if value < 0 else None)
    )

    intervals: list[dict[str, object]] = []
    for idx in range(1, len(trend_df)):
        direction = trend_df.loc[idx, "Trend"]
        if direction is None:
            continue

        start_time = trend_df.loc[idx - 1, time_col]
        end_time = trend_df.loc[idx, time_col]
        if pd.isna(start_time) or pd.isna(end_time) or end_time <= start_time:
            continue

        intervals.append(
            {
                "Trend": direction,
                "Inicio": start_time,
                "Fim": end_time,
            }
        )

    if not intervals:
        return pd.DataFrame(columns=["Area", "Inicio", "Fim", "DuracaoSegundos", "Segmento"])

    segments: list[dict[str, object]] = []
    for interval in intervals:
        area = "Área quente" if interval["Trend"] == "up" else "Área fria"
        if not segments:
            segments.append(
                {
                    "Area": area,
                    "Trend": interval["Trend"],
                    "Inicio": interval["Inicio"],
                    "Fim": interval["Fim"],
                }
            )
            continue

        previous = segments[-1]
        if (
            previous["Trend"] == interval["Trend"]
            and interval["Inicio"] <= previous["Fim"]
        ):
            if interval["Fim"] > previous["Fim"]:
                previous["Fim"] = interval["Fim"]
        else:
            segments.append(
                {
                    "Area": area,
                    "Trend": interval["Trend"],
                    "Inicio": interval["Inicio"],
                    "Fim": interval["Fim"],
                }
            )

    if not segments:
        return pd.DataFrame(columns=["Area", "Inicio", "Fim", "DuracaoSegundos", "Segmento"])

    segment_count = {"Área quente": 0, "Área fria": 0}
    parsed_segments: list[dict[str, object]] = []
    for segment in segments:
        area = str(segment["Area"])
        start_time = segment["Inicio"]
        end_time = segment["Fim"]
        duration_seconds = float((end_time - start_time).total_seconds())
        if duration_seconds <= 0:
            continue

        segment_count[area] = segment_count.get(area, 0) + 1
        parsed_segments.append(
            {
                "Area": area,
                "Inicio": start_time,
                "Fim": end_time,
                "DuracaoSegundos": duration_seconds,
                "Segmento": f"{area} #{segment_count[area]}",
            }
        )

    if not parsed_segments:
        return pd.DataFrame(columns=["Area", "Inicio", "Fim", "DuracaoSegundos", "Segmento"])

    return pd.DataFrame(parsed_segments)


def format_duration(seconds: float) -> str:
    if pd.isna(seconds) or seconds <= 0:
        return "0s"

    total_seconds = int(round(float(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours > 0:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes > 0:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def render_main_plot(df: pd.DataFrame, selected_columns: list[str]) -> None:
    time_col = get_main_time_column(df)

    if not time_col:
        st.info("Não foi possível montar o gráfico porque não encontramos horário/data nos dados.")
        return

    if not selected_columns:
        st.info("Não encontramos valores numéricos para montar o gráfico.")
        return

    plot_df = (
        df[[time_col] + selected_columns]
        .dropna(subset=[time_col])
        .sort_values(time_col)
        .reset_index(drop=True)
    )

    if plot_df.empty:
        st.info("Não há dados suficientes para plotar.")
        return

    st.subheader("Gráfico ao longo do tempo")

    if "chart_reset_nonce" not in st.session_state:
        st.session_state.chart_reset_nonce = 0

    total_records = len(plot_df)
    total_pages = max(1, (total_records + CHART_PAGE_SIZE - 1) // CHART_PAGE_SIZE)
    page_signature = (
        total_records,
        str(plot_df[time_col].min()),
        str(plot_df[time_col].max()),
        tuple(selected_columns),
    )
    page_key = "chart_page_" + str(abs(hash(page_signature)))

    if st.session_state.get("chart_page_signature") != page_signature:
        st.session_state.chart_page_signature = page_signature
        st.session_state[page_key] = total_pages

    if st.session_state.get(page_key, total_pages) > total_pages:
        st.session_state[page_key] = total_pages

    page_col, reset_col = st.columns([1, 2], gap="small")
    with page_col:
        if total_pages > 1:
            current_page = st.number_input(
                "Página do gráfico",
                min_value=1,
                max_value=total_pages,
                value=st.session_state.get(page_key, total_pages),
                step=1,
                key=page_key,
            )
        else:
            current_page = 1
            st.caption("Mostrando todos os registros disponíveis no gráfico.")

    start_idx = (int(current_page) - 1) * CHART_PAGE_SIZE
    end_idx = min(start_idx + CHART_PAGE_SIZE, total_records)
    page_df = plot_df.iloc[start_idx:end_idx]

    with reset_col:
        st.markdown("<div class='button-row-spacer'></div>", unsafe_allow_html=True)
        if st.button("Voltar gráfico ao padrão", use_container_width=True):
            st.session_state.chart_reset_nonce += 1
            st.rerun()

    st.caption(
        f"Mostrando registros {start_idx + 1} a {end_idx} de {total_records}. "
        f"Cada página mostra até {CHART_PAGE_SIZE} registros."
    )

    chart_df = page_df.melt(
        id_vars=time_col,
        value_vars=selected_columns,
        var_name="Medição",
        value_name="Valor",
    )
    x_min_for_chart = page_df[time_col].min()
    x_max_for_chart = page_df[time_col].max()
    y_values = chart_df["Valor"].dropna()
    y_scale = alt.Scale(zero=False)
    y_min_for_chart = -1.0
    y_max_for_chart = 1.0
    if not y_values.empty:
        y_min = float(y_values.min())
        y_max = float(y_values.max())
        y_range = y_max - y_min
        margin = y_range * 0.08 if y_range else max(abs(y_max) * 0.08, 1)
        y_min_for_chart = y_min - margin
        y_max_for_chart = y_max + margin
        y_scale = alt.Scale(domain=[y_min_for_chart, y_max_for_chart], zero=False)

    temperature_reference_col = "Tprincipal" if "Tprincipal" in selected_columns else selected_columns[0]
    trend_areas_df = build_temperature_trend_areas(
        page_df,
        time_col,
        temperature_reference_col,
    )
    segment_durations_df = compute_temperature_segment_durations(
        page_df,
        time_col,
        temperature_reference_col,
    )

    zoom_brush = alt.selection_interval(
        encodings=["x"],
        name=f"brush_{current_page}_{st.session_state.chart_reset_nonce}",
        value={"x": [x_min_for_chart, x_max_for_chart]},
    )
    series_palette = ["#0d9488", "#6366f1", "#f59e0b", "#ec4899", "#14b8a6", "#8b5cf6"]
    hover_selection = alt.selection_point(
        fields=[time_col, "Medição"],
        nearest=True,
        on="pointermove",
        empty=False,
        clear="pointerout",
    )
    line_chart = (
        alt.Chart(chart_df)
        .mark_line(strokeWidth=2.5, interpolate="monotone")
        .encode(
            x=alt.X(
                f"{time_col}:T",
                title="Data e hora",
                scale=alt.Scale(domain=zoom_brush, clamp=True, nice=False),
                axis=alt.Axis(labelFontSize=13, titleFontSize=14, labelPadding=6, format="%H:%M"),
            ),
            y=alt.Y(
                "Valor:Q",
                title="Valor",
                scale=y_scale,
                axis=alt.Axis(labelFontSize=13, titleFontSize=14, labelPadding=6),
            ),
            color=alt.Color(
                "Medição:N",
                title=None,
                scale=alt.Scale(range=series_palette),
                legend=alt.Legend(orient="top", labelFontSize=14, symbolSize=180, symbolStrokeWidth=3),
            ),
            tooltip=[
                alt.Tooltip(f"{time_col}:T", title="Data e hora", format="%d/%m/%Y %H:%M:%S"),
                alt.Tooltip("Medição:N", title="Medição"),
                alt.Tooltip("Valor:Q", title="Valor", format=",.2f"),
            ],
        )
    )
    point_chart = (
        alt.Chart(chart_df)
        .mark_circle(size=90, stroke="white", strokeWidth=1.2)
        .encode(
            x=alt.X(
                f"{time_col}:T",
                scale=alt.Scale(domain=zoom_brush, clamp=True, nice=False),
            ),
            y=alt.Y("Valor:Q", scale=y_scale),
            color=alt.Color("Medição:N", scale=alt.Scale(range=series_palette), legend=None),
            opacity=alt.condition(hover_selection, alt.value(1), alt.value(0)),
            tooltip=[
                alt.Tooltip(f"{time_col}:T", title="Data e hora", format="%d/%m/%Y %H:%M:%S"),
                alt.Tooltip("Medição:N", title="Medição"),
                alt.Tooltip("Valor:Q", title="Valor", format=",.2f"),
            ],
        )
        .add_params(hover_selection)
    )

    chart = alt.layer(line_chart, point_chart)
    if not trend_areas_df.empty:
        trend_areas_df = trend_areas_df.copy()
        trend_areas_df["Base"] = y_min_for_chart
        trend_area_layer = (
            alt.Chart(trend_areas_df)
            .mark_area(opacity=0.22, interpolate="monotone")
            .encode(
                x=alt.X(
                    "DataHora:T",
                    title="Data e hora",
                    scale=alt.Scale(domain=zoom_brush, clamp=True, nice=False),
                ),
                y=alt.Y(
                    "Temperatura:Q",
                    scale=y_scale,
                    title="Valor",
                    axis=alt.Axis(labelFontSize=13, titleFontSize=14, labelPadding=6),
                ),
                y2="Base:Q",
                color=alt.Color(
                    "Area:N",
                    title="Faixa térmica",
                    scale=alt.Scale(domain=["Área quente", "Área fria"], range=["#ef4444", "#3b82f6"]),
                    legend=alt.Legend(orient="top", labelFontSize=13, titleFontSize=13),
                ),
                detail="Segmento:N",
            )
        )
        chart = alt.layer(trend_area_layer, line_chart, point_chart).resolve_scale(color="independent")

    chart = chart.properties(height=450)

    overview_chart = (
        alt.Chart(chart_df)
        .mark_line(strokeWidth=1.2, opacity=0.7)
        .encode(
            x=alt.X(
                f"{time_col}:T",
                title="Faixa para zoom",
                axis=alt.Axis(format="%H:%M"),
            ),
            y=alt.Y("Valor:Q", title=None, axis=alt.Axis(labels=False, ticks=False, domain=False, grid=False)),
            color=alt.Color("Medição:N", scale=alt.Scale(range=series_palette), legend=None),
        )
        .add_params(zoom_brush)
        .properties(height=95)
    )

    chart_with_brush = (
        alt.vconcat(chart, overview_chart, spacing=8)
        .configure_view(strokeWidth=0)
        .configure_axis(
            grid=True,
            gridColor="#e2e8f0",
            gridOpacity=0.6,
            domainColor="#cbd5e1",
            tickColor="#cbd5e1",
            labelColor="#64748b",
            titleColor="#0f172a",
        )
    )
    st.altair_chart(
        chart_with_brush,
        use_container_width=True,
        key=f"main_chart_{current_page}_{st.session_state.chart_reset_nonce}",
    )

    st.caption(
        "Faixas em vermelho indicam momentos de subida de temperatura; "
        "faixas em azul indicam momentos de descida."
    )

    if not segment_durations_df.empty:
        hot_segments = segment_durations_df[segment_durations_df["Area"] == "Área quente"]
        cold_segments = segment_durations_df[segment_durations_df["Area"] == "Área fria"]

        hot_avg_seconds = hot_segments["DuracaoSegundos"].mean() if not hot_segments.empty else 0.0
        cold_avg_seconds = cold_segments["DuracaoSegundos"].mean() if not cold_segments.empty else 0.0
        hot_total_seconds = hot_segments["DuracaoSegundos"].sum() if not hot_segments.empty else 0.0
        cold_total_seconds = cold_segments["DuracaoSegundos"].sum() if not cold_segments.empty else 0.0

        st.caption("Média calculada no período exibido nesta página do gráfico (até 250 pontos).")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Média faixa quente", format_duration(hot_avg_seconds))
        m2.metric("Média faixa fria", format_duration(cold_avg_seconds))
        m3.metric("Tempo total quente", format_duration(hot_total_seconds))
        m4.metric("Tempo total frio", format_duration(cold_total_seconds))
    else:
        st.caption("Não há pontos suficientes nesta página para calcular médias de faixas térmicas.")

ALARM_GLYPHS = {
    "high": "▲",
    "low": "▼",
    "door": "◆",
    "generic": "●",
}
ALARM_LABELS = {
    "high": "Alarme de alta temperatura",
    "low": "Alarme de baixa temperatura",
    "door": "Alarme de porta aberta",
    "generic": "Alarme ativo (sem classificação)",
}


def alarm_kind(temperature, door) -> str:
    if door == 1:
        return "door"
    if pd.notna(temperature) and temperature > TEMP_HIGH_ALARM_LIMIT:
        return "high"
    if pd.notna(temperature) and temperature < TEMP_LOW_ALARM_LIMIT:
        return "low"
    return "generic"


def classify_alarm(temperature, door) -> str:
    return ALARM_LABELS[alarm_kind(temperature, door)]


def render_empty_state(title: str, body: str, glyph: str = "✓") -> None:
    st.markdown(
        f"""
            <div class="empty-state">
                <div class="glyph">{glyph}</div>
                <h3>{title}</h3>
                <p>{body}</p>
            </div>
        """,
        unsafe_allow_html=True,
    )


def render_alarm_monitor(df: pd.DataFrame) -> None:
    st.subheader("Monitor de alarmes")

    if "Alarme" not in df.columns:
        render_empty_state(
            "Sem coluna de alarme",
            "A coluna Alarme não foi encontrada nesta tabela.",
            glyph="?",
        )
        return

    alarm_series = pd.to_numeric(df["Alarme"], errors="coerce").fillna(0)
    alarm_df = df[alarm_series == 1].copy()
    if alarm_df.empty:
        render_empty_state(
            "Nenhum alarme no período",
            "Não encontramos eventos com alarme ativo na janela selecionada.",
            glyph="✓",
        )
        return

    temperature = (
        pd.to_numeric(alarm_df["Tprincipal"], errors="coerce")
        if "Tprincipal" in alarm_df.columns
        else pd.Series(index=alarm_df.index, dtype="float64")
    )
    door = (
        pd.to_numeric(alarm_df["Porta"], errors="coerce").fillna(0)
        if "Porta" in alarm_df.columns
        else pd.Series(index=alarm_df.index, dtype="float64")
    )

    high_temp_mask = temperature > TEMP_HIGH_ALARM_LIMIT
    low_temp_mask = temperature < TEMP_LOW_ALARM_LIMIT
    door_open_mask = door == 1

    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Alarmes ativos", int(len(alarm_df)))
    with a2:
        with st.container(key="kpi-high"):
            st.metric("Alta temperatura", int(high_temp_mask.sum()))
    with a3:
        with st.container(key="kpi-low"):
            st.metric("Baixa temperatura", int(low_temp_mask.sum()))
    with a4:
        with st.container(key="kpi-door"):
            st.metric("Porta aberta", int(door_open_mask.sum()))

    latest_alarm = alarm_df.iloc[-1]
    latest_temp = pd.to_numeric(pd.Series([latest_alarm.get("Tprincipal")]), errors="coerce").iloc[0]
    latest_door = pd.to_numeric(pd.Series([latest_alarm.get("Porta")]), errors="coerce").fillna(0).iloc[0]
    latest_kind = alarm_kind(latest_temp, latest_door)
    latest_label = ALARM_LABELS[latest_kind]
    latest_glyph = ALARM_GLYPHS[latest_kind]

    time_col = get_main_time_column(alarm_df)
    meta_parts = []
    if time_col and pd.notna(latest_alarm.get(time_col)):
        meta_parts.append(latest_alarm[time_col].strftime("%d/%m/%Y %H:%M:%S"))
    if pd.notna(latest_temp):
        meta_parts.append(f"{format_metric(latest_temp)} °C")
    if latest_door == 1:
        meta_parts.append("porta aberta")
    meta_text = " · ".join(meta_parts) if meta_parts else "Sem detalhes adicionais."

    st.markdown(
        f"""
            <div class="latest-alarm-card {latest_kind}">
                <div class="latest-alarm-icon" aria-hidden="true">{latest_glyph}</div>
                <div>
                    <div class="latest-alarm-label">Último alarme</div>
                    <div class="latest-alarm-title">{latest_label}</div>
                    <div class="latest-alarm-meta">{meta_text}</div>
                </div>
            </div>
        """,
        unsafe_allow_html=True,
    )

    def latest_badge(mask: pd.Series, kind: str) -> str:
        filtered = alarm_df[mask]
        if filtered.empty:
            return ""
        row = filtered.iloc[-1]
        if time_col and pd.notna(row.get(time_col)):
            when = row[time_col].strftime("%d/%m/%Y %H:%M")
        else:
            when = "sem data"
        glyph = ALARM_GLYPHS[kind]
        label = ALARM_LABELS[kind].replace("Alarme de ", "").capitalize()
        return f'<span class="alarm-badge {kind}"><span class="glyph" aria-hidden="true">{glyph}</span>Último {label.lower()}: {when}</span>'

    badges = [
        latest_badge(high_temp_mask, "high"),
        latest_badge(low_temp_mask, "low"),
        latest_badge(door_open_mask, "door"),
    ]
    badges = [b for b in badges if b]
    if badges:
        st.markdown(
            f"<div class='last-events-row'>{''.join(badges)}</div>",
            unsafe_allow_html=True,
        )

    st.markdown(
        "<div class='panel-section-title'>Histórico de alarmes</div>",
        unsafe_allow_html=True,
    )

    history_html = [
        "<div class='alarm-history' role='table' aria-label='Histórico de alarmes'>",
        "<div class='alarm-history-head' role='row'>"
        "<span role='columnheader'>Quando</span>"
        "<span role='columnheader'>Tipo</span>"
        "<span role='columnheader'>Temperatura</span>"
        "<span role='columnheader'>Porta</span>"
        "</div>",
    ]
    for _, row in alarm_df.tail(200).iloc[::-1].iterrows():
        row_temp = pd.to_numeric(pd.Series([row.get("Tprincipal")]), errors="coerce").iloc[0]
        row_door = pd.to_numeric(pd.Series([row.get("Porta")]), errors="coerce").fillna(0).iloc[0]
        row_kind = alarm_kind(row_temp, row_door)
        when = format_datetime(row.get(time_col)) if time_col else "-"
        glyph = ALARM_GLYPHS[row_kind]
        label = ALARM_LABELS[row_kind].replace("Alarme de ", "").capitalize()
        history_html.append(
            "<div class='alarm-row' role='row'>"
            f"<time class='alarm-when' role='cell'>{when}</time>"
            f"<span class='alarm-badge {row_kind}' role='cell'><span class='glyph' aria-hidden='true'>{glyph}</span>{label}</span>"
            f"<span role='cell'>{format_metric(row_temp)}</span>"
            f"<span role='cell'>{format_metric(row_door)}</span>"
            "</div>"
        )
    history_html.append("</div>")
    st.markdown("".join(history_html), unsafe_allow_html=True)


def build_report_pdf(df: pd.DataFrame, source_name: str) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.6 * cm,
        rightMargin=1.6 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title="Relatorio Datalogger",
    )
    styles = getSampleStyleSheet()
    story = []

    if LOGO_PATH.exists():
        try:
            story.append(Image(str(LOGO_PATH), width=3.4 * cm, height=1.2 * cm))
            story.append(Spacer(1, 4))
        except Exception:
            pass

    story.append(Paragraph("Relatorio do Datalogger", styles["Title"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"Arquivo: {source_name}", styles["Normal"]))
    story.append(Paragraph(f"Total de registros: {len(df)}", styles["Normal"]))

    temp_series = (
        pd.to_numeric(df["Tprincipal"], errors="coerce")
        if "Tprincipal" in df.columns
        else pd.Series(dtype="float64")
    )
    temp_min = temp_series.min() if not temp_series.empty else float("nan")
    temp_max = temp_series.max() if not temp_series.empty else float("nan")
    temp_avg = temp_series.mean() if not temp_series.empty else float("nan")
    story.append(Spacer(1, 10))
    story.append(Paragraph("Resumo de temperatura", styles["Heading3"]))
    summary_table = Table(
        [
            ["Metrica", "Valor"],
            ["Temperatura minima", format_metric(temp_min)],
            ["Temperatura maxima", format_metric(temp_max)],
            ["Temperatura media", format_metric(temp_avg)],
        ],
        colWidths=[7 * cm, 7 * cm],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ]
        )
    )
    story.append(summary_table)

    time_col = get_main_time_column(df)
    if time_col and "Tprincipal" in df.columns:
        plot_df = (
            df[[time_col, "Tprincipal"]]
            .dropna(subset=[time_col])
            .sort_values(time_col)
            .tail(1200)
            .copy()
        )
        plot_df["Tprincipal"] = pd.to_numeric(plot_df["Tprincipal"], errors="coerce")
        plot_df = plot_df.dropna(subset=["Tprincipal"])
        if not plot_df.empty:
            story.append(Spacer(1, 12))
            story.append(Paragraph("Grafico de temperatura", styles["Heading3"]))

            chart_w = 17.5 * cm
            chart_h = 6.2 * cm
            pad_l, pad_r, pad_t, pad_b = 34, 12, 14, 22
            plot_w = chart_w - pad_l - pad_r
            plot_h = chart_h - pad_t - pad_b
            y_vals_full = plot_df["Tprincipal"].tolist()
            max_points = 260
            if len(y_vals_full) > max_points:
                step = max(1, len(y_vals_full) // max_points)
                sampled = (
                    plot_df.reset_index(drop=True)
                    .groupby(plot_df.reset_index(drop=True).index // step)["Tprincipal"]
                    .mean()
                    .tolist()
                )
                y_vals = sampled
            else:
                y_vals = y_vals_full

            y_min = min(y_vals)
            y_max = max(y_vals)
            if y_min == y_max:
                y_min -= 1
                y_max += 1

            n = len(y_vals)
            points = []
            for i, y in enumerate(y_vals):
                x = pad_l + (i / max(1, n - 1)) * plot_w
                y_norm = (y - y_min) / (y_max - y_min)
                y_pix = pad_b + y_norm * plot_h
                points.extend([x, y_pix])

            drawing = Drawing(chart_w, chart_h)
            drawing.add(Rect(0, 0, chart_w, chart_h, fillColor=colors.white, strokeColor=colors.white))
            drawing.add(Rect(pad_l, pad_b, plot_w, plot_h, fillColor=colors.HexColor("#f8fafc"), strokeColor=colors.HexColor("#cbd5e1"), strokeWidth=0.8))
            for t in (0.25, 0.5, 0.75):
                gy = pad_b + plot_h * t
                drawing.add(Line(pad_l, gy, pad_l + plot_w, gy, strokeColor=colors.HexColor("#e2e8f0"), strokeWidth=0.5))
            drawing.add(PolyLine(points, strokeColor=colors.HexColor("#0d9488"), strokeWidth=1.2))
            drawing.add(String(2, chart_h - 10, "T", fontSize=8, fillColor=colors.HexColor("#64748b")))
            drawing.add(String(chart_w - 52, 4, "tempo", fontSize=8, fillColor=colors.HexColor("#64748b")))
            drawing.add(String(pad_l, 4, f"min: {format_metric(y_min)}", fontSize=8, fillColor=colors.HexColor("#64748b")))
            drawing.add(String(chart_w - 120, chart_h - 10, f"max: {format_metric(y_max)}", fontSize=8, fillColor=colors.HexColor("#64748b")))
            story.append(drawing)

    story.append(Spacer(1, 12))
    story.append(Paragraph("Ultimos alarmes", styles["Heading3"]))
    if "Alarme" in df.columns:
        alarm_series = pd.to_numeric(df["Alarme"], errors="coerce").fillna(0)
        alarm_df = df[alarm_series == 1].copy()
    else:
        alarm_df = pd.DataFrame()

    if alarm_df.empty:
        story.append(Paragraph("Nenhum alarme ativo encontrado.", styles["Normal"]))
    else:
        rows = [["Data e hora", "Tipo", "Temperatura", "Porta"]]
        for _, row in alarm_df.tail(10).iloc[::-1].iterrows():
            row_temp = pd.to_numeric(pd.Series([row.get("Tprincipal")]), errors="coerce").iloc[0]
            row_door = pd.to_numeric(pd.Series([row.get("Porta")]), errors="coerce").fillna(0).iloc[0]
            when = format_datetime(row.get(time_col)) if time_col else "-"
            rows.append(
                [
                    when,
                    classify_alarm(row_temp, row_door),
                    format_metric(row_temp),
                    format_metric(row_door),
                ]
            )
        alarm_table = Table(rows, colWidths=[4.8 * cm, 6.1 * cm, 3 * cm, 2.1 * cm])
        alarm_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(alarm_table)

    doc.build(story)
    return buffer.getvalue()


def main() -> None:
    st.set_page_config(page_title="IoT Datalogger", layout="wide", initial_sidebar_state="collapsed")

    apply_custom_style()
    render_launcher_heartbeat()

    if "file_uploader_nonce" not in st.session_state:
        st.session_state.file_uploader_nonce = 0
    if "reset_main_view_next_run" not in st.session_state:
        st.session_state.reset_main_view_next_run = False

    if st.session_state.reset_main_view_next_run:
        st.session_state.main_view = "Painel"
        st.session_state.reset_main_view_next_run = False

    db_path = None
    df = None
    tables = []
    source_name = "-"

    remembered_path = st.session_state.get("last_db_path")
    if remembered_path and Path(remembered_path).exists():
        db_path = Path(remembered_path)
        source_name = st.session_state.get("last_source_name", db_path.name)

    if db_path is None and not st.session_state.get("ignore_latest_temp_db"):
        latest = get_latest_temp_database()
        if latest is not None:
            db_path = latest
            source_name = latest.name
            st.session_state.last_db_path = str(latest)
            st.session_state.last_source_name = source_name

    if db_path is None:
        render_welcome_page()
        uploaded_file = st.file_uploader(
            "Importar arquivo de dados",
            key=f"welcome_upload_{st.session_state.file_uploader_nonce}",
        )
        if uploaded_file is not None:
            try:
                temp_path, uploaded_name = save_uploaded_database(uploaded_file)
                st.session_state.ignore_latest_temp_db = False
                st.session_state.last_db_path = str(temp_path)
                st.session_state.last_source_name = uploaded_name
                st.success("Arquivo importado com sucesso.")
                st.rerun()
            except Exception as e:
                st.error(f"Não consegui salvar o arquivo enviado: {e}")
        st.stop()
        return

    # Lista tabelas
    try:
        tables = list_tables(str(db_path))
    except Exception as e:
        st.error(f"Não consegui abrir o arquivo de dados: {e}")
        st.stop()
        return

    if not tables:
        st.error("Não encontramos dados para exibir neste arquivo.")
        st.stop()
        return

    with st.container(key="sticky_header_menu"):
        with st.container(key="header-inner"):
            render_header()
            active_view = st.segmented_control(
                "Menu principal",
                ["Painel", "Dados completos", "Alarmes", "Submeter novos dados"],
                default="Painel",
                required=True,
                label_visibility="collapsed",
                width="stretch",
                key="main_view",
            )

    if active_view == "Submeter novos dados":
        st.session_state.pop("last_db_path", None)
        st.session_state.pop("last_source_name", None)
        st.session_state.ignore_latest_temp_db = True
        st.session_state.file_uploader_nonce = st.session_state.get("file_uploader_nonce", 0) + 1
        st.session_state.reset_main_view_next_run = True
        st.rerun()

    default_table = "DataGrpData" if "DataGrpData" in tables else tables[0]
    selected_table = default_table

    # Lê tabela
    try:
        raw_df = read_table(str(db_path), selected_table)
        df = normalize_dataframe(raw_df)
    except Exception as e:
        st.error(f"Não consegui ler o grupo de dados '{selected_table}': {e}")
        st.stop()
        return

    if df is None or df.empty:
        st.warning("Esse grupo foi aberto, mas está sem dados.")
        st.stop()
        return

    if active_view == "Painel":
        render_table_overview(df, source_name)
        filtered_df = render_period_filter(df)
        if filtered_df.empty:
            st.warning("Não há dados para mostrar no período selecionado.")
            st.stop()
            return

        if selected_table == "DataGrpData":
            st.markdown(
                "<div class='panel-section-title'>Indicadores principais</div>",
                unsafe_allow_html=True,
            )
            k1, k2, k3, k4 = st.columns(4)

            with k1:
                with st.container(key="kpi-accent"):
                    if "Tprincipal" in filtered_df.columns:
                        st.metric("Temperatura média", format_metric(filtered_df["Tprincipal"].mean()))
                    else:
                        st.metric("Temperatura média", "-")
            if "Setpoint" in filtered_df.columns:
                k2.metric("Setpoint médio", format_metric(filtered_df["Setpoint"].mean()))
            else:
                k2.metric("Setpoint médio", "-")
            if "Tprincipal" in filtered_df.columns:
                k3.metric("Maior temperatura", format_metric(filtered_df["Tprincipal"].max()))
            else:
                k3.metric("Maior temperatura", "-")
            if "Tprincipal" in filtered_df.columns:
                k4.metric("Menor temperatura", format_metric(filtered_df["Tprincipal"].min()))
            else:
                k4.metric("Menor temperatura", "-")

        plot_columns = get_preferred_plot_columns(filtered_df)
        default_plot_columns = [
            col for col in ["Tprincipal", "Setpoint"]
            if col in plot_columns
        ]
        selected_plot_columns = st.multiselect(
            "Medições no gráfico",
            plot_columns,
            default=default_plot_columns,
        )
        render_main_plot(filtered_df, selected_plot_columns)

    elif active_view == "Dados completos":
        st.subheader("Dados completos")
        search_text = st.text_input(
            "Buscar nos dados",
            placeholder="Digite uma palavra, número ou data",
        )
        display_df = prepare_display_dataframe(df)
        if search_text:
            display_df = display_df[
                display_df.astype(str).apply(
                    lambda row: row.str.contains(search_text, case=False, na=False).any(),
                    axis=1,
                )
            ]

        chip_col, dl_col, report_col = st.columns([2.4, 1, 1.2])
        with chip_col:
            chip_class = "result-chip filtered" if search_text else "result-chip"
            st.markdown(
                f"<span class='{chip_class}'>"
                f"<strong>{len(display_df):,}</strong> de {len(df):,} registros".replace(",", ".")
                + (f" · filtro: <em>{search_text}</em>" if search_text else "")
                + "</span>",
                unsafe_allow_html=True,
            )
        with dl_col:
            excel_buffer = BytesIO()
            display_df.to_excel(excel_buffer, index=False, sheet_name="Dados")
            excel_data = excel_buffer.getvalue()
            source_stem = Path(source_name).stem if source_name else "arquivo"
            st.download_button(
                "Exportar Excel",
                data=excel_data,
                file_name=f"{source_stem}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True,
                disabled=display_df.empty,
            )
        with report_col:
            report_pdf = build_report_pdf(df, source_name)
            source_stem = Path(source_name).stem if source_name else "arquivo"
            st.download_button(
                "Gerar Relatório",
                data=report_pdf,
                file_name=f"relatorio_{source_stem}.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True,
                disabled=df.empty,
            )

        if display_df.empty:
            render_empty_state(
                "Nenhum resultado",
                "Tente ajustar a busca ou limpar o filtro.",
                glyph="∅",
            )
        else:
            st.dataframe(display_df, use_container_width=True, hide_index=True, height=540)

    elif active_view == "Alarmes":
        render_alarm_monitor(df)


if __name__ == "__main__":
    if not is_running_in_streamlit_runtime():
        print("Execute este app com Streamlit:")
        print("  streamlit run app.py")
        sys.exit(0)
    main()
