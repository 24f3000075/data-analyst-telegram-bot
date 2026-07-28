import requests

from app.config import GITHUB_TOKEN

GIST_API = "https://api.github.com/gists"


def publish_gist(filename: str, content: str, description: str):
    if not GITHUB_TOKEN:
        return None
    try:
        resp = requests.post(
            GIST_API,
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
            json={
                "description": description,
                "public": True,
                "files": {filename: {"content": content}},
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["files"][filename]["raw_url"]
    except Exception:
        return None
