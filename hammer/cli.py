"""Command line: python app.py build <repo> | detect <repo> | tools"""
import argparse
import sys

from . import APP_NAME, VERSION, build, detect, fetch, toolchain


def ask(items, context):
    """Terminal version of the download question."""
    print()
    print(f"RepoHammer needs to download {'this' if len(items) == 1 else 'these'}" +
          (f" ({context})" if context else "") + ":")
    for _id, label, size in items:
        print(f"  - {label}  (~{size})")
    print("  The project's own packages (npm, pip, NuGet...) are also downloaded while it builds.")
    if not sys.stdin or not sys.stdin.isatty():
        print("No terminal to answer in - not downloading. Use --yes to allow downloads.")
        return False
    try:
        return input("Download? [Y/n] ").strip().lower() in ("", "y", "yes")
    except EOFError:
        return False


def ask_retry(info):
    """Terminal version of the 'build failed - what now?' question."""
    print()
    print(f"{info['plan']} didn't work (attempt {info['attempt']}, took {info['secs']}s).")
    for r in info["reasons"][:4]:
        print(f"  - {r}")
    if info["fixes"]:
        print("  RepoHammer fixed: " + "; ".join(info["fixes"]) + ". Retrying should get further.")
    if not sys.stdin or not sys.stdin.isatty():
        return "retry" if (info["fixes"] and info["attempt"] == 1) else "next"
    rest = info["remaining"]
    if rest:
        print("  Other ways to build: " + ", ".join(f"[{i}] {n}" for i, n in enumerate(rest, 1)))
    prompt = "[R]etry, " + ("[T]ry the next way (or its number), " if rest else "") + "[S]top? "
    try:
        a = input(prompt).strip().lower()
    except EOFError:
        return "stop"
    if a in ("r", "retry"):
        return "retry"
    if rest and a.isdigit() and 1 <= int(a) <= len(rest):
        return ("plan", rest[int(a) - 1])
    if rest and a in ("", "t", "n", "next"):
        return "next"
    return "stop"


def ask_readme(cmds, fname):
    """Terminal version of 'run the README's commands?'. Returns the indices to run."""
    print()
    print(f"RepoHammer didn't recognise how this project builds. Its {fname} suggests:")
    for i, c in enumerate(cmds, 1):
        tag = {"risky": f"  [careful: {c['why']}]", "unix": "  [Linux/macOS]"}.get(c["kind"], "")
        print(f"  {i:>2}. {c['cmd']}{tag}")
    safe = [i for i, c in enumerate(cmds) if c["kind"] == "ok"]
    if not sys.stdin or not sys.stdin.isatty():
        print("No terminal to answer in - not running them.")
        return []
    print("  Only the commands you pick run, in order, inside the project folder.")
    try:
        a = input(f"Run which? [Enter] = the {len(safe)} unmarked ones, numbers like 1,3, or N for none: ")
    except EOFError:
        return []
    a = a.strip().lower()
    if a == "":
        return safe
    if a in ("n", "no", "none"):
        return []
    picks = [int(x) - 1 for x in a.replace(",", " ").split() if x.isdigit() and 1 <= int(x) <= len(cmds)]
    return sorted(dict.fromkeys(picks))


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="repohammer", description=f"{APP_NAME} {VERSION} - build any repo locally")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="clone and build a repository")
    b.add_argument("repo")
    b.add_argument("--yes", "-y", action="store_true", help="download needed tools without asking")
    b.add_argument("--no-install", action="store_true", help="never download tools (answer No)")
    b.add_argument("--fresh", action="store_true", help="delete any previous clone first")
    b.add_argument("--no-ask", action="store_true",
                   help="don't ask questions during the build: after a failure retry once if a fix was "
                        "applied, then try the next way; never run README commands")
    b.add_argument("--plan", help="force a specific strategy by name")
    b.add_argument("--workdir", default=build.DEFAULT_WORKDIR)
    d = sub.add_parser("detect", help="show how a repository would be built")
    d.add_argument("repo")
    d.add_argument("--workdir", default=build.DEFAULT_WORKDIR)
    sub.add_parser("tools", help="list build tools found on this PC")
    a = ap.parse_args(argv)

    if a.cmd == "tools":
        for k, ok in toolchain.snapshot().items():
            print(f"  {'OK ' if ok else '-- '} {k}")
        return 0
    if a.cmd == "detect":
        _info, src = fetch.fetch(a.repo, a.workdir, print)
        plans = detect.detect(src)
        for p in plans:
            print(p.describe())
            for i, s in enumerate(p.steps, 1):
                print(f"   {i}. " + "  ||  ".join(getattr(a, "label", None) or str(a) for a in s)
                      + ("  (optional)" if any(getattr(a, "soft", False) for a in s) else ""))
        return 0 if plans else 1
    confirm = None if (a.yes or a.no_install) else ask
    res = build.Builder(a.workdir, auto_install=a.yes, confirm=confirm,
                        on_fail=None if a.no_ask else ask_retry,
                        on_readme=None if a.no_ask else ask_readme).run(a.repo, a.fresh, a.plan)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
