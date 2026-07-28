import hashlib
import os
import subprocess
import sys
import textwrap
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from app.config import DOWNLOADS_DIR, FETCH_TIMEOUT, PYTHON_EXEC_TIMEOUT


def web_search(query: str) -> dict:
    """Free, no-API-key web search via DuckDuckGo's HTML endpoint."""
    resp = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers={"User-Agent": "Mozilla/5.0 (data-analyst-agent/1.0)"},
        timeout=8,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    for result in soup.select(".result")[:8]:
        link_tag = result.select_one(".result__a")
        snippet_tag = result.select_one(".result__snippet")
        if not link_tag:
            continue
        results.append(
            {
                "title": link_tag.get_text(strip=True),
                "url": link_tag.get("href", ""),
                "snippet": snippet_tag.get_text(strip=True) if snippet_tag else "",
            }
        )
    return {"query": query, "results": results}


TOOL_SPECS = [
    {
        "name": "web_search",
        "description": (
            "Search the web and return a list of {title, url, snippet} results. "
            "Use this to find the actual page/report/dataset for a question "
            "referencing MOSPI, data.gov.in, or other public statistics -- then "
            "fetch_url the real URL you find here."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING", "description": "Search query."}},
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "Download a public URL (CSV, XLSX, JSON, HTML, PDF, or any file) to local "
            "disk so it can be analyzed with run_python. Returns the local file path, "
            "detected content type, size, and a short text preview."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {"url": {"type": "STRING", "description": "The URL to download."}},
            "required": ["url"],
        },
    },
    {
        "name": "run_python",
        "description": (
            "Execute a Python snippet in a sandboxed subprocess and return its stdout "
            "(plus stderr on failure). Pandas, numpy, openpyxl, requests, pdfplumber, "
            "and bs4 are pre-installed. Use this to load files fetch_url downloaded "
            "and compute the actual answer -- print() whatever you need to see, "
            "including the final computed value. Each call is a fresh process: no "
            "state carries over between run_python calls, so print intermediate "
            "results you need later and recompute or re-load data as needed."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {"code": {"type": "STRING", "description": "The Python code to run."}},
            "required": ["code"],
        },
    },
]


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
        with open(path, "r", errors="replace") as f:
            return f.read(1500)
    except Exception as e:
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
            if total > 50 * 1024 * 1024:
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
    if name == "web_search":
        return web_search(tool_input["query"])
    if name == "fetch_url":
        return fetch_url(tool_input["url"])
    if name == "run_python":
        return run_python(tool_input["code"])
    raise ValueError(f"Unknown client tool: {name}")
