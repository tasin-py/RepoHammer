"""Get source code: GitHub URL, owner/repo shorthand, any git URL, or a local folder."""
import io
import json
import os
import re
import shutil
import subprocess
import urllib.request
import zipfile

from . import toolchain

UA = {"User-Agent": "RepoHammer"}


def parse(spec):
    """Return dict(kind, url, owner, repo, ref, path)."""
    spec = spec.strip().strip('"')
    if os.path.isdir(spec):
        name = os.path.basename(os.path.abspath(spec))
        return {"kind": "local", "path": os.path.abspath(spec), "owner": "local", "repo": name, "ref": None}
    m = re.match(r"^(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/([^/\s#?]+?)(?:\.git)?"
                 r"(?:/(?:tree|releases/tag|commit)/([^\s?#]+))?/?(?:[?#].*)?$", spec)
    if not m:
        m = re.match(r"^git@github\.com:([^/\s]+)/([^/\s]+?)(?:\.git)?$", spec)
    if not m:
        m = re.match(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:@(\S+))?$", spec)
    if m:
        owner, repo = m.group(1), m.group(2)
        ref = m.group(3) if m.lastindex and m.lastindex >= 3 else None
        return {"kind": "github", "url": f"https://github.com/{owner}/{repo}.git",
                "owner": owner, "repo": repo, "ref": ref}
    if re.match(r"^(https?|git|ssh)://|^[\w.-]+@[\w.-]+:", spec):
        repo = re.sub(r"\.git$", "", spec.rstrip("/").split("/")[-1].split(":")[-1])
        return {"kind": "git", "url": spec, "owner": "git", "repo": repo, "ref": None}
    raise ValueError(f"Not a GitHub link, git URL or folder: {spec}")


def _git(args, cwd, log):
    log("$ git " + " ".join(args))
    p = subprocess.Popen(["git", *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, text=True, errors="replace",
                         env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    for line in p.stdout:
        log("  " + line.rstrip())
    return p.wait() == 0


def fetch(spec, workdir, log, fresh=False, allow_install=True):
    """Download the source and return (info, source_dir)."""
    info = parse(spec)
    if info["kind"] == "local":
        log(f"[fetch] using local folder {info['path']}")
        return info, info["path"]
    dest = os.path.join(workdir, "repos", f"{info['owner']}__{info['repo']}")
    if fresh and os.path.isdir(dest):
        log("[fetch] removing old copy")
        shutil.rmtree(dest, onerror=_force_remove)
    have_git = toolchain.ensure("git", log, allow_install)
    if have_git:
        if os.path.isdir(os.path.join(dest, ".git")):
            log("[fetch] updating existing clone")
            ok = _git(["fetch", "--depth", "1", "origin", info["ref"] or "HEAD"], dest, log) and \
                _git(["reset", "--hard", "FETCH_HEAD"], dest, log)
            _git(["submodule", "update", "--init", "--recursive", "--depth", "1"], dest, log)
            if ok:
                return info, dest
            shutil.rmtree(dest, onerror=_force_remove)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        args = ["clone", "--depth", "1", "--recurse-submodules", "--shallow-submodules"]
        if info["ref"]:
            args += ["--branch", info["ref"]]
        if _git(args + [info["url"], dest], None, log):
            return info, dest
        if info["ref"]:  # ref may be a commit sha: full clone + checkout
            shutil.rmtree(dest, ignore_errors=True)
            if _git(["clone", "--recurse-submodules", info["url"], dest], None, log) and \
                    _git(["checkout", info["ref"]], dest, log):
                return info, dest
    if info["kind"] != "github":
        raise RuntimeError("git clone failed")
    log("[fetch] git unavailable or failed - downloading zip archive instead")
    return info, _zip(info, dest, log)


def _zip(info, dest, log):
    ref = info["ref"]
    if not ref:
        req = urllib.request.Request(f"https://api.github.com/repos/{info['owner']}/{info['repo']}", headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            ref = json.load(r)["default_branch"]
    url = f"https://codeload.github.com/{info['owner']}/{info['repo']}/zip/{ref}"
    log(f"[fetch] {url}")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        data = r.read()
    shutil.rmtree(dest, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = z.namelist()[0].split("/")[0]
        tmp = dest + ".tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        z.extractall(tmp)
    os.replace(os.path.join(tmp, root), dest)
    shutil.rmtree(tmp, ignore_errors=True)
    log("[fetch] note: zip downloads do not include git submodules")
    return dest


def _force_remove(func, path, _exc):
    os.chmod(path, 0o666)
    func(path)
