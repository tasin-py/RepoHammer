"""Run build plans on this PC: ensure tools, execute steps with fallbacks, collect artifacts."""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

from . import detect, fetch, heal, toolchain

IS_WIN = os.name == "nt"
DEFAULT_WORKDIR = os.path.join(os.path.expanduser("~"), "RepoHammer")
JUNK_EXT = {".obj", ".o", ".d", ".rlib", ".rmeta", ".tlog", ".cache", ".ilk", ".exp", ".iobj",
            ".ipdb", ".pch", ".idb", ".lastbuildstate", ".fingerprint", ".map"}
OUTPUT_EXT = {".exe", ".dll", ".msi", ".msix", ".appx", ".jar", ".war", ".whl", ".apk", ".aab",
              ".zip", ".7z", ".nupkg", ".vsix", ".so", ".dylib", ".appimage", ".deb", ".dmg"}
NO_SCAN = {".git", "node_modules", ".rf-venv", "obj", ".gradle", "build-rf", "target"}


class Cancelled(Exception):
    pass


class Builder:
    def __init__(self, workdir=DEFAULT_WORKDIR, log=print, auto_install=True, on_event=None, confirm=None):
        self.workdir = os.path.normpath(workdir)
        # confirm(items, context) -> bool asks the user before anything is downloaded.
        # items: [(id, label, approx size)]. Without it, auto_install decides.
        self._confirm = confirm
        self._decided = {}          # id -> True/False, so each download is asked about once
        self.declined = []
        self._on_event = on_event   # optional callback(kind, *data) for progress / time estimates
        self.auto_install = auto_install
        self._log = log
        self._proc = None
        self._cancel = threading.Event()
        self._file = None
        toolchain.refresh_path()    # see tools installed since this process started
        self.heal_env = {}          # environment fixes discovered by the healer
        self._tail = []             # recent output lines, for diagnosing failures
        os.makedirs(workdir, exist_ok=True)

    # ------------------------------------------------------------ logging
    def log(self, msg):
        try:
            self._log(msg)
        except Exception:  # a console that can't show a character must never stop a build
            try:
                self._log(msg.encode("ascii", "replace").decode())
            except Exception:
                pass
        if self._file:
            self._file.write(msg + "\n")

    def event(self, kind, *data):
        if self._on_event:
            try:
                self._on_event(kind, *data)
            except Exception:
                pass

    # ------------------------------------------------------------ downloads need permission
    def allow(self, items, context=""):
        """Ask once about downloading these items [(id, label, size)]. True if all may be downloaded."""
        new = [it for it in items if it[0] not in self._decided]
        if new:
            if self._confirm:
                ok = bool(self._confirm(new, context))
            else:
                ok = self.auto_install
            for it in new:
                self._decided[it[0]] = ok
            labels = ", ".join(f"{it[1]} (~{it[2]})" for it in new)
            self.log(f"[download] {'approved' if ok else 'declined'}: {labels}")
            if not ok:
                self.declined += [it[1] for it in new]
        return all(self._decided[it[0]] for it in items)

    def missing(self, keys):
        """Tools from this list that aren't on the PC, as download items (deduplicated by installer)."""
        out, seen = [], set()
        for k in dict.fromkeys(keys):
            if toolchain.find(k):
                continue
            label, size, how = toolchain.about(k)
            if how in seen:
                continue
            seen.add(how)
            out.append((k, label, size))
        return out

    def need(self, key, context=""):
        """Make a tool available, asking before it is downloaded."""
        if toolchain.find(key):
            return True
        items = self.missing([key])
        allowed = self.allow(items, context) if items else True
        if key == "msvc" and self._decided.get("msbuild"):
            allowed = True  # same installer as MSBuild, already approved
        return toolchain.ensure(key, self.log, allowed)

    def cancel(self):
        self._cancel.set()
        p = self._proc
        if p and p.poll() is None:
            if IS_WIN:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
            else:
                p.kill()

    # ------------------------------------------------------------ main entry
    def run(self, spec, fresh=False, only=None, plans=None):
        """Fetch + build. Returns a result dict."""
        self._cancel.clear()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        os.makedirs(os.path.join(self.workdir, "logs"), exist_ok=True)
        result = {"spec": spec, "ok": False, "time": stamp, "artifacts": [], "output": "", "built": [],
                  "log": ""}
        t0 = time.time()
        try:
            remote = not os.path.isdir(spec.strip().strip('"'))
            git_ok = (not remote) or bool(toolchain.find("git")) or                 self.allow(self.missing(["git"]), "to download the repository (otherwise a zip copy is used)")
            info, src = fetch.fetch(spec, self.workdir, self.log, fresh, git_ok)
            name = f"{info['owner']}__{info['repo']}"
            result["log"] = os.path.join(self.workdir, "logs", f"{name}-{stamp}.log")
            self._file = open(result["log"], "w", encoding="utf-8")
            self._file.write(f"RepoHammer build of {spec} at {stamp}\n")
            plans = plans if plans is not None else detect.detect(src)
            if only:
                plans = [p for p in plans if p.name == only] or plans
            if not plans:
                self.log("[detect] could not recognise any build system in this repository.")
                self._readme_hint(src)
                return result
            groups = {}
            for p in plans:
                groups.setdefault(p.cwd, []).append(p)
            self.log(f"[detect] {len(plans)} plan(s) in {len(groups)} project folder(s)")
            out_dir = os.path.join(self.workdir, "output", name, stamp)
            any_ok = False
            self._why = []
            for cwd, group in groups.items():
                for plan in group:
                    self._check()
                    started = time.time()
                    self.event("plan", plan.name)
                    self._tail = []
                    ok = self._run_plan(plan)
                    if not ok and not self._cancel.is_set():
                        fixes = heal.heal(chr(10).join(self._tail), self)
                        if fixes:
                            self.log(f"[heal] retrying {plan.name} after {len(fixes)} fix(es)")
                            self._tail = []
                            ok = self._run_plan(plan)
                    if not ok:
                        self._why += heal.diagnose(chr(10).join(self._tail))
                    if ok:
                        files = self._collect(plan, started, src, out_dir)
                        result["built"].append(plan.name)
                        result["artifacts"] += files
                        any_ok = True
                        break
                    self.log(f"[build] {plan.name} failed - trying the next strategy\n")
                else:
                    self.log(f"[build] every strategy failed for {os.path.relpath(cwd, src)}")
            result["ok"] = any_ok
            result["output"] = out_dir if result["artifacts"] else ""
            if any_ok:
                self.log(f"\n[done] BUILD SUCCEEDED - {len(result['artifacts'])} file(s) in {out_dir}"
                         if result["artifacts"] else
                         f"\n[done] BUILD SUCCEEDED - outputs are inside {src}")
                if not result["artifacts"]:
                    result["output"] = src
            else:
                if self.declined:
                    self._why.append("These downloads were declined, and the build needs them: "
                                     + ", ".join(dict.fromkeys(self.declined)) + ". Build again and press Y to allow them.")
                why = list(dict.fromkeys(self._why))
                self.log("\n[done] BUILD FAILED" + (":" if why else " - see the log above for the first error"))
                for w in why:
                    self.log("  >> " + w)
                result["why"] = why
        except Cancelled:
            self.log("\n[done] cancelled")
        except Exception as e:
            self.log(f"\n[error] {type(e).__name__}: {e}")
        finally:
            if self._file:
                self._file.close()
                self._file = None
            result["secs"] = round(time.time() - t0)
            self._save_history(result)
        return result

    # ------------------------------------------------------------ plans
    def _check(self):
        if self._cancel.is_set():
            raise Cancelled()

    def _run_plan(self, plan):
        self.log(f"\n=== {plan.name}  ({plan.note or os.path.basename(plan.cwd)}) ===")
        if plan.runtime is not None:
            plan.runtime.log = self.log
            plan.runtime.need = self.need
            plan.runtime.allow = self.allow
        # one question up front listing everything this strategy would download
        items = self.missing(plan.tools + plan.soft_tools)
        if items:
            self.allow(items, f"for the {plan.name} build")
        else:
            self.log("[download] nothing to install - every tool this strategy needs is already here")
        for t in plan.tools:
            self._check()
            if not self.need(t):
                self.log(f"[build] needs {toolchain.about(t)[0]}, which isn't installed - skipping this strategy")
                return False
        for t in plan.soft_tools:
            self._check()
            self.need(t)
        if plan.name.startswith(".NET SDK"):
            self._ensure_dotnet_sdk(int(plan.env.get("_dotnet_major") or 0))
        env = {k: v for k, v in plan.env.items() if not k.startswith("_")}
        for i, alternatives in enumerate(plan.steps, 1):
            self.event("step", i, len(plan.steps))
            ok = False
            soft = False
            for n, alt in enumerate(alternatives):
                self._check()
                if callable(alt):  # a workflow step: resolved now, so earlier outputs are known
                    soft = getattr(alt, "soft", False)
                    self.log(f"[step {i}/{len(plan.steps)}] {alt.label}" + ("  (optional)" if soft else ""))
                    try:
                        cmd = alt()
                    except Exception as e:
                        self.log(f"  could not prepare this step: {e}")
                        cmd = "exit /b 1"
                    step_env = {**env, **(alt.env() if hasattr(alt, "env") else {})}
                    ok = self._exec(cmd, plan.cwd, step_env)
                    if hasattr(alt, "after"):
                        alt.after()
                    if ok:
                        break
                    continue
                cmd = self._resolve(alt)
                self.log(f"[step {i}/{len(plan.steps)}] $ {cmd}")
                if self._exec(cmd, plan.cwd, env):
                    ok = True
                    break
                self.log("[step] failed, trying alternative..." if n < len(alternatives) - 1 else "")
            if not ok and soft:
                self.log("[step] optional step failed - continuing")
                continue
            if not ok:
                return False
        return True

    def _resolve(self, cmd):
        if "{msbuild}" in cmd:
            cmd = cmd.replace("{msbuild}", f'"{toolchain.find_msbuild() or "msbuild"}"')
        if "{py}" in cmd:
            py = toolchain.find("python") or "python"
            cmd = cmd.replace("{py}", f'"{py}"')
        return cmd

    def _ensure_dotnet_sdk(self, major):
        if not major:
            return
        try:
            out = subprocess.run(["dotnet", "--list-sdks"], capture_output=True, text=True).stdout
        except OSError:
            out = ""
        have = {int(m) for m in re.findall(r"^(\d+)\.", out, re.M)}
        if major in have or (have and max(have) >= major and major < 5):
            return
        self.log(f"[toolchain] project targets .NET {major}")
        if self.allow([(f"dotnet-sdk-{major}", f".NET {major} SDK", "220 MB")], "the project targets this .NET version")                 and shutil.which("winget"):
            toolchain._run(["winget", "install", "--id", f"Microsoft.DotNet.SDK.{major}", "-e", "--silent",
                            "--accept-package-agreements", "--accept-source-agreements"], self.log)

    def _exec(self, cmd, cwd, extra_env):
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "PIP_NO_INPUT": "1", "npm_config_yes": "true",
               "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0", "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
               "DOTNET_NOLOGO": "1", "PYTHONUTF8": "1", "FORCE_COLOR": "0", **self.heal_env, **extra_env}
        env.pop("PYTHONPATH", None)
        try:
            self._proc = subprocess.Popen(cmd, cwd=cwd, shell=True, env=env, stdin=subprocess.DEVNULL,
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          text=True, errors="replace", bufsize=1)
            ansi = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
            for line in self._proc.stdout:
                line = ansi.sub("", line.rstrip())
                self.log("  " + line)
                self._tail.append(line)
                if len(self._tail) > 4000:
                    del self._tail[:1000]
            code = self._proc.wait()
        except OSError as e:
            self.log(f"  {e}")
            return False
        finally:
            self._proc = None
        self._check()
        if code:
            self.log(f"  (exit code {code})")
        return code == 0

    # ------------------------------------------------------------ artifacts
    def _collect(self, plan, started, src, out_dir):
        found = set()
        for pat in plan.artifacts:
            for f in glob.glob(os.path.join(plan.cwd, pat), recursive=True):
                if os.path.isfile(f) and self._keep(f, started - 5):
                    found.add(os.path.normpath(f))
        if not found:  # fallback: any fresh binary/package anywhere in the project
            for root, dirs, files in os.walk(plan.cwd):
                dirs[:] = [d for d in dirs if d not in NO_SCAN or d == "target"]
                for n in files:
                    f = os.path.join(root, n)
                    if os.path.splitext(n)[1].lower() in OUTPUT_EXT and self._keep(f, started - 5):
                        found.add(f)
        if not found:
            self.log("[artifacts] build finished but produced no recognisable output files")
            return []
        copied = []
        for f in sorted(found):
            rel = os.path.relpath(f, src)
            dst = os.path.join(out_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(f, dst)
            copied.append(dst)
        self.log(f"[artifacts] collected {len(copied)} file(s) -> {out_dir}")
        for c in [c for c in copied if os.path.splitext(c)[1].lower() in OUTPUT_EXT][:15]:
            self.log("  * " + os.path.relpath(c, out_dir))
        return copied

    @staticmethod
    def _keep(f, since):
        parts = set(f.replace("\\", "/").split("/"))
        if parts & {"node_modules", ".git", ".rf-venv", "obj", "deps", "incremental", ".fingerprint"}:
            return False
        if os.path.splitext(f)[1].lower() in JUNK_EXT:
            return False
        try:
            return os.path.getmtime(f) >= since
        except OSError:
            return False

    def _readme_hint(self, src):
        for n in os.listdir(src):
            if n.lower().startswith("readme"):
                text = open(os.path.join(src, n), encoding="utf-8", errors="replace").read()
                blocks = re.findall(r"```(?:sh|bash|shell|console|powershell|cmd)?\n(.*?)```", text, re.S)
                cmds = [b.strip() for b in blocks if re.search(r"\b(build|make|install|compile)\b", b)][:4]
                if cmds:
                    self.log("[hint] the README mentions these build commands:")
                    for c in cmds:
                        self.log("    " + c.replace("\n", "\n    "))
                break

    def _save_history(self, result):
        path = os.path.join(self.workdir, "history.json")
        try:
            hist = json.load(open(path, encoding="utf-8")) if os.path.isfile(path) else []
        except ValueError:
            hist = []
        entry = {k: result[k] for k in ("spec", "ok", "time", "output", "built", "log")}
        entry["count"] = len(result["artifacts"])
        entry["secs"] = result.get("secs", 0)
        hist = [entry] + hist[:49]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(hist, f, indent=1)


def history(workdir=DEFAULT_WORKDIR):
    try:
        return json.load(open(os.path.join(workdir, "history.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return []


def clear_history(workdir=DEFAULT_WORKDIR):
    """Forget past builds (their logs and output folders are kept on disk)."""
    path = os.path.join(workdir, "history.json")
    if os.path.isfile(path):
        os.remove(path)
