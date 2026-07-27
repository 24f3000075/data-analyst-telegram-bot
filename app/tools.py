import hashlib
import os
import subprocess
import sys
import textwrap
from urllib.parse import urlparse

import requests

from app.config import DOWNLOADS_DIR, FETCH_TIMEOUT, PYTHON_EXEC_TIMEOUT

# ---------------------------------------------------------------------------
# Tool schemas passed to the Anthropic API. `web_search` is a server-side tool
# (Anthropic runs it and injects results automatically); the other two are
# client-side tools we execute ourselves in execute_tool() below.
# ---------------------------------------------------------------------------

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}

FETCH_URL_TOOL = {
    "name": "fetch_url",
    "description": (
        "Download a public URL (CSV, XLSX, JSON, HTML, PDF, or any file) to local "
        "disk so it can be analyzed with run_python. Returns the local file path, "
        "detected content type, size, and a short text preview. Use this before "
        "run_python whenever the question points at an external dataset (MOSPI, "
        "data.gov.in, etc.) instead of trying to guess the data's contents."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to download."},
        },
        "required": ["url"],
    },
}

RUN_PYTHON_TOOL = {
    "name": "run_python",
    "description": (
        "Execute a Python snippet in a sandboxed subprocess and return its stdout "
        "(plus stderr on failure). Pandas, numpy, openpyxl, requests, pdfplumber, "
        "and bs4 are pre-installed. Use this to load files fetch_url downloaded "
        "(paths are given to you in fetch_url's result) and compute the actual "
        "answer -- print() whatever you need to see, including the final "
        "computed value. Each call is a fresh process: no state carries over "
        "between run_python calls, so print intermediate results you need later "
        "and recompute or re-load data as needed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "The Python code to run."},
        },
        "required": ["code"],
    },
}

CLIENT_TOOLS = [FETCH_URL_TOOL, RUN_PYTHON_TOOL]
ALL_TOOLS = [WEB_SEARCH_TOOL] + CLIENT_TOOLS


def _guess_extension(url: str, content_type: str) -> str:
    path = urlparse(url).path
    if "." in os.path.basename(path):
        return os.path.basename(path).split(".")[-1][:10]
    mapping = {
        "text/csv": "csv",
        "application/vnd.ms-excel": "xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/json": "json",
        "application/pdf": "pdf",
        "text/html": "html",
    }
    for key, ext in mapping.items():
        if key in content_type:
            return ext
    return "bin"


def _preview_bytes(path: str, ext: str, content_type: str) -> str:
    try:
        if ext in ("csv", "tsv"):
            import pandas as pd

            df = pd.read_csv(path, nrows=5, sep=None, engine="python")
            return f"columns={list(df.columns)}\n{df.head(5).to_string()}"
        if ext in ("xlsx", "xls"):
            import pandas as pd

            xls = pd.ExcelFile(path)
            sheets = xls.sheet_names
            df = xls.parse(sheets[0], nrows=5)
            return f"sheets={sheets}\nfirst_sheet_columns={list(df.columns)}\n{df.head(5).to_string()}"
        if ext == "pdf":
            import pdfplumber

            with pdfplumber.open(path) as pdf:
                n_pages = len(pdf.pages)
                text = (pdf.pages[0].extract_text() or "")[:1500]
            return f"pages={n_pages}\nfirst_page_text_preview={text}"
        if ext in ("json",):
            with open(path, "r", errors="replace") as f:
                return f.read(1500)
        # html / txt / anything else -> raw text preview
        with open(path, "r", errors="replace") as f:
            return f.read(1500)
    except Exception as e:  # noqa: BLE001
        return f"(could not generate preview: {e})"


def fetch_url(url: str) -> dict:
    resp = requests.get(
        url,
        timeout=FETCH_TIMEOUT,
        headers={"User-Agent": "data-analyst-agent/1.0"},
        stream=True,
    )
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    ext = _guess_extension(url, content_type)

    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    local_path = os.path.join(DOWNLOADS_DIR, f"{digest}.{ext}")

    total = 0
    with open(local_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            f.write(chunk)
            total += len(chunk)
            if total > 50 * 1024 * 1024:  # 50MB safety cap
                break

    preview = _preview_bytes(local_path, ext, content_type)
    return {
        "local_path": local_path,
        "content_type": content_type,
        "size_bytes": total,
        "preview": preview,
    }


def run_python(code: str) -> dict:
    wrapped = textwrap.dedent(code)
    script_path = os.path.join(DOWNLOADS_DIR, "_agent_snippet.py")
    with open(script_path, "w") as f:
        f.write(wrapped)

    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            cwd=DOWNLOADS_DIR,
            capture_output=True,
            text=True,
            timeout=PYTHON_EXEC_TIMEOUT,
        )
        return {
            "stdout": proc.stdout[-6000:],
            "stderr": proc.stderr[-3000:],
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": f"Execution timed out after {PYTHON_EXEC_TIMEOUT}s",
            "returncode": -1,
        }


def execute_tool(name: str, tool_input: dict) -> dict:
    if name == "fetch_url":
        return fetch_url(tool_input["url"])
    if name == "run_python":
        return run_python(tool_input["code"])
    raise ValueError(f"Unknown client tool: {name}")
