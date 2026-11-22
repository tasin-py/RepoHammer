"""One-click self update from the app's GitHub releases."""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from . import APP_REPO_URL, VERSION

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _vt(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4]) or (0,)


def check():
    """Return (status, message, release_or_None). status: 'update' | 'latest' | 'error'."""
    m = re.match(r"https?://github\.com/([^/]+)/([^/#?]+)", APP_REPO_URL or "")
    if not m:
        return "error", "The app's GitHub repo is not configured yet, so updates can't be checked.", None
    api = f"https://api.github.com/repos/{m.group(1)}/{m.group(2).removesuffix('.git')}/releases/latest"
    try:
        req = urllib.request.Request(api, headers={"User-Agent": "RepoHammer",
                                                   "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            rel = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "latest", f"You're on {VERSION}. No releases have been published yet.", None
        return "error", f"GitHub answered with an error ({e.code}). Try again later.", None
    except (OSError, ValueError):
        return "error", "Couldn't reach GitHub. Check your internet connection.", None
    tag = rel.get("tag_name", "")
    if _vt(tag) > _vt(VERSION):
        return "update", f"Version {tag} is available (you have {VERSION}).", rel
    return "latest", f"You're up to date ({VERSION}).", None


def apply(rel, log=lambda m: None):
    """Download the release, swap files in after this process exits, then relaunch."""
    assets = [a for a in rel.get("assets", []) if a["name"].lower().endswith(".zip")]
    url = assets[0]["browser_download_url"] if assets else rel["zipball_url"]
    tmp = tempfile.mkdtemp(prefix="repohammer-update-")
    zpath = os.path.join(tmp, "update.zip")
    log("Downloading update...")
    req = urllib.request.Request(url, headers={"User-Agent": "RepoHammer"})
    with urllib.request.urlopen(req, timeout=120) as r, open(zpath, "wb") as f:
        f.write(r.read())
    ex = os.path.join(tmp, "x")
    with zipfile.ZipFile(zpath) as z:
        z.extractall(ex)
    root = ex
    entries = os.listdir(ex)
    if len(entries) == 1 and os.path.isdir(os.path.join(ex, entries[0])):
        root = os.path.join(ex, entries[0])  # GitHub zipball wraps everything in one folder
    if getattr(sys, "frozen", False):
        relaunch = f'start "" "{sys.executable}"'
        target = os.path.dirname(sys.executable)
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        relaunch = f'start "" "{pyw if os.path.isfile(pyw) else sys.executable}" "{os.path.join(APP_DIR, "app.py")}"'
        target = APP_DIR
    script = os.path.join(tmp, "apply.cmd")
    with open(script, "w", encoding="utf-8") as f:
        f.write("@echo off\r\n"
                f":wait\r\ntasklist /FI \"PID eq {os.getpid()}\" | find \"{os.getpid()}\" >nul && "
                "(timeout /t 1 /nobreak >nul & goto wait)\r\n"
                f'robocopy "{root}" "{target}" /E /NFL /NDL /NJH /NJS /R:3 /W:1 >nul\r\n'
                f"{relaunch}\r\n")
    subprocess.Popen(["cmd", "/c", script], creationflags=0x08000000)  # CREATE_NO_WINDOW
    log("Restarting into the new version...")
    return True
