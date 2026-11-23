"""Find the build commands a repository's README suggests, and label the ones to be careful with.

Nothing here runs anything: the user picks which commands run (see Builder._readme_build)."""
import os
import re

MAX_COMMANDS = 12
SHELL_LANGS = {"", "sh", "bash", "shell", "zsh", "console", "terminal", "powershell", "ps", "ps1", "pwsh",
               "cmd", "bat", "batch"}
TOPIC = re.compile(r"\b(build|install|compil|setup|set up|getting started|from source|develop|usage|run)", re.I)
WORDS = re.compile(r"\b(build|make|install|compile|pack|publish|cargo|dotnet|npm|pnpm|yarn|pip|python|go|"
                   r"gradle|mvn|cmake|meson|msbuild|setup\.py|pyinstaller)\b", re.I)
PROMPT = re.compile(r"^\s*(?:\$|>|PS(?: [^>]*)?>|[A-Za-z]:\\[^>]*>)\s+")

# (pattern, why) - these change the PC or run code from the internet: never ticked by default
RISKY = [
    (r"\bsudo\b", "asks for admin rights"),
    (r"\b(curl|wget|iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b.*\|", "runs a script straight from the internet"),
    (r"\b(iex|Invoke-Expression)\b", "runs a script straight from the internet"),
    (r"\|\s*(ba|z)?sh\b", "runs a script straight from the internet"),
    (r"\brm\s+-\w*[rf]", "deletes files"),
    (r"\b(del|erase|rd|rmdir)\b.*\s/s\b", "deletes files"),
    (r"\bRemove-Item\b", "deletes files"),
    (r"\bformat\s+[a-z]:", "formats a drive"),
    (r"\breg\s+(add|delete|import)\b", "changes the Windows registry"),
    (r"\bSet-ExecutionPolicy\b", "changes a Windows security setting"),
    (r"\b(chmod|chown)\b", "changes file permissions"),
    (r"\b(npm|pnpm|yarn)\s+(i|install|add)\b.*\s(-g|--global)\b", "installs a program for the whole PC"),
]
UNIX = re.compile(r"^\s*(apt|apt-get|yum|dnf|pacman|zypper|apk|brew|port|snap|pkg|xcode-select|"
                  r"export|source|ln|open|ls|cat|cp|mv|touch|nix|nix-shell)\b|^\s*\./|^\s*\.\s", re.I)
SKIP = re.compile(r"^\s*(git\s+clone|#|rem\b|::|//)", re.I)

# first word of a command -> toolchain key, so a missing tool can be offered for download first
TOOL_FOR = {"npm": "node", "npx": "node", "node": "node", "pnpm": "pnpm", "yarn": "yarn", "bun": "bun",
            "deno": "deno", "cargo": "cargo", "rustup": "cargo", "go": "go", "dotnet": "dotnet",
            "msbuild": "msbuild", "nuget": "nuget", "mvn": "mvn", "gradle": "gradle", "cmake": "cmake",
            "ninja": "ninja", "meson": "meson", "make": "make", "mingw32-make": "make", "zig": "zig",
            "python": "python", "python3": "python", "py": "python", "pip": "python", "pip3": "python",
            "git": "git", "iscc": "iscc", "makensis": "makensis", "flutter": "flutter"}


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _find_readme(src):
    names = sorted((n for n in os.listdir(src) if n.lower().startswith("readme")),
                   key=lambda n: (not n.lower().endswith(".md"), len(n)))
    return names[0] if names else None


def _lines(block, lang):
    """Commands in one code block: prompts removed, continuations joined, comments and output dropped."""
    raw = block.splitlines()
    prompted = any(PROMPT.match(l) for l in raw)
    out, cur = [], ""
    for line in raw:
        if prompted:
            if PROMPT.match(line):
                line = PROMPT.sub("", line, count=1)
            elif not cur and not (tool_for(line) or UNIX.search(line)):
                continue  # command output, not a command
        line = line.rstrip()
        if not line.strip() and not cur:
            continue
        if line.endswith(("\\", "`", "^")) and not line.endswith("\\\\"):
            cur += line[:-1].rstrip() + " "
            continue
        cur += line.strip()
        out.append(cur.strip())
        cur = ""
    if cur.strip():
        out.append(cur.strip())
    return out


def classify(cmd, lang):
    """(kind, why): kind is 'ok', 'unix' (Linux/macOS only) or 'risky'."""
    for pat, why in RISKY:
        if re.search(pat, cmd, re.I):
            return "risky", why
    if UNIX.search(cmd):
        return "unix", "Linux/macOS command"
    return "ok", ""


def shell_for(cmd, lang, kind):
    if lang in ("powershell", "ps", "ps1", "pwsh") or re.search(r"\$env:|\b[A-Z][a-z]+-[A-Z][a-zA-Z]+\b|\.ps1\b", cmd):
        return "powershell"
    if kind == "unix" and lang in ("sh", "bash", "shell", "zsh", "console", "terminal", ""):
        return "bash"
    return "cmd"


def commands(src):
    """(readme file name, [command dicts]) or None. Each dict: cmd, lang, kind, why, shell."""
    name = _find_readme(src)
    if not name:
        return None
    text = _read(os.path.join(src, name))
    found, seen = [], set()
    heading = ""
    pos = 0
    for m in re.finditer(r"^```[ \t]*([\w+-]*)[^\n]*\n(.*?)^```", text, re.S | re.M):
        for h in re.findall(r"^#{1,6}\s*(.+)$", text[pos:m.start()], re.M):
            heading = h
        pos = m.end()
        lang = m.group(1).lower()
        if lang not in SHELL_LANGS:
            continue
        body = m.group(2)
        if not (WORDS.search(body) or TOPIC.search(heading)):
            continue
        for cmd in _lines(body, lang):
            if SKIP.match(cmd) or cmd in seen or len(cmd) > 300:
                continue
            seen.add(cmd)
            kind, why = classify(cmd, lang)
            found.append({"cmd": cmd, "lang": lang, "kind": kind, "why": why,
                          "shell": shell_for(cmd, lang, kind)})
            if len(found) >= MAX_COMMANDS:
                return name, found
    return (name, found) if found else None


def tool_for(cmd):
    first = re.split(r"\s+", cmd.strip(), 1)[0].lower()
    first = os.path.basename(first)
    if first.endswith(".exe"):
        first = first[:-4]
    return TOOL_FOR.get(first)
