"""Self-healing: recognise common build failures in the output, fix the cause, let the plan retry.

Each healer gets (text, builder) and returns a short description of what it fixed, or None.
Fixes that change the environment are stored in builder.heal_env and applied to later commands.
"""
import io
import os
import re
import subprocess
import urllib.request
import zipfile

from . import toolchain


def _netfx_refs(text, b):
    """MSB3644: .NET Framework targeting pack missing -> download Microsoft's reference assemblies."""
    vers = set(re.findall(r"\.NETFramework,Version=v(\d)\.(\d)(?:\.(\d))?", text))
    if not vers or "MSB3644" not in text:
        return None
    root = os.path.join(b.workdir, "tools", "netfx-refs")
    done = []
    for v in sorted(vers):
        tfm = "net" + "".join(x for x in v if x)          # 4.8 -> net48, 4.7.2 -> net472
        pkg = f"Microsoft.NETFramework.ReferenceAssemblies.{tfm}"
        dest = os.path.join(root, tfm)
        if not os.path.isdir(os.path.join(dest, "build")):
            if not b.allow([(pkg, f".NET Framework {'.'.join(x for x in v if x)} reference assemblies (nuget.org)",
                             "5 MB")], "the project needs them to compile"):
                return None
            b.log(f"[heal] downloading {pkg} from nuget.org")
            url = f"https://www.nuget.org/api/v2/package/{pkg}"
            req = urllib.request.Request(url, headers={"User-Agent": "RepoHammer"})
            with urllib.request.urlopen(req, timeout=180) as r:
                data = r.read()
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                z.extractall(dest)
        merged = os.path.join(root, "merged", ".NETFramework")
        src = os.path.join(dest, "build", ".NETFramework")
        if os.path.isdir(src):  # merge every version into one root MSBuild can point at
            for name in os.listdir(src):
                tgt = os.path.join(merged, name)
                if not os.path.isdir(tgt):
                    import shutil
                    shutil.copytree(os.path.join(src, name), tgt)
        done.append(tfm)
    b.heal_env["TargetFrameworkRootPath"] = os.path.join(root, "merged")
    return f".NET Framework reference assemblies ({', '.join(done)}) provided"


def _dotnet_sdk(text, b):
    """NETSDK1045: the project needs a newer .NET SDK."""
    m = re.search(r"NETSDK1045.*?\.NET (\d+)\.\d", text)
    if not m:
        return None
    major = m.group(1)
    if not b.allow([(f"dotnet-sdk-{major}", f".NET {major} SDK", "220 MB")], "the project needs a newer .NET SDK"):
        return None
    if toolchain._run(["winget", "install", "--id", f"Microsoft.DotNet.SDK.{major}", "-e", "--silent",
                       "--accept-package-agreements", "--accept-source-agreements"], b.log):
        toolchain.refresh_path()
        return f".NET {major} SDK installed"
    return None


def _rustup_default(text, b):
    if "rustup could not choose a version" in text or "no default toolchain" in text.lower():
        if b.allow([("rust-stable", "Rust stable toolchain", "300 MB")], "no Rust toolchain is selected") and toolchain._run(["rustup", "default", "stable"], b.log):
            return "Rust stable toolchain selected"
    return None


def _git_safe_dir(text, b):
    m = re.search(r"detected dubious ownership in repository at '([^']+)'", text)
    if m:
        subprocess.run(["git", "config", "--global", "--add", "safe.directory", m.group(1)])
        return "git safe.directory added"
    return None


def _long_paths(text, b):
    if "Filename too long" in text or "path too long" in text.lower():
        subprocess.run(["git", "config", "--global", "core.longpaths", "true"])
        return "git long paths enabled"
    return None


def _node_openssl(text, b):
    if "ERR_OSSL_EVP_UNSUPPORTED" in text and "--openssl-legacy-provider" not in b.heal_env.get("NODE_OPTIONS", ""):
        b.heal_env["NODE_OPTIONS"] = (b.heal_env.get("NODE_OPTIONS", "") + " --openssl-legacy-provider").strip()
        return "legacy OpenSSL enabled for old webpack"
    return None


def _missing_tool(text, b):
    """'X is not recognized as an internal or external command' -> install X if we know how."""
    fixed = []
    for name in set(re.findall(r"'([\w.-]+)' is not recognized as an internal or external command", text) +
                    re.findall(r"Required command not found in PATH: ([\w.-]+)", text)):
        key = next((k for k, (exes, _w, _x) in toolchain.TOOLS.items() if name.lower() in exes), None)
        if key and not toolchain.find(key) and b.need(key, "the build script called it"):
            fixed.append(key)
    return f"installed {', '.join(fixed)}" if fixed else None


DIAGNOSES = [
    (r"error CS2001: Source file '([^']+)' could not be found",
     "The source code is incomplete: the project expects {n} file(s) that aren't in the download "
     "(e.g. {first}). From a zip, try a git clone; if it's already a clone, the authors haven't "
     "published those files and the project can't be built from its public source."),
    (r"error MSB4126: The specified solution configuration \"([^\"]+)\" is invalid", None),
    (r"(?:EPERM|EACCES).*?'([^']+)'", "Permission denied writing {first}. Try a workspace folder you own."),
    (r"(?:Step failed: (Test[^\n]*)|Tests?\s+\d+ failed)",
     "The project's own tests failed, so its build script stopped (the code itself may still compile)."),
    (r"(?:No space left on device|There is not enough space on the disk)", "The disk is full."),
    (r"Could not resolve host|getaddrinfo ENOTFOUND|Unable to load the service index",
     "Network problem while downloading dependencies. Check the internet connection and retry."),
]


def diagnose(text):
    """Plain-language reasons for failures that can't be healed."""
    out = []
    for pat, msg in DIAGNOSES:
        hits = list(dict.fromkeys(m.group(1) if m.groups() else m.group(0) for m in re.finditer(pat, text)))
        if hits and msg:
            out.append(msg.format(n=len(hits), first=os.path.basename(hits[0]) if hits[0] else ""))
    return out


HEALERS = [_netfx_refs, _dotnet_sdk, _rustup_default, _git_safe_dir, _long_paths, _node_openssl, _missing_tool]


def heal(text, builder):
    """Try every healer; return list of fixes applied (empty if nothing could be done)."""
    fixes = []
    for h in HEALERS:
        try:
            r = h(text, builder)
        except Exception as e:
            builder.log(f"[heal] {h.__name__} could not apply its fix: {e}")
            r = None
        if r:
            builder.log(f"[heal] fixed: {r}")
            fixes.append(r)
    return fixes
