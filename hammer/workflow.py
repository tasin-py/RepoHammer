"""Run a repo's GitHub Actions build workflow locally on this PC.

Reads .github/workflows/*.yml (tiny built-in YAML reader, no dependencies), picks the job that
builds on Windows, maps setup-* actions to local toolchains, emulates GITHUB_OUTPUT / GITHUB_ENV /
${{ }} expressions, and skips CI-only steps (publishing, releases, secrets).
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

from . import toolchain

IS_WIN = os.name == "nt"


# ------------------------------------------------------------------ minimal YAML
def _scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1].replace("''", "'") if v[0] == "'" else v[1:-1]
    return v


def _strip_comment(line):
    out, q = [], None
    for i, ch in enumerate(line):
        if q:
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def parse_yaml(text):
    """Enough YAML for workflow files: maps, lists, block scalars (| >), quoted/flow scalars."""
    lines = text.replace("\t", "  ").splitlines()
    pos = 0

    def indent(s):
        return len(s) - len(s.lstrip(" "))

    def skip_blank():
        nonlocal pos
        while pos < len(lines) and not _strip_comment(lines[pos]).strip():
            pos += 1

    def block_scalar(min_ind, folded):
        nonlocal pos
        body, ind = [], None
        while pos < len(lines):
            ln = lines[pos]
            if ln.strip() == "":
                body.append("")
                pos += 1
                continue
            i = indent(ln)
            if ind is None:
                if i < min_ind:
                    break
                ind = i
            if i < ind:
                break
            body.append(ln[ind:])
            pos += 1
        while body and body[-1] == "":
            body.pop()
        return (" ".join(body) if folded else "\n".join(body)) + "\n"

    def value_after(rest, ind):
        nonlocal pos
        rest = rest.strip()
        if rest in ("|", "|-", "|+", ">", ">-", ">+"):
            return block_scalar(ind + 1, rest.startswith(">"))
        if rest:
            return _scalar(rest)
        skip_blank()
        if pos < len(lines) and indent(lines[pos]) > ind or \
                (pos < len(lines) and lines[pos].lstrip().startswith("- ") and indent(lines[pos]) >= ind):
            return node(indent(lines[pos]))
        return ""

    def node(ind):
        nonlocal pos
        skip_blank()
        if pos >= len(lines):
            return None
        if _strip_comment(lines[pos]).lstrip().startswith("- ") or _strip_comment(lines[pos]).strip() == "-":
            items = []
            while True:
                skip_blank()
                if pos >= len(lines) or indent(lines[pos]) != ind:
                    break
                s = _strip_comment(lines[pos]).lstrip()
                if not (s.startswith("- ") or s == "-"):
                    break
                rest = s[2:] if s.startswith("- ") else ""
                if re.match(r"^[\w.-]+\s*:(\s|$)", rest):
                    # list item that is a map: re-indent its first line and parse as map
                    lines[pos] = " " * (ind + 2) + rest
                    items.append(node(ind + 2))
                else:
                    pos += 1
                    items.append(value_after(rest, ind) if rest else node(ind + 2))
            return items
        m = {}
        while True:
            skip_blank()
            if pos >= len(lines) or indent(lines[pos]) != ind:
                break
            s = _strip_comment(lines[pos]).strip()
            mm = re.match(r"^(\"[^\"]*\"|'[^']*'|[^:]+?)\s*:(?:\s+(.*)|$)", s)
            if not mm:
                pos += 1
                continue
            pos += 1
            m[_scalar(mm.group(1))] = value_after(mm.group(2) or "", ind)
        return m

    return node(0) or {}


# ------------------------------------------------------------------ job selection
SKIP_USES = ("actions/checkout", "actions/cache", "actions/upload", "actions/download", "softprops/",
             "actions/create-release", "ncipollo/", "codecov", "github/codeql", "actions/github-script",
             "actions/attest", "actions/labeler", "peaceiris/", "JamesIves/", "docker/login")
SKIP_WORDS = ("gh release", "gh_token", "github_token", "secrets.", "publish", "deploy", "upload",
              "twine", "docker push", "signtool", "codesign", "gpg ", "release create", "git push",
              "git tag", "notarize", "chocolatey push", "winget-create", "vsce publish")
SOFT_WORDS = ("test", "check", "lint", "verify", "must", "validate", "format", "audit", "smoke")
CHOCO_TO_TOOL = {"innosetup": "iscc", "nsis": "makensis", "7zip": "7z", "7zip.install": "7z",
                 "cmake": "cmake", "ninja": "ninja", "nodejs": "node", "nodejs-lts": "node",
                 "python": "python", "git": "git", "wixtoolset": "wix", "llvm": "llvm", "make": "make",
                 "dotnet-sdk": "dotnet", "golang": "go", "rustup.install": "cargo", "zig": "zig"}


def _is_windows_job(job):
    ro = str(job.get("runs-on", ""))
    if "windows" in ro.lower():
        return True
    if "matrix" in ro:
        return "windows" in str(job.get("strategy", "")).lower()
    return False


def _score(jid, job):
    text = (jid + " " + str(job.get("name", ""))).lower()
    steps = job.get("steps") or []
    runs = " ".join(str(s.get("run", "")) for s in steps if isinstance(s, dict)).lower()
    score = 0
    score += 3 * any(w in text for w in ("build", "release", "package", "dist", "installer", "bundle"))
    score += 2 * any(w in runs for w in ("pyinstaller", "msbuild", "dotnet publish", "dotnet build", "cargo build",
                                         "npm run build", "pnpm build", "iscc", "makensis", "go build",
                                         "cmake --build", "gradlew", "mvn ", "electron-builder", "tauri"))
    score -= 2 * any(w in text for w in ("lint", "docs", "codeql", "stale", "label"))
    return score


def find_jobs(repo):
    """[(score, file, job_id, job, workflow)] for Windows jobs that look like builds, best first."""
    out = []
    for f in sorted(glob.glob(os.path.join(repo, ".github", "workflows", "*.y*ml"))):
        try:
            wf = parse_yaml(open(f, encoding="utf-8", errors="replace").read())
        except Exception:
            continue
        for jid, job in (wf.get("jobs") or {}).items():
            if isinstance(job, dict) and _is_windows_job(job) and isinstance(job.get("steps"), list):
                s = _score(jid, job)
                if s > 0:
                    out.append((s, f, jid, job, wf))
    out.sort(key=lambda t: -t[0])
    return out


# ------------------------------------------------------------------ runtime
class Run:
    """State shared by the steps of one workflow job while it runs locally."""

    def __init__(self, repo, wf, job, builder_log):
        self.repo = repo
        self.log = builder_log
        self.outputs = {}
        self.env = {}
        self.path = []
        self.tmp = tempfile.mkdtemp(prefix="rf-runner-")
        self.matrix = {}
        # set by the builder at run time: ask before downloading anything
        self.need = lambda key, context="": toolchain.ensure(key, self.log, True)
        self.allow = lambda items, context="": True
        mx = (job.get("strategy") or {}).get("matrix") if isinstance(job.get("strategy"), dict) else None
        if isinstance(mx, dict):
            for k, v in mx.items():
                if isinstance(v, list) and v:
                    win = [x for x in v if "windows" in str(x).lower()]
                    self.matrix[k] = str((win or v)[0])
        for scope in (wf.get("env"), job.get("env")):
            if isinstance(scope, dict):
                self.env.update({k: str(v) for k, v in scope.items()})
        self.ref_name = _latest_tag(repo)
        self.env.setdefault("CI", "true")
        self.env.update({"GITHUB_ACTIONS": "true", "GITHUB_WORKSPACE": repo, "RUNNER_TEMP": self.tmp,
                         "RUNNER_OS": "Windows", "GITHUB_REF_NAME": self.ref_name,
                         "GITHUB_REF": f"refs/tags/{self.ref_name}" if self.ref_name else "refs/heads/main",
                         "GITHUB_SHA": _git(repo, "rev-parse", "HEAD"),
                         "GITHUB_REPOSITORY": _repo_slug(repo)})

    def expr(self, text):
        def sub(m):
            e = m.group(1).strip()
            mm = re.fullmatch(r"steps\.([\w-]+)\.outputs\.([\w-]+)", e)
            if mm:
                return self.outputs.get(mm.group(1), {}).get(mm.group(2), "")
            mm = re.fullmatch(r"(?:env)\.([\w-]+)", e)
            if mm:
                return self.env.get(mm.group(1), os.environ.get(mm.group(1), ""))
            mm = re.fullmatch(r"matrix\.([\w-]+)", e)
            if mm:
                return self.matrix.get(mm.group(1), "")
            return {"github.ref_name": self.ref_name, "github.workspace": self.repo, "runner.temp": self.tmp,
                    "runner.os": "Windows", "github.ref": self.env["GITHUB_REF"],
                    "github.sha": self.env["GITHUB_SHA"], "github.repository": self.env["GITHUB_REPOSITORY"],
                    "github.event_name": "push", "runner.arch": "X64"}.get(e, "")
        return re.sub(r"\$\{\{(.*?)\}\}", sub, str(text))

    def step_env(self, extra):
        env = dict(self.env)
        if isinstance(extra, dict):
            env.update({k: self.expr(v) for k, v in extra.items()})
        if self.path:
            env["PATH"] = os.pathsep.join(self.path + [os.environ.get("PATH", "")])
        return env


class Step:
    """One workflow step, resolved at run time (so earlier outputs are available)."""

    def __init__(self, run, spec, index, shell_default):
        self.run, self.spec = run, spec
        self.id = spec.get("id") or f"step{index}"
        self.label = spec.get("name") or (spec.get("run", "").strip().splitlines() or ["step"])[0][:70]
        self.shell = spec.get("shell") or shell_default
        self.soft = any(w in self.label.lower() for w in SOFT_WORDS) or \
            str(spec.get("continue-on-error", "")).lower() == "true"
        self.files = {}

    def __call__(self):
        r = self.run
        script = r.expr(self.spec["run"])
        script = self._choco(script)
        for k in ("GITHUB_OUTPUT", "GITHUB_ENV", "GITHUB_PATH", "GITHUB_STEP_SUMMARY"):
            p = os.path.join(r.tmp, f"{self.id}_{k.lower()}")
            open(p, "w").close()
            self.files[k] = p
        shell = self.shell.split()[0].lower()
        wd = self.spec.get("working-directory")
        cwd = os.path.join(r.repo, r.expr(wd)) if wd else r.repo
        if shell in ("pwsh", "powershell"):
            body = ("$ErrorActionPreference = 'Stop'\n$ProgressPreference = 'SilentlyContinue'\n"
                    f"Set-Location -LiteralPath '{cwd}'\n" + script +
                    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n")
            f = os.path.join(r.tmp, f"{self.id}.ps1")
            open(f, "w", encoding="utf-8-sig").write(body)
            exe = "pwsh" if shutil.which("pwsh") else "powershell"
            return f'{exe} -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{f}"'
        if shell == "bash":
            f = os.path.join(r.tmp, f"{self.id}.sh")
            open(f, "w", encoding="utf-8", newline="\n").write(f"set -eo pipefail\ncd '{cwd}'\n" + script)
            return f'bash "{f}"'
        if shell == "python":
            f = os.path.join(r.tmp, f"{self.id}.py")
            open(f, "w", encoding="utf-8").write(script)
            return f'cd /d "{cwd}" && python "{f}"'
        f = os.path.join(r.tmp, f"{self.id}.cmd")  # cmd
        open(f, "w", encoding="utf-8").write(f'@echo off\r\ncd /d "{cwd}"\r\n' + script.replace("\n", "\r\n"))
        return f'cmd /d /c "{f}"'

    def env(self):
        e = self.run.step_env(self.spec.get("env"))
        e.update(self.files)
        return e

    def after(self):
        """Read what the step wrote to GITHUB_OUTPUT / GITHUB_ENV / GITHUB_PATH."""
        r = self.run
        r.outputs[self.id] = _kv(self.files.get("GITHUB_OUTPUT"))
        r.env.update(_kv(self.files.get("GITHUB_ENV")))
        try:
            for p in open(self.files.get("GITHUB_PATH", ""), encoding="utf-8-sig").read().splitlines():
                if p.strip():
                    r.path.insert(0, p.strip())
        except OSError:
            pass

    def _choco(self, script):
        """Local PCs rarely run choco as admin: install known packages through RepoHammer instead."""
        def repl(m):
            pkgs = [p for p in m.group(1).split() if not p.startswith("-")]
            keep = []
            for p in pkgs:
                tool = CHOCO_TO_TOOL.get(p.lower())
                if tool:
                    self.run.need(tool, "the workflow installs it")
                else:
                    keep.append(p)
            return ("choco install " + " ".join(keep) + " -y") if keep else "# (installed by RepoHammer)"
        return re.sub(r"(?m)^[ \t]*choco install ([^\r\n;|&]+)", repl, script)


def _kv(path):
    out = {}
    try:
        text = open(path, encoding="utf-8-sig", errors="replace").read()
    except (OSError, TypeError):
        return out
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r"^([\w.-]+)<<(\S+)$", ln)
        if m:
            j = i + 1
            buf = []
            while j < len(lines) and lines[j] != m.group(2):
                buf.append(lines[j])
                j += 1
            out[m.group(1)] = "\n".join(buf)
            i = j + 1
            continue
        if "=" in ln:
            k, v = ln.split("=", 1)
            out[k.strip()] = v
        i += 1
    return out


def _git(repo, *args):
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _latest_tag(repo):
    """Tag pointing at HEAD (asks the remote, since clones are shallow), else newest tag."""
    head = _git(repo, "rev-parse", "HEAD")
    out = _git(repo, "ls-remote", "--tags", "--sort=-v:refname", "origin")
    tags = []
    for ln in out.splitlines():
        sha, _, ref = ln.partition("\t")
        name = ref.removeprefix("refs/tags/").removesuffix("^{}")
        tags.append((sha, name))
        if sha == head:
            return name
    return tags[0][1] if tags else ""


def _repo_slug(repo):
    url = _git(repo, "remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", url)
    return m.group(1) if m else ""


def setup_action(run, uses, with_):
    """Map a setup-* action to a local tool. Returns False if the build can't continue."""
    u = uses.lower()
    with_ = {k: run.expr(v) for k, v in (with_ or {}).items()} if isinstance(with_, dict) else {}
    if u.startswith("actions/setup-python"):
        ver = with_.get("python-version", "").strip()
        py = _python_for(ver, run)
        if not py:
            return False
        venv = os.path.join(run.repo, ".rf-venv")
        if not os.path.isfile(os.path.join(venv, "Scripts", "python.exe")):
            run.log(f"[workflow] creating a private Python {ver or ''} environment (.rf-venv)")
            subprocess.run([py, "-m", "venv", venv], capture_output=True)
        run.path.insert(0, os.path.join(venv, "Scripts"))
        run.env["VIRTUAL_ENV"] = venv
        return True
    simple = {"actions/setup-node": "node", "actions/setup-dotnet": "dotnet", "actions/setup-java": "java",
              "actions/setup-go": "go", "dtolnay/rust-toolchain": "cargo", "actions-rs/toolchain": "cargo",
              "pnpm/action-setup": "pnpm", "oven-sh/setup-bun": "bun", "denoland/setup-deno": "deno",
              "goto-bus-stop/setup-zig": "zig", "mlugg/setup-zig": "zig", "lukka/get-cmake": "cmake",
              "seanmiddleditch/gha-setup-ninja": "ninja", "subosito/flutter-action": "flutter",
              "ilammy/msvc-dev-cmd": "msvc", "egor-tensin/setup-mingw": "make"}
    for prefix, tool in simple.items():
        if u.startswith(prefix):
            if tool == "pnpm":
                run.need("node")
            return run.need(tool, f"the workflow uses {uses}")
    if u.startswith("microsoft/setup-msbuild"):
        msb = toolchain.find_msbuild() if run.need("msbuild", "the workflow uses MSBuild") else ""
        if msb:
            run.path.insert(0, os.path.dirname(msb))
        return bool(msb)
    if u.startswith("nuget/setup-nuget"):
        return run.need("nuget", "the workflow uses NuGet")
    return True


def _python_for(ver, run):
    log = run.log
    if not ver:
        return toolchain.find("python")
    want = re.match(r"(\d+)\.(\d+)", ver)
    if not want:
        return toolchain.find("python")
    mm = f"{want.group(1)}.{want.group(2)}"
    if sys.version.startswith(mm + ".") and not getattr(sys, "frozen", False):
        return sys.executable
    for cmd in (["py", f"-{mm}"], [f"python{mm}"]):
        try:
            p = subprocess.run(cmd + ["-c", "import sys;print(sys.executable)"], capture_output=True, text=True)
            if p.returncode == 0 and p.stdout.strip():
                return p.stdout.strip()
        except OSError:
            pass
    if not run.allow([(f"python-{mm}", f"Python {mm}", "30 MB")], "the workflow asks for this Python version"):
        return toolchain.find("python")
    log(f"[workflow] Python {mm} not found - installing it")
    toolchain._run(["winget", "install", "--id", f"Python.Python.{mm}", "-e", "--silent",
                    "--accept-package-agreements", "--accept-source-agreements"], log)
    try:
        p = subprocess.run(["py", f"-{mm}", "-c", "import sys;print(sys.executable)"], capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else toolchain.find("python")
    except OSError:
        return toolchain.find("python")


class SetupStep:
    def __init__(self, run, spec):
        self.run, self.spec = run, spec
        self.label = f"{spec.get('name') or spec['uses']}  (local setup)"
        self.soft = False

    def __call__(self):
        ok = setup_action(self.run, self.spec["uses"], self.spec.get("with"))
        return "exit /b 0" if ok else "exit /b 1"


def build_steps(repo, wf, job, log):
    """Turn a job into a list of runnable step objects. Returns (steps, skipped_labels)."""
    run = Run(repo, wf, job, log)
    defaults = (job.get("defaults") or wf.get("defaults") or {})
    shell = ((defaults.get("run") or {}).get("shell") if isinstance(defaults, dict) else None) or \
        ("pwsh" if IS_WIN else "bash")
    steps, skipped = [], []
    for i, s in enumerate(job.get("steps") or [], 1):
        if not isinstance(s, dict):
            continue
        cond = str(s.get("if", "")).lower()
        if any(w in cond for w in ("failure()", "cancelled()", "pull_request")):
            skipped.append(s.get("name") or f"step {i}")
            continue
        blob = (str(s.get("name", "")) + " " + str(s.get("run", "")) + " " + str(s.get("env", ""))).lower()
        if "uses" in s:
            u = str(s["uses"]).lower()
            if any(u.startswith(x.lower()) for x in SKIP_USES):
                skipped.append(s.get("name") or s["uses"])
                continue
            steps.append(SetupStep(run, s))
            continue
        if "run" not in s:
            continue
        if any(w in blob for w in SKIP_WORDS):
            skipped.append(s.get("name") or f"step {i}")
            continue
        steps.append(Step(run, s, i, s.get("shell") or shell))
    return run, steps, skipped


SETUP_TOOLS = {"actions/setup-node": "node", "actions/setup-dotnet": "dotnet", "actions/setup-java": "java",
               "actions/setup-go": "go", "dtolnay/rust-toolchain": "cargo", "actions-rs/toolchain": "cargo",
               "pnpm/action-setup": "pnpm", "oven-sh/setup-bun": "bun", "denoland/setup-deno": "deno",
               "goto-bus-stop/setup-zig": "zig", "mlugg/setup-zig": "zig", "lukka/get-cmake": "cmake",
               "seanmiddleditch/gha-setup-ninja": "ninja", "subosito/flutter-action": "flutter",
               "ilammy/msvc-dev-cmd": "msvc", "egor-tensin/setup-mingw": "make",
               "microsoft/setup-msbuild": "msbuild", "nuget/setup-nuget": "nuget"}


def required_tools(steps):
    """Tools a job will need, so the user can be asked about all downloads at once."""
    tools = []
    for st in steps:
        spec = st.spec
        if "uses" in spec:
            u = str(spec["uses"]).lower()
            tools += [t for prefix, t in SETUP_TOOLS.items() if u.startswith(prefix)]
            if u.startswith("pnpm/action-setup"):
                tools.insert(0, "node")
        for m in re.finditer(r"(?m)^[ \t]*choco install ([^\r\n;|&]+)", str(spec.get("run", ""))):
            tools += [CHOCO_TO_TOOL[p.lower()] for p in m.group(1).split() if p.lower() in CHOCO_TO_TOOL]
    return list(dict.fromkeys(tools))
