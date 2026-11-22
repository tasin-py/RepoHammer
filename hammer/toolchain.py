"""Locate build tools on this PC and install missing ones (winget / npm / corepack)."""
import os
import shutil
import subprocess
import sys

IS_WIN = os.name == "nt"

# key -> (executables to look for, winget id or None, extra winget args)
VS_OVERRIDE_MANAGED = ("--override", "--quiet --wait --norestart --nocache "
                       "--add Microsoft.VisualStudio.Workload.MSBuildTools "
                       "--add Microsoft.VisualStudio.Workload.ManagedDesktopBuildTools "
                       "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended")

TOOLS = {
    "git":     (["git"], "Git.Git", ()),
    "node":    (["node", "npm"], "OpenJS.NodeJS.LTS", ()),
    "pnpm":    (["pnpm"], None, ()),        # via corepack / npm
    "yarn":    (["yarn"], None, ()),        # via corepack / npm
    "bun":     (["bun"], "Oven-sh.Bun", ()),
    "deno":    (["deno"], "DenoLand.Deno", ()),
    "python":  (["python", "py"], "Python.Python.3.12", ()),
    "go":      (["go"], "GoLang.Go", ()),
    "cargo":   (["cargo"], "Rustlang.Rustup", ()),
    "dotnet":  (["dotnet"], "Microsoft.DotNet.SDK.8", ()),
    "java":    (["java"], "Microsoft.OpenJDK.21", ()),
    "mvn":     (["mvn"], "Apache.Maven", ()),
    "gradle":  (["gradle"], "Gradle.Gradle", ()),
    "cmake":   (["cmake"], "Kitware.CMake", ()),
    "ninja":   (["ninja"], "Ninja-build.Ninja", ()),
    "meson":   (["meson"], "mesonbuild.meson", ()),
    "make":    (["make", "mingw32-make"], "ezwinports.make", ()),
    "zig":     (["zig"], "zig.zig", ()),
    "msbuild": ([], "Microsoft.VisualStudio.2022.BuildTools", VS_OVERRIDE_MANAGED),
    "msvc":    ([], "Microsoft.VisualStudio.2022.BuildTools", VS_OVERRIDE_MANAGED),
    "nuget":   (["nuget"], "Microsoft.NuGet", ()),
    "flutter": (["flutter"], None, ()),
    "docker":  (["docker"], None, ()),
    "bash":    (["bash"], "Git.Git", ()),
    "iscc":    (["iscc"], "JRSoftware.InnoSetup", ()),
    "makensis": (["makensis"], "NSIS.NSIS", ()),
    "7z":      (["7z"], "7zip.7zip", ()),
    "wix":     (["wix", "candle"], "WiXToolset.WiXCLI", ()),
    "llvm":    (["clang"], "LLVM.LLVM", ()),
}

# What each tool is and roughly how much it downloads, for the "download these?" question.
ABOUT = {
    "git": ("Git", "60 MB"), "node": ("Node.js LTS", "30 MB"), "pnpm": ("pnpm", "10 MB"),
    "yarn": ("Yarn", "5 MB"), "bun": ("Bun", "40 MB"), "deno": ("Deno", "40 MB"),
    "python": ("Python", "30 MB"), "go": ("Go", "70 MB"), "cargo": ("Rust (rustup + stable toolchain)", "300 MB"),
    "dotnet": (".NET SDK", "220 MB"), "java": ("Microsoft OpenJDK 21", "190 MB"), "mvn": ("Apache Maven", "10 MB"),
    "gradle": ("Gradle", "130 MB"), "cmake": ("CMake", "35 MB"), "ninja": ("Ninja", "1 MB"),
    "meson": ("Meson", "20 MB"), "make": ("GNU Make", "1 MB"), "zig": ("Zig", "50 MB"),
    "msbuild": ("Visual Studio Build Tools (MSBuild, C++, .NET)", "3-6 GB"),
    "msvc": ("Visual Studio Build Tools (MSBuild, C++, .NET)", "3-6 GB"),
    "nuget": ("NuGet", "7 MB"), "iscc": ("Inno Setup", "6 MB"), "makensis": ("NSIS", "3 MB"),
    "7z": ("7-Zip", "2 MB"), "wix": ("WiX Toolset", "20 MB"), "llvm": ("LLVM / Clang", "400 MB"),
    "bash": ("Git Bash", "60 MB"), "flutter": ("Flutter", "1 GB"), "docker": ("Docker Desktop", "600 MB"),
}


def about(key):
    """(label, approximate size, how it's installed) for one tool."""
    label, size = ABOUT.get(key, (key, "?"))
    wid = TOOLS.get(key, ([], None, ()))[1]
    how = f"winget {wid}" if wid else ("npm" if key in ("pnpm", "yarn") else "manual install")
    return label, size, how


# Folders tools install into without always landing on PATH (globs; env vars expanded).
EXTRA_DIRS = {
    "iscc": [r"%LOCALAPPDATA%\Programs\Inno Setup*", r"%ProgramFiles(x86)%\Inno Setup*",
             r"%ProgramFiles%\Inno Setup*"],
    "makensis": [r"%ProgramFiles(x86)%\NSIS", r"%ProgramFiles%\NSIS"],
    "7z": [r"%ProgramFiles%\7-Zip", r"%ProgramFiles(x86)%\7-Zip"],
    "wix": [r"%ProgramFiles%\WiX Toolset*\bin", r"%ProgramFiles(x86)%\WiX Toolset*\bin"],
    "llvm": [r"%ProgramFiles%\LLVM\bin"],
    "cmake": [r"%ProgramFiles%\CMake\bin"],
    "git": [r"%ProgramFiles%\Git\cmd"],
    "bash": [r"%ProgramFiles%\Git\bin"],
    "node": [r"%ProgramFiles%\nodejs"],
    "go": [r"%ProgramFiles%\Go\bin"],
    "dotnet": [r"%ProgramFiles%\dotnet"],
    "cargo": [r"%USERPROFILE%\.cargo\bin"],
    "nuget": [r"%LOCALAPPDATA%\Microsoft\WinGet\Links"],
}


def refresh_path():
    """Reload PATH from the registry so tools installed a moment ago become visible."""
    if not IS_WIN:
        return
    import winreg
    parts = []
    for hive, sub in ((winreg.HKEY_LOCAL_MACHINE,
                       r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, r"Environment")):
        try:
            with winreg.OpenKey(hive, sub) as k:
                parts.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
        except OSError:
            pass
    extra = [os.path.expanduser(r"~\.cargo\bin"), os.path.expandvars(r"%APPDATA%\npm")]
    merged, seen = [], set()
    for p in ";".join(parts + [os.environ.get("PATH", "")] + extra).split(";"):
        if p and p.lower() not in seen:
            seen.add(p.lower())
            merged.append(p)
    os.environ["PATH"] = ";".join(merged)


def vswhere(*args):
    exe = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                       "Microsoft Visual Studio", "Installer", "vswhere.exe")
    if not os.path.isfile(exe):
        return ""
    try:
        out = subprocess.run([exe, "-latest", "-prerelease", "-products", "*", *args],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def find_msbuild():
    p = vswhere("-requires", "Microsoft.Component.MSBuild", "-find", r"MSBuild\**\Bin\MSBuild.exe")
    return p or shutil.which("msbuild") or ""


def has_msvc():
    return bool(vswhere("-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                        "-property", "installationPath")) or bool(shutil.which("cl"))


def find(key):
    """Return a path/command for the tool, or '' if it is not available."""
    if key == "msbuild":
        return find_msbuild()
    if key == "msvc":
        return "msvc" if has_msvc() else ""
    if key == "python":
        return sys.executable if not getattr(sys, "frozen", False) else (shutil.which("python") or "")
    exes = TOOLS.get(key, ([key], None, ()))[0]
    for exe in exes:
        p = shutil.which(exe)
        if p:
            return p
    import glob
    for pat in EXTRA_DIRS.get(key, []):
        for d in sorted(glob.glob(os.path.expandvars(pat)), reverse=True):
            for exe in exes:
                if os.path.isfile(os.path.join(d, exe + ".exe")):
                    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")  # visible to builds
                    return os.path.join(d, exe + ".exe")
    return ""


def ensure(key, log, allow_install=True):
    """Make sure a tool exists. Installs it automatically when allowed. Returns True if usable."""
    if find(key):
        return True
    if not allow_install:
        log(f"[toolchain] {key} is not installed and was not downloaded")
        return False
    log(f"[toolchain] {key} not found - installing it automatically...")
    ok = False
    if key in ("pnpm", "yarn"):
        if ensure("node", log, allow_install):
            ok = _run(["npm", "install", "-g", key], log) or _run(["corepack", "enable", key], log)
    else:
        exes, wid, extra = TOOLS.get(key, ([key], None, ()))
        if wid and shutil.which("winget"):
            ok = _run(["winget", "install", "--id", wid, "-e", "--silent",
                       "--accept-package-agreements", "--accept-source-agreements", *extra], log)
            if key == "cargo":
                refresh_path()
                _run(["rustup", "default", "stable"], log)
        elif not wid:
            log(f"[toolchain] no automatic installer known for {key}; install it once manually.")
    refresh_path()
    found = bool(find(key))
    log(f"[toolchain] {key}: {'ready' if found else 'still missing'}")
    if not found and ok:
        log(f"[toolchain] {key} installed but not on PATH yet; restarting RepoHammer may help.")
    return found


def _run(cmd, log):
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, text=True, errors="replace",
                             shell=IS_WIN and cmd[0] not in ("winget",))
        for line in p.stdout:
            line = line.rstrip()
            if line and not set(line) <= set(" -\\|/█▒"):  # skip spinner noise
                log("  " + line)
        return p.wait() == 0
    except OSError as e:
        log(f"  {e}")
        return False


def snapshot():
    """Status of every known tool, for the UI."""
    return {k: bool(find(k)) for k in TOOLS}
