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
    res = build.Builder(a.workdir, auto_install=a.yes, confirm=confirm).run(a.repo, a.fresh, a.plan)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
