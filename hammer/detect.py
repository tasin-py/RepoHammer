"""Work out how a project builds. Produces ordered build plans with fallback commands.

A plan step is a list of alternative commands: the first that succeeds wins.
Placeholders resolved at run time: {msbuild} {py} {venvpy}
"""
import fnmatch
import glob
import json
import os
import re
from dataclasses import dataclass, field

IS_WIN = os.name == "nt"
SKIP_DIRS = {".git", "node_modules", "vendor", "third_party", "thirdparty", "external", "deps",
             "build", "dist", "out", "target", "bin", "obj", ".venv", "venv", "test", "tests",
             "docs", "examples", "example", "samples", "fixtures", ".github"}


@dataclass
class Plan:
    name: str
    cwd: str
    steps: list
    tools: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    note: str = ""
    soft_tools: list = field(default_factory=list)  # try to provide, but don't fail without them
    runtime: object = None  # workflow state (gets the builder's log at run time)

    def describe(self):
        rel = self.note or ""
        return f"{self.name}  [{', '.join(self.tools) or 'no extra tools'}]  {rel}".strip()


def _has(d, *names):
    return any(os.path.exists(os.path.join(d, n)) for n in names)


def _read(path, limit=200_000):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def _q(p):
    return f'"{p}"'


# ---------------------------------------------------------------- repo scripts
def _script_tools(text):
    t = text.lower()
    tools = []
    for key, pats in {"pnpm": ["pnpm"], "yarn": ["yarn "], "node": ["npm ", "npx ", "node "],
                      "msbuild": ["msbuild", "vswhere"], "dotnet": ["dotnet "], "cmake": ["cmake"],
                      "cargo": ["cargo "], "go": ["go build"], "python": ["python", "pip "],
                      "java": ["java ", "mvn", "gradle"], "nuget": ["nuget "]}.items():
        if any(p in t for p in pats):
            tools.append(key)
    if "pnpm" in tools or "yarn" in tools:
        tools.insert(0, "node")
    return list(dict.fromkeys(tools))


def detect_scripts(d):
    plans = []
    cands = []
    if IS_WIN:
        cands += [("build.ps1", "powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1"),
                  ("build.cmd", "build.cmd"), ("build.bat", "build.bat"),
                  ("scripts/build.ps1", "powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build.ps1"),
                  ("make.bat", "make.bat")]
    cands += [("build.sh", "bash ./build.sh"), ("scripts/build.sh", "bash ./scripts/build.sh")]
    seen = False
    for f, cmd in cands:
        p = os.path.join(d, f)
        if os.path.isfile(p) and not seen:
            text = _read(p)
            if f.endswith(".cmd") and "build.ps1" in text.lower() and _has(d, "build.ps1"):
                continue  # wrapper around build.ps1, which we already planned
            tools = _script_tools(text) + (["bash"] if cmd.startswith("bash") else [])
            steps = []
            if "node" in tools:
                steps += _node_install_steps(d)
            hard = [t for t in tools if t in ("node", "pnpm", "yarn", "bash") and (t != "node" or steps)]
            plans.append(Plan(f"Project script ({f})", d, steps + [[cmd]], hard,
                              ["dist/**", "out/**", "release/**", "build/**/*.exe", "**/bin/Release/**"],
                              note="the repo's own build script",
                              soft_tools=[t for t in tools if t not in hard]))
            seen = True
    return plans


# ---------------------------------------------------------------- node
def _node_pm(d):
    pkg = {}
    try:
        pkg = json.loads(_read(os.path.join(d, "package.json")) or "{}")
    except ValueError:
        pass
    pm = (pkg.get("packageManager") or "").split("@")[0]
    if not pm:
        root = d
        for _ in range(3):  # lockfile may live at the workspace root
            if _has(root, "pnpm-lock.yaml", "pnpm-workspace.yaml"):
                pm = "pnpm"; break
            if _has(root, "yarn.lock"):
                pm = "yarn"; break
            if _has(root, "bun.lockb", "bun.lock"):
                pm = "bun"; break
            if _has(root, "package-lock.json"):
                pm = "npm"; break
            root = os.path.dirname(root)
    return pm or "npm", pkg


def _node_install_steps(d):
    steps = []
    for sub in [d] + [os.path.join(d, s) for s in os.listdir(d)
                      if os.path.isfile(os.path.join(d, s, "package.json")) and s not in SKIP_DIRS]:
        if not os.path.isfile(os.path.join(sub, "package.json")):
            continue
        pm, _ = _node_pm(sub)
        cd = f'cd /d {_q(sub)} && ' if IS_WIN else f'cd {_q(sub)} && '
        steps.append([cd + c for c in {
            "pnpm": ["pnpm install --frozen-lockfile", "pnpm install"],
            "yarn": ["yarn install --frozen-lockfile", "yarn install"],
            "bun": ["bun install"],
            "npm": ["npm ci", "npm install", "npm install --legacy-peer-deps"]}[pm]])
    return steps


def detect_node(d):
    if not _has(d, "package.json"):
        return []
    pm, pkg = _node_pm(d)
    scripts = pkg.get("scripts", {})
    run = {"npm": "npm run", "pnpm": "pnpm run", "yarn": "yarn run", "bun": "bun run"}[pm]
    steps = [_node_install_steps(d)[0]]
    tools = ["node"] + ([pm] if pm != "npm" else [])
    note = pm
    if _has(d, "src-tauri/Cargo.toml"):
        tools += ["cargo", "msvc"]
        steps.append([f"{run} tauri build", f"npx --yes @tauri-apps/cli build"])
        note += ", Tauri app"
        arts = ["src-tauri/target/release/bundle/**", "src-tauri/target/release/*.exe"]
    else:
        chain = [n for n in ("build", "dist", "package", "make", "compile", "build:prod") if n in scripts]
        if any(k in str(pkg.get("devDependencies", {})) + str(pkg.get("dependencies", {}))
               for k in ("electron-builder", "electron-forge", "@electron-forge")):
            note += ", Electron app"
            chain = [n for n in ("dist", "package", "make", "build:win", "electron:build", "build") if n in scripts] or chain
        if not chain:
            if pkg.get("main") or pkg.get("bin"):
                note += ", no build script (ready after install)"
            else:
                return []
        else:
            steps.append([f"{run} {c}" for c in chain])
        arts = ["dist/**", "build/**", "out/**", "release/**", ".next/standalone/**"]
    return [Plan(f"Node.js ({pm})", d, steps, tools, arts, note=note)]


# ---------------------------------------------------------------- python
def detect_python(d):
    meta = _has(d, "pyproject.toml", "setup.py", "setup.cfg")
    req = _has(d, "requirements.txt")
    if not (meta or req):
        return []
    venv = os.path.join(d, ".rf-venv")
    vpy = os.path.join(venv, "Scripts", "python.exe") if IS_WIN else os.path.join(venv, "bin", "python")
    steps = [[f'{{py}} -m venv {_q(venv)}'],
             [f'{_q(vpy)} -m pip install --upgrade pip wheel build pyinstaller']]
    plans = []
    if req:
        steps.append([f'{_q(vpy)} -m pip install -r requirements.txt'])
    entry = _python_entry(d)
    for spec in _find(d, "*.spec"):
        if "Analysis(" in _read(spec, 50_000):
            rel = os.path.relpath(spec, d)
            plans.append(Plan("Python app (PyInstaller spec)", d,
                              steps + [[f'{_q(vpy)} -m PyInstaller --noconfirm {_q(rel)}']],
                              ["python"], ["dist/**"], note=f"spec: {rel}"))
            break
    if meta:
        plans.append(Plan("Python package (wheel)", d,
                          steps + [[f'{_q(vpy)} -m build', f'{_q(vpy)} -m pip wheel . --no-deps -w dist']],
                          ["python"], ["dist/*.whl", "dist/*.tar.gz"], note="pip build"))
    if entry:
        install = [] if not meta else [[f'{_q(vpy)} -m pip install .', f'{_q(vpy)} -m pip install -e .']]
        src = _read(os.path.join(d, entry), 100_000)
        gui = re.search(r"^\s*(?:from|import)\s+(PySide\d|PyQt\d|tkinter|wx|kivy|customtkinter|flet)\b", src, re.M)
        flags = "--onefile" + (" --windowed" if gui else "")
        plans.append(Plan("Python app (PyInstaller exe)", d,
                          steps + install + [[f'{_q(vpy)} -m PyInstaller --noconfirm {flags} {entry}']],
                          ["python"], ["dist/*"], note=f"entry: {entry}" + (", GUI" if gui else "")))
    return plans


def _find(d, pattern, depth=2):
    out = []
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if x.lower() not in SKIP_DIRS and not x.startswith(".")]
        if root[len(d):].count(os.sep) >= depth:
            dirs[:] = []
        out += [os.path.join(root, f) for f in files if fnmatch.fnmatch(f, pattern)]
    return sorted(out)


def _python_entry(d):
    """The script that starts the app: README's 'python X.py', the repo-named file, common names."""
    files = [f for f in os.listdir(d) if f.endswith(".py") and os.path.isfile(os.path.join(d, f))]
    if not files:
        return None
    for n in os.listdir(d):
        if n.lower().startswith("readme"):
            for m in re.finditer(r"\bpython3?\s+([\w.-]+\.py)\b", _read(os.path.join(d, n))):
                if m.group(1) in files:
                    return m.group(1)
    name = re.sub(r"[^a-z0-9]", "", os.path.basename(d).split("__")[-1].lower())
    for f in files:
        if re.sub(r"[^a-z0-9]", "", f[:-3].lower()) == name:
            return f
    for e in ("main.py", "app.py", "run.py", "__main__.py", "gui.py", "cli.py", "start.py"):
        if e in files:
            return e
    mains = [f for f in files if "__name__" in _read(os.path.join(d, f)) and "__main__" in _read(os.path.join(d, f))]
    return mains[0] if len(mains) == 1 else None


# ---------------------------------------------------------------- compiled languages
def detect_rust(d):
    if not _has(d, "Cargo.toml") or os.path.basename(d) == "src-tauri":
        return []
    return [Plan("Rust (cargo)", d, [["cargo build --release --locked", "cargo build --release"]],
                 ["cargo", "msvc"] if IS_WIN else ["cargo"],
                 ["target/release/*.exe", "target/release/*.dll", "target/release/*.so", "target/release/*.dylib"])]


def detect_go(d):
    if not _has(d, "go.mod"):
        return []
    return [Plan("Go", d, [["go mod download"], ["go build -o bin/ ./...", "go build ./..."]],
                 ["go"], ["bin/*"])]


def _dotnet_sdk_version(d, projects):
    gj = os.path.join(d, "global.json")
    if os.path.isfile(gj):
        m = re.search(r'"version"\s*:\s*"(\d+)', _read(gj))
        if m:
            return int(m.group(1))
    best = 0
    for p in projects:
        for v in re.findall(r"net(\d+)\.\d", _read(p)):
            best = max(best, int(v))
    return best


def detect_dotnet(d):
    slns = sorted(glob.glob(os.path.join(d, "*.sln")) + glob.glob(os.path.join(d, "*.slnx")))
    projs = glob.glob(os.path.join(d, "*.csproj")) + glob.glob(os.path.join(d, "*.fsproj")) + \
        glob.glob(os.path.join(d, "*.vbproj")) + glob.glob(os.path.join(d, "*", "*.*proj"))
    projs = [p for p in projs if re.search(r"\.(cs|fs|vb|vcx)proj$", p)]
    if not slns and not projs:
        return []
    target = slns[0] if slns else projs[0]
    sdk_style = projs and all("<Project Sdk=" in _read(p, 4000) or "<Project Sdk =" in _read(p, 4000)
                              for p in projs if not p.endswith(".vcxproj"))
    has_cpp = any(p.endswith(".vcxproj") for p in projs)
    name = os.path.basename(target)
    arts = ["**/bin/Release/**", "**/bin/x64/Release/**", "x64/Release/*", "Release/*", "publish/**"]
    plans = []
    if sdk_style and not has_cpp:
        ver = _dotnet_sdk_version(d, projs)
        plans.append(Plan(".NET SDK (dotnet)", d,
                          [[f"dotnet restore {_q(name)}"], [f"dotnet build {_q(name)} -c Release"]],
                          ["dotnet"], arts, note=f"needs .NET {ver or '?'}",
                          env={"_dotnet_major": str(ver)}))
    nuget = [f'{{msbuild}} {_q(name)} -t:Restore -p:Configuration=Release -p:RestorePackagesConfig=true',
             f"nuget restore {_q(name)}"]
    plans.append(Plan("Visual Studio (MSBuild)", d,
                      [nuget, [f'{{msbuild}} {_q(name)} -p:Configuration=Release -m -nologo',
                               f'{{msbuild}} {_q(name)} -p:Configuration=Release -p:Platform="Any CPU" -m -nologo',
                               f'{{msbuild}} {_q(name)} -p:Configuration=Release -p:Platform=x64 -m -nologo']],
                      ["msbuild"] + (["msvc"] if has_cpp else []), arts,
                      note="classic .NET Framework / C++ solution" if not sdk_style else "MSBuild fallback"))
    return plans


def detect_java(d):
    if _has(d, "pom.xml"):
        mvn = "mvnw.cmd" if IS_WIN and _has(d, "mvnw.cmd") else ("./mvnw" if _has(d, "mvnw") else "mvn")
        tools = ["java"] + ([] if mvn != "mvn" else ["mvn"])
        return [Plan("Java (Maven)", d, [[f"{mvn} -B -DskipTests package"]], tools, ["**/target/*.jar"])]
    if _has(d, "build.gradle", "build.gradle.kts"):
        gw = "gradlew.bat" if IS_WIN and _has(d, "gradlew.bat") else ("./gradlew" if _has(d, "gradlew") else "gradle")
        tools = ["java"] + ([] if gw != "gradle" else ["gradle"])
        steps = [[f"{gw} assemble --no-daemon", f"{gw} build -x test --no-daemon"]]
        if _has(d, "app/src/main/AndroidManifest.xml"):
            steps = [[f"{gw} assembleRelease --no-daemon", f"{gw} assembleDebug --no-daemon"]]
        return [Plan("Java/Kotlin (Gradle)", d, steps, tools, ["**/build/libs/*.jar", "**/build/outputs/**/*.apk"])]
    return []


def detect_native(d):
    plans = []
    if _has(d, "CMakeLists.txt"):
        tools = ["cmake"] + (["msvc"] if IS_WIN else [])
        plans.append(Plan("C/C++ (CMake)", d,
                          [["cmake -S . -B build-rf -DCMAKE_BUILD_TYPE=Release"],
                           ["cmake --build build-rf --config Release --parallel"]], tools,
                          ["build-rf/Release/*", "build-rf/*.exe", "build-rf/bin/**", "build-rf/**/Release/*.exe"]))
    if _has(d, "meson.build"):
        plans.append(Plan("C/C++ (Meson)", d, [["meson setup build-rf --buildtype=release"],
                                               ["meson compile -C build-rf"]],
                          ["meson", "ninja"] + (["msvc"] if IS_WIN else []), ["build-rf/*.exe", "build-rf/*"]))
    if _has(d, "build.zig"):
        plans.append(Plan("Zig", d, [["zig build -Doptimize=ReleaseFast"]], ["zig"], ["zig-out/**"]))
    if _has(d, "Makefile", "makefile", "GNUmakefile") and not plans:
        plans.append(Plan("Make", d, [["make -j", "mingw32-make -j"]], ["make"], ["*.exe", "bin/*", "build/*"]))
    return plans


def detect_misc(d):
    plans = []
    pub = _read(os.path.join(d, "pubspec.yaml"))
    if "flutter:" in pub:
        plans.append(Plan("Flutter", d, [["flutter pub get"],
                                         ["flutter build windows" if IS_WIN else "flutter build linux"]],
                          ["flutter"], ["build/windows/**/Release/**", "build/linux/**/release/**"]))
    if _has(d, "deno.json", "deno.jsonc") and "build" in _read(os.path.join(d, "deno.json")):
        plans.append(Plan("Deno", d, [["deno task build"]], ["deno"], ["dist/**"]))
    if _has(d, "Dockerfile"):
        plans.append(Plan("Docker image", d, [[f"docker build -t rf-{os.path.basename(d).lower()} ."]],
                          ["docker"], [], note="last resort"))
    return plans


def detect_workflow(d):
    """The repo's own CI build job (GitHub Actions), replayed locally."""
    if not IS_WIN or not os.path.isdir(os.path.join(d, ".github", "workflows")):
        return []
    from . import workflow
    plans = []
    for score, f, jid, job, wf in workflow.find_jobs(d)[:2]:
        run, steps, skipped = workflow.build_steps(d, wf, job, lambda m: None)
        if not any(isinstance(st, workflow.Step) for st in steps):
            continue
        note = f"{os.path.basename(f)} -> job '{jid}'"
        if skipped:
            note += f"; skips CI-only: {', '.join(map(str, skipped))[:120]}"
        plans.append(Plan(f"GitHub workflow ({os.path.basename(f)}: {jid})", d, [[st] for st in steps],
                          workflow.required_tools(steps),
                          ["dist/**", "build/**/*.exe", "out/**", "release/**", "artifacts/**",
                           "target/release/*.exe", "**/bin/Release/**", "**/publish/**"],
                          note=note, runtime=run))
    return plans


DETECTORS = [detect_scripts, detect_workflow, detect_dotnet, detect_rust, detect_go, detect_java, detect_native,
             detect_node, detect_python, detect_misc]


def detect_dir(d):
    plans = []
    for fn in DETECTORS:
        try:
            plans += fn(d)
        except Exception as e:  # a broken manifest must not stop the whole scan
            plans.append(Plan(f"(detector {fn.__name__} failed: {e})", d, [], []))
    return [p for p in plans if p.steps]


def detect(root, max_depth=2):
    """Plans for the root, or for sub-projects if the root has none (monorepos)."""
    plans = detect_dir(root)
    if plans:
        return plans
    level = [root]
    for _ in range(max_depth):
        nxt = []
        for base in level:
            try:
                subs = sorted(e.path for e in os.scandir(base)
                              if e.is_dir() and e.name.lower() not in SKIP_DIRS and not e.name.startswith("."))
            except OSError:
                continue
            for s in subs:
                found = detect_dir(s)
                for p in found:
                    p.note = (p.note + " " if p.note else "") + f"in {os.path.relpath(s, root)}"
                plans += found
                if not found:
                    nxt.append(s)
        if plans:
            return plans
        level = nxt
    return plans
