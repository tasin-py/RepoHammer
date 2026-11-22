"""RepoHammer desktop UI - minimal, Windows XP (Luna) look. Tkinter only: tiny footprint."""
import json
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from . import (APP_NAME, APP_REPO_URL, AUTHOR, AUTHOR_GITHUB, COFFEE_URL, DESCRIPTION, LICENSE,
               VERSION, build, detect, fetch, toolchain, updater)

SETTINGS = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RepoHammer", "settings.json")

# Windows XP "Luna" palette
FACE = "#ECE9D8"      # window / dialog background
WHITE = "#FFFFFF"
TEXT = "#000000"
GREY = "#6D6D6D"      # secondary text (still readable on FACE)
BORDER = "#7F9DB9"    # entry / list border
BTN_EDGE = "#003C74"  # button outline
GROUP = "#0046D5"     # group box title
SELECT = "#316AC5"
GREEN = "#2FB72F"
FONT = ("Tahoma", 9)
FONT_B = ("Tahoma", 9, "bold")
MONO = ("Lucida Console", 9)
MAX_LOG_LINES = 6000

# typical durations (seconds) when a repo has never been built here before
TYPICAL = {"Go": 60, "Rust": 300, "Node": 150, "Python app": 240, "Python package": 90, ".NET": 120,
           "Visual Studio": 180, "Java": 240, "C/C++": 300, "GitHub workflow": 420, "Project script": 300,
           "Flutter": 400, "Zig": 120, "Make": 180, "Deno": 60, "Docker": 600}


def load_settings():
    old = SETTINGS.replace("RepoHammer", "RepoForge")  # carry settings over from the old name
    if not os.path.isfile(SETTINGS) and os.path.isfile(old):
        try:
            os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
            import shutil
            shutil.copy2(old, SETTINGS)
        except OSError:
            pass
    try:
        s = json.load(open(SETTINGS, encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    old_work = os.path.join(os.path.expanduser("~"), "RepoForge")
    if "workdir" not in s and os.path.isdir(old_work) and not os.path.isdir(build.DEFAULT_WORKDIR):
        s["workdir"] = old_work  # keep earlier clones and history visible
    return s


def save_settings(s):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    json.dump(s, open(SETTINGS, "w", encoding="utf-8"), indent=1)


def open_path(p):
    if p and os.path.exists(p):
        os.startfile(p)


ASSETS = os.path.join(updater.APP_DIR, "assets")


def play_done_sound():
    """Short two-note chime when a build succeeds (instead of the Windows error beep)."""
    wav = os.path.join(ASSETS, "done.wav")
    try:
        import winsound
        winsound.PlaySound(wav, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass  # no sound is better than the wrong sound


def fmt_secs(s):
    s = max(0, int(s))
    return f"{s // 60:02d}:{s % 60:02d}" if s < 3600 else f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _check_image(master, checked, disabled=False):
    """13x13 XP checkbox: blue-edged white box, green tick when checked."""
    img = tk.PhotoImage(master=master, width=13, height=13)
    edge = "#B5B2A6" if disabled else "#1C5180"
    img.put(edge, to=(0, 0, 13, 13))
    img.put("#F4F3EE" if disabled else WHITE, to=(1, 1, 12, 12))
    if checked:
        tick = "#A8A8A8" if disabled else "#21A121"
        pts = [(x, x + 2) for x in (3, 4, 5)] + [(x, 12 - x) for x in (6, 7, 8, 9)]
        for x, y in pts:
            img.put(tick, to=(x, y, x + 1, y + 3))
    return img


class DownloadDialog(tk.Toplevel):
    """'These need to be downloaded - go ahead?' Y = yes, N / Esc / close = no."""

    def __init__(self, master, items, context):
        super().__init__(master)
        self.result = False
        self.title("Downloads needed")
        self.configure(bg=FACE)
        self.resizable(False, False)
        self.transient(master)
        body = ttk.Frame(self, padding=(16, 14, 16, 12))
        body.pack(fill="both", expand=True)
        head = "RepoHammer needs to download " + ("this" if len(items) == 1 else "these") + \
            (f" {context}" if context else "") + ":"
        ttk.Label(body, text=head, wraplength=460, justify="left", font=FONT_B).pack(anchor="w")
        box = tk.Frame(body, bg=BORDER)
        box.pack(fill="x", pady=(8, 6))
        lst = tk.Frame(box, bg=WHITE)
        lst.pack(fill="x", padx=1, pady=1)
        for _id, label, size in items:
            row = tk.Frame(lst, bg=WHITE)
            row.pack(fill="x", padx=8, pady=2)
            tk.Label(row, text="•  " + label, bg=WHITE, fg=TEXT, font=FONT, anchor="w").pack(side="left")
            tk.Label(row, text="~" + size, bg=WHITE, fg=GREY, font=FONT).pack(side="right")
        for line in ("The project's own packages (npm, pip, NuGet…) also download while it builds.",
                     "If you say No, RepoHammer tries other ways to build that don't need these."):
            ttk.Label(body, text=line, style="Grey.TLabel").pack(anchor="w")
        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(12, 0))
        no = ttk.Button(btns, text="No", width=10, command=self._no, underline=0)
        no.pack(side="right")
        yes = ttk.Button(btns, text="Yes, download", width=15, style="Default.TButton", command=self._yes,
                         underline=0)
        yes.pack(side="right", padx=(0, 6))
        ttk.Label(btns, text="Press Y or N", style="Grey.TLabel").pack(side="left")
        for k in ("y", "Y", "<Return>"):
            self.bind(k if k.startswith("<") else f"<Key-{k}>", lambda _e: self._yes())
        for k in ("n", "N", "<Escape>"):
            self.bind(k if k.startswith("<") else f"<Key-{k}>", lambda _e: self._no())
        self.protocol("WM_DELETE_WINDOW", self._no)
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        yes.focus_set()
        self.grab_set()
        self.wait_window()

    def _yes(self):
        self.result = True
        self.destroy()

    def _no(self):
        self.result = False
        self.destroy()


class RetryDialog(tk.Toplevel):
    """'This strategy failed - retry, try another way, or stop?'  R / T / S (Esc = stop)."""

    def __init__(self, master, info):
        super().__init__(master)
        self.result = "stop"
        remaining = info["remaining"]
        self.title("Build failed")
        self.configure(bg=FACE)
        self.resizable(False, False)
        self.transient(master)
        body = ttk.Frame(self, padding=(16, 14, 16, 12))
        body.pack(fill="both", expand=True)
        took = fmt_secs(info["secs"]) if info["secs"] >= 1 else "under a second"
        head = f"{info['plan']} didn't work (attempt {info['attempt']}, took {took})."
        ttk.Label(body, text=head, wraplength=470, justify="left", font=FONT_B).pack(anchor="w")

        box = tk.Frame(body, bg=BORDER)
        box.pack(fill="x", pady=(8, 6))
        lst = tk.Frame(box, bg=WHITE)
        lst.pack(fill="x", padx=1, pady=1)
        reasons = info["reasons"] or ["No known cause was recognised. The first error in the log shows what went wrong."]
        for r in reasons[:4]:
            tk.Label(lst, text="•  " + r, bg=WHITE, fg=TEXT, font=FONT, anchor="w", justify="left",
                     wraplength=440).pack(fill="x", padx=8, pady=2)
        if info["fixes"]:
            note = "RepoHammer fixed: " + "; ".join(info["fixes"]) + ". Retrying should get further."
        else:
            note = ("Nothing was changed, so a retry will probably fail the same way, "
                    "unless you fixed something yourself (for example, closed a program or reconnected).")
        ttk.Label(body, text=note, style="Grey.TLabel", wraplength=470, justify="left").pack(anchor="w")
        ttk.Label(body, text="A retry runs this strategy again; files already built are usually reused, "
                  "so it's often faster.", style="Grey.TLabel", wraplength=470, justify="left").pack(anchor="w")

        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(12, 0))
        retry = ttk.Button(btns, text="Retry", width=10, command=lambda: self._pick("retry"), underline=0)
        retry.pack(side="left")
        other = None
        if remaining:
            self.choice = ttk.Combobox(btns, state="readonly", width=28, values=remaining)
            self.choice.current(0)
            other = ttk.Button(btns, text="Try another way:", width=16, underline=0,
                               command=lambda: self._pick(("plan", self.choice.get())))
            other.pack(side="left", padx=(6, 4))
            self.choice.pack(side="left")
        stop = ttk.Button(btns, text="Stop", width=9, command=lambda: self._pick("stop"), underline=0)
        stop.pack(side="right")
        default = retry if (info["fixes"] or not other) else other
        default.configure(style="Default.TButton")
        keys = "Retry: R  ·  Another way: T  ·  Stop: S" if other else "Retry: R  ·  Stop: S"
        ttk.Label(body, text=keys, style="Grey.TLabel").pack(anchor="w", pady=(6, 0))
        self.bind("<Key-r>", lambda _e: self._pick("retry"))
        self.bind("<Key-R>", lambda _e: self._pick("retry"))
        if other:
            self.bind("<Key-t>", lambda _e: other.invoke())
            self.bind("<Key-T>", lambda _e: other.invoke())
        for k in ("<Key-s>", "<Key-S>", "<Escape>"):
            self.bind(k, lambda _e: self._pick("stop"))
        self.bind("<Return>", lambda _e: default.invoke())
        self.protocol("WM_DELETE_WINDOW", lambda: self._pick("stop"))
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        default.focus_set()
        self.grab_set()
        self.wait_window()

    def _pick(self, choice):
        self.result = choice
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.title(f"{APP_NAME} {VERSION}")
        self.geometry(self.settings.get("geometry", "900x620"))
        self.minsize(700, 480)
        self.configure(bg=FACE)
        self.option_add("*Font", FONT)
        self.q = queue.Queue()
        self.busy = False
        self.builder = None
        self.plans, self.plans_for = [], None
        self.last = None
        self.full_log = []
        self.t_start = 0
        self.estimate = None
        self._last_tick = 0
        self._waiting = 0  # background checks still running (tool scan, update check)
        self._style()
        self._ui()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._set_icon()

    def _set_icon(self):
        ico = os.path.join(ASSETS, "icon.ico")
        try:
            if os.name == "nt" and os.path.isfile(ico):
                self.iconbitmap(default=ico)
            pngs = [os.path.join(ASSETS, f"icon-{n}.png") for n in (64, 32, 16)]
            self._icons = [tk.PhotoImage(file=p) for p in pngs if os.path.isfile(p)]
            if self._icons:
                self.iconphoto(True, *self._icons)
        except tk.TclError:
            pass

    # ------------------------------------------------------------ XP style
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=FACE, foreground=TEXT, font=FONT, fieldbackground=WHITE,
                    bordercolor=BORDER, lightcolor=FACE, darkcolor=FACE, troughcolor="#F3F1EC",
                    selectbackground=SELECT, selectforeground=WHITE, focuscolor=FACE)
        s.configure("TButton", background="#F7F6F2", foreground=TEXT, bordercolor=BTN_EDGE,
                    lightcolor=WHITE, darkcolor="#D6D0C5", padding=(10, 3), relief="raised", anchor="center")
        s.map("TButton",
              background=[("disabled", "#F5F4EA"), ("pressed", "#E3E2DA"), ("active", "#EEF4FF")],
              lightcolor=[("active", "#C9DCFC")], darkcolor=[("active", "#7DA2CE")],
              bordercolor=[("disabled", "#C9C7BA"), ("active", "#2C628B")],
              foreground=[("disabled", "#A1A192")])
        s.configure("Default.TButton", font=FONT_B)
        s.configure("TEntry", fieldbackground=WHITE, bordercolor=BORDER, lightcolor=WHITE, darkcolor=WHITE,
                    padding=3)
        s.configure("TCombobox", fieldbackground=WHITE, background="#C3D3FD", arrowcolor="#4D6185",
                    bordercolor=BORDER, lightcolor=WHITE, darkcolor=WHITE, padding=2)
        s.map("TCombobox", fieldbackground=[("readonly", WHITE)], selectbackground=[("readonly", WHITE)],
              selectforeground=[("readonly", TEXT)])
        s.configure("TLabelframe", background=FACE, bordercolor="#D0D0BF", lightcolor=FACE, darkcolor=FACE)
        s.configure("TLabelframe.Label", background=FACE, foreground=GROUP)
        s.configure("TNotebook", background=FACE, bordercolor="#919B9C", tabmargins=(2, 4, 2, 0))
        s.configure("TNotebook.Tab", background="#EEEDE5", foreground=TEXT, bordercolor="#919B9C",
                    lightcolor=WHITE, padding=(12, 3))
        s.map("TNotebook.Tab", background=[("selected", "#FCFCFE")], lightcolor=[("selected", "#3C7FB1")],
              expand=[("selected", (2, 2, 2, 0))])
        s.configure("TFrame", background=FACE)
        s.configure("Page.TFrame", background=FACE)
        s.configure("TLabel", background=FACE, foreground=TEXT)
        s.configure("Grey.TLabel", foreground=GREY)
        s.configure("Big.TLabel", font=("Tahoma", 14, "bold"), foreground="#1F3F8C")
        s.configure("Link.TLabel", foreground="#0033CC")
        s.configure("Time.TLabel", font=("Tahoma", 9))
        s.configure("Treeview", background=WHITE, fieldbackground=WHITE, foreground=TEXT, rowheight=20,
                    bordercolor=BORDER)
        s.configure("Treeview.Heading", background="#EBEADB", foreground=TEXT, relief="flat",
                    bordercolor="#C7C5B2", lightcolor=WHITE, darkcolor="#C7C5B2")
        s.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", WHITE)])
        s.configure("Horizontal.TProgressbar", background=GREEN, troughcolor=WHITE, bordercolor="#686868",
                    lightcolor="#7BE37B", darkcolor="#1E9A1E", thickness=14)
        for o in ("Vertical", "Horizontal"):
            s.configure(f"{o}.TScrollbar", background="#C3D3FD", troughcolor="#F3F1EC", arrowcolor="#4D6185",
                        bordercolor="#B8CBF6", lightcolor="#E6EEFC", darkcolor="#A9C0F4")
        # XP checkbox with a green tick (clam's default shows a cross)
        self._ck = {k: _check_image(self, *k) for k in ((False, False), (True, False), (False, True), (True, True))}
        s.element_create("XP.indicator", "image", self._ck[(False, False)],
                         ("disabled", "selected", self._ck[(True, True)]),
                         ("disabled", self._ck[(False, True)]),
                         ("selected", self._ck[(True, False)]), border=0, sticky="w")
        s.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("XP.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.label", {"side": "left", "sticky": "nswe"})]})])
        s.configure("TCheckbutton", background=FACE, padding=(0, 2, 8, 2))
        s.map("TCheckbutton", background=[("active", FACE)])
        s.configure("TCheckbutton.label", padding=(5, 0))

    # ------------------------------------------------------------ layout
    def _ui(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.nb = nb
        for name, fn in (("Build", self._tab_build), ("History", self._tab_history),
                         ("Toolchains", self._tab_tools), ("About", self._tab_about)):
            f = ttk.Frame(nb, padding=10, style="Page.TFrame")
            nb.add(f, text=name)
            fn(f)
        nb.bind("<<NotebookTabChanged>>", self._tab_changed)

    def _tab_build(self, f):
        box = ttk.LabelFrame(f, text=" Project ", padding=(10, 6))
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Source:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.url = tk.StringVar(value="")  # starts empty every time the app opens
        e = ttk.Entry(box, textvariable=self.url)
        e.grid(row=0, column=1, sticky="ew")
        e.bind("<Return>", lambda _e: self.start_build())
        ttk.Button(box, text="Browse…", width=10, command=self._pick_folder).grid(row=0, column=2, padx=(6, 0))
        ttk.Label(box, text="GitHub link, owner/repo, git URL or local folder",
                  style="Grey.TLabel").grid(row=1, column=1, sticky="w", pady=(1, 6))
        ttk.Label(box, text="Build in:").grid(row=2, column=0, sticky="w", padx=(0, 8))
        self.workdir = tk.StringVar(value=self.settings.get("workdir", build.DEFAULT_WORKDIR))
        ttk.Entry(box, textvariable=self.workdir).grid(row=2, column=1, sticky="ew")
        ttk.Button(box, text="Browse…", width=10, command=self._pick_workdir).grid(row=2, column=2, padx=(6, 0))
        ttk.Label(box, text="Repos are cloned and built here; finished files go to its \\output folder",
                  style="Grey.TLabel").grid(row=3, column=1, sticky="w", pady=(1, 0))

        opts = ttk.LabelFrame(f, text=" Options ", padding=(10, 4))
        opts.pack(fill="x", pady=(8, 0))
        self.ask_dl = tk.BooleanVar(value=self.settings.get("ask_downloads", True))
        self.fresh = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Ask before downloads", variable=self.ask_dl).pack(side="left")
        self.ask_fail = tk.BooleanVar(value=self.settings.get("ask_on_fail", True))
        ttk.Checkbutton(opts, text="Ask when a build fails", variable=self.ask_fail).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(opts, text="Fresh clone", variable=self.fresh).pack(side="left", padx=(12, 0))
        self.strategy = ttk.Combobox(opts, state="readonly", width=22, values=["Automatic (best first)"])
        self.strategy.current(0)
        self.strategy.pack(side="right")
        ttk.Label(opts, text="Strategy:").pack(side="right", padx=(0, 6))

        run = ttk.Frame(f)
        run.pack(fill="x", pady=(10, 6))
        self.build_btn = ttk.Button(run, text="Build", width=11, style="Default.TButton", command=self.start_build)
        self.build_btn.pack(side="left")
        self.detect_btn = ttk.Button(run, text="Detect", width=10, command=self.start_detect)
        self.detect_btn.pack(side="left", padx=(6, 0))
        self.cancel_btn = ttk.Button(run, text="Cancel", width=10, command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=(6, 0))
        self.prog = ttk.Progressbar(run, mode="determinate", length=200, maximum=1000)
        self.prog.pack(side="right")
        line = ttk.Frame(f)
        line.pack(fill="x", pady=(0, 4))
        # fixed width so changing digits never shift anything
        self.time_lbl = ttk.Label(line, text="", style="Grey.TLabel", width=36, anchor="e")
        self.time_lbl.pack(side="right")
        self.status = ttk.Label(line, text="Ready.", style="Grey.TLabel")
        self.status.pack(side="left", fill="x", expand=True)

        btns = ttk.Frame(f)
        btns.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Button(btns, text="Copy", width=10, command=self.copy_log).pack(side="left")
        self.out_btn = ttk.Button(btns, text="Open output", state="disabled",
                                  command=lambda: open_path(self.last and self.last.get("output")))
        self.out_btn.pack(side="right")
        self.logfile_btn = ttk.Button(btns, text="Open log file", state="disabled",
                                      command=lambda: open_path(self.last and self.last.get("log")))
        self.logfile_btn.pack(side="right", padx=(0, 6))

        logf = tk.Frame(f, bg=BORDER, bd=0)
        logf.pack(fill="both", expand=True)
        self.log = tk.Text(logf, bg="#000000", fg="#C0C0C0", insertbackground="#C0C0C0", relief="flat",
                           font=MONO, wrap="none", padx=6, pady=4, state="disabled", bd=0,
                           selectbackground="#C0C0C0", selectforeground="#000000", highlightthickness=0)
        for tag, col in (("err", "#FF5555"), ("ok", "#55FF55"), ("head", "#55FFFF"), ("dim", "#A0A0A0")):
            self.log.tag_configure(tag, foreground=col)
        ys = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        xs = ttk.Scrollbar(logf, orient="horizontal", command=self.log.xview)
        self.log.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.log.grid(row=0, column=0, sticky="nsew", padx=(1, 0), pady=(1, 0))
        ys.grid(row=0, column=1, sticky="ns", pady=(1, 0))
        xs.grid(row=1, column=0, sticky="ew", padx=(1, 0))
        logf.rowconfigure(0, weight=1)
        logf.columnconfigure(0, weight=1)

    def _tab_history(self, f):
        cols = ("time", "repo", "result", "files", "took")
        wrap = tk.Frame(f, bg=BORDER)
        wrap.pack(fill="both", expand=True)
        self.hist = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        for c, w, a in zip(cols, (130, 400, 80, 50, 70), ("w", "w", "center", "center", "center")):
            self.hist.heading(c, text=c.title())
            self.hist.column(c, width=w, anchor=a, stretch=c == "repo")
        self.hist.pack(fill="both", expand=True, padx=1, pady=1)
        self.hist.bind("<Double-1>", lambda _e: self._hist_open("output"))
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(8, 0))
        ttk.Button(b, text="Open output", command=lambda: self._hist_open("output")).pack(side="left")
        ttk.Button(b, text="Open log", command=lambda: self._hist_open("log")).pack(side="left", padx=6)
        ttk.Button(b, text="Rebuild", command=self._hist_rebuild).pack(side="left")
        ttk.Button(b, text="Clear history", command=self._hist_clear).pack(side="right")

    def _tab_tools(self, f):
        ttk.Label(f, text="Build tools on this PC. When a project needs a missing one, RepoHammer asks before "
                          "downloading it.", style="Grey.TLabel").pack(anchor="w", pady=(0, 6))
        wrap = tk.Frame(f, bg=BORDER)
        wrap.pack(fill="both", expand=True)
        self.tools = ttk.Treeview(wrap, columns=("tool", "status", "installer"), show="headings")
        for c, w in (("tool", 140), ("status", 110), ("installer", 380)):
            self.tools.heading(c, text=c.title())
            self.tools.column(c, width=w, stretch=c == "installer")
        self.tools.tag_configure("ok", foreground="#006400")
        self.tools.tag_configure("no", foreground=GREY)
        self.tools.pack(fill="both", expand=True, padx=1, pady=1)
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(8, 0))
        ttk.Button(b, text="Refresh", command=self._refresh_tools).pack(side="left")
        ttk.Button(b, text="Install selected", command=self._install_tool).pack(side="left", padx=6)

    def _tab_about(self, f):
        left = ttk.Frame(f)
        left.pack(side="left", fill="both", expand=True)
        title = ttk.Frame(left)
        title.pack(anchor="w")
        icon = os.path.join(ASSETS, "icon-64.png")
        if os.path.isfile(icon):
            self._about_icon = tk.PhotoImage(file=icon)
            ttk.Label(title, image=self._about_icon).pack(side="left", padx=(0, 10))
        ttk.Label(title, text=APP_NAME, font=("Tahoma", 16, "bold"), foreground="#1F3F8C").pack(side="left")
        ttk.Label(left, text=f"Version {VERSION}  ·  {LICENSE} License", style="Grey.TLabel").pack(anchor="w")
        ttk.Label(left, text=DESCRIPTION, wraplength=440, justify="left").pack(anchor="w", pady=(10, 0))
        ttk.Label(left, text=f"Made by {AUTHOR}", font=FONT_B).pack(anchor="w", pady=(12, 2))
        self._link(left, "Tasin on GitHub", AUTHOR_GITHUB)
        repo_ok = APP_REPO_URL.startswith("http")
        self._link(left, "Project repository" if repo_ok else "Project repository (coming soon)",
                   APP_REPO_URL if repo_ok else None)
        ttk.Label(left, text="Credits: Python, Tkinter, winget.", style="Grey.TLabel").pack(anchor="w", pady=(10, 0))
        upd = ttk.Frame(left)
        upd.pack(anchor="w", pady=(16, 0))
        self.upd_btn = ttk.Button(upd, text="Check for Updates", width=18, command=self.check_updates)
        self.upd_btn.pack(side="left")
        self.upd_lbl = ttk.Label(upd, text="", style="Grey.TLabel", wraplength=320)
        self.upd_lbl.pack(side="left", padx=10)

        right = ttk.LabelFrame(f, text=" Buy Me a Coffee ", padding=10)
        right.pack(side="right", anchor="n", padx=(16, 0))
        qr = os.path.join(ASSETS, "buymeacoffee-qr.png")
        if os.path.isfile(qr):
            self._qr = tk.PhotoImage(file=qr)
            tk.Label(right, image=self._qr, bg=WHITE, bd=1, relief="solid").pack()
        ttk.Button(right, text="Buy Me a Coffee", style="Default.TButton",
                   command=lambda: webbrowser.open(COFFEE_URL)).pack(fill="x", pady=(8, 0))
        ttk.Label(right, text="Scan or click to buy Tasin a coffee", style="Grey.TLabel").pack(pady=(4, 0))

    def _link(self, parent, text, url):
        lbl = ttk.Label(parent, text=text, style="Link.TLabel" if url else "Grey.TLabel",
                        cursor="hand2" if url else "")
        lbl.pack(anchor="w")
        if url:
            lbl.bind("<Button-1>", lambda _e: webbrowser.open(url))

    # ------------------------------------------------------------ actions
    def _work(self):
        return self.workdir.get().strip() or build.DEFAULT_WORKDIR

    def _pick_folder(self):
        d = filedialog.askdirectory(title="Choose a project folder to build")
        if d:
            self.url.set(os.path.normpath(d))

    def _pick_workdir(self):
        d = filedialog.askdirectory(title="Where should RepoHammer clone and build?", initialdir=self._work())
        if d:
            self.workdir.set(os.path.normpath(d))
            self.settings["workdir"] = self.workdir.get()
            save_settings(self.settings)

    def _set_busy(self, busy, text=""):
        self.busy = busy
        st = "disabled" if busy else "normal"
        self.build_btn.configure(state=st)
        self.detect_btn.configure(state=st)
        self.cancel_btn.configure(state="normal" if busy else "disabled")
        self.status.configure(text=text)
        if busy:
            self.t_start = time.time()
            self.prog.configure(value=0)
            self.after(80, self._pump)
        else:
            self.prog.configure(value=1000 if "succeeded" in text.lower() else self.prog["value"])

    def _new_builder(self):
        wd = self._work()
        try:
            os.makedirs(wd, exist_ok=True)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Can't use this build folder:\n{wd}\n\n{e}")
            return None
        self.settings["workdir"] = wd
        ask = self.ask_dl.get()
        self.builder = build.Builder(wd, log=lambda m: self.q.put(("log", m)), auto_install=not ask,
                                     on_event=lambda *a: self.q.put(("event", a)),
                                     confirm=self._confirm_from_worker if ask else None,
                                     on_fail=self._fail_from_worker if self.ask_fail.get() else None)
        return self.builder

    # ------------------------------------------------------------ retry question
    def _fail_from_worker(self, info):
        """Called on the build thread after a strategy fails: ask retry / another way / stop."""
        answer, done = ["stop"], threading.Event()
        self.q.put(("fail", (info, answer, done)))
        done.wait()
        return answer[0]

    def _ask_retry(self, info, answer, done):
        started = time.time()
        answer[0] = RetryDialog(self, info).result
        self.t_start += time.time() - started  # time spent deciding isn't build time
        if answer[0] != "stop":
            self.estimate = None  # the old estimate no longer fits; the next strategy sets a new one
        done.set()

    # ------------------------------------------------------------ download question
    def _confirm_from_worker(self, items, context):
        """Called on the build thread: show the Y/N dialog on the UI thread and wait for the answer."""
        answer, done = [False], threading.Event()
        self.q.put(("ask", (items, context, answer, done)))
        done.wait()
        return answer[0]

    def _ask_downloads(self, items, context, answer, done):
        started = time.time()
        answer[0] = DownloadDialog(self, items, context).result
        self.t_start += time.time() - started  # time spent deciding isn't build time
        done.set()

    def start_detect(self):
        spec = self.url.get().strip()
        if not spec or self.busy:
            return
        b = self._new_builder()
        if not b:
            return
        self._clear_log()
        self.estimate = None
        fresh = self.fresh.get()  # tk variables may only be read on the main thread
        self._set_busy(True, "Downloading and inspecting…")

        def work():
            try:
                remote = not os.path.isdir(spec.strip('"'))
                git_ok = (not remote) or bool(toolchain.find("git")) or \
                    b.allow(b.missing(["git"]), "to download the repository (otherwise a zip copy is used)")
                _info, src = fetch.fetch(spec, b.workdir, b.log, fresh, git_ok)
                plans = detect.detect(src)
                for p in plans:
                    b.log(f"[detect] {p.describe()}")
                if plans:
                    need = b.missing(plans[0].tools + plans[0].soft_tools)
                    b.log("[detect] the first strategy would download: " +
                          (", ".join(f"{label} (~{size})" for _k, label, size in need) if need else "nothing"))
                if not plans:
                    b.log("[detect] nothing recognisable found")
                self.q.put(("plans", (spec, plans)))
            except Exception as e:
                b.log(f"[error] {e}")
            self.q.put(("idle", "Detection finished."))
        threading.Thread(target=work, daemon=True).start()

    def start_build(self):
        spec = self.url.get().strip()
        if not spec or self.busy:
            return
        b = self._new_builder()
        if not b:
            return
        self.settings.update(ask_downloads=self.ask_dl.get(), ask_on_fail=self.ask_fail.get())
        save_settings(self.settings)
        self._clear_log()
        choice = self.strategy.get()
        only = None if choice.startswith("Automatic") else choice
        plans = self.plans if (only and self.plans_for == spec) else None
        self.estimate = self._history_estimate(spec)
        fresh = self.fresh.get()  # tk variables may only be read on the main thread
        self._set_busy(True, "Building on this PC…")

        def work():
            res = b.run(spec, fresh=fresh, only=only, plans=plans)
            self.q.put(("done", res))
        threading.Thread(target=work, daemon=True).start()

    def cancel(self):
        if self.builder:
            self.status.configure(text="Cancelling…")
            threading.Thread(target=self.builder.cancel, daemon=True).start()

    def copy_log(self):
        text = "\n".join(self.full_log) if self.full_log else self.log.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.configure(text=f"Copied {len(text.splitlines())} lines to the clipboard.")

    # ------------------------------------------------------------ time estimate
    def _history_estimate(self, spec):
        times = [h.get("secs", 0) for h in build.history(self._work()) if h.get("spec") == spec and h.get("ok")]
        times = [t for t in times if t][:3]
        return sorted(times)[len(times) // 2] if times else None

    def _plan_started(self, name):
        if self.estimate and self._from_history:
            return
        typical = next((v for k, v in TYPICAL.items() if name.startswith(k)), 180)
        self.estimate = (time.time() - self.t_start) + typical

    def _tick(self):
        el = time.time() - self.t_start
        if self.estimate:
            left = self.estimate - el
            frac = min(el / self.estimate, 0.97)
            self.prog.configure(value=int(frac * 1000))
            txt = f"about {fmt_secs(left)} left" if left > 5 else "almost done…" if left > -60 else "taking longer than usual"
            self.time_lbl.configure(text=f"Elapsed {fmt_secs(el)}  ·  {txt}")
        else:
            self.time_lbl.configure(text=f"Elapsed {fmt_secs(el)}  ·  estimating…")

    # ------------------------------------------------------------ queue pump
    def _pump(self):
        lines = []
        try:
            for _ in range(800):
                kind, val = self.q.get_nowait()
                if kind == "log":
                    lines.append(val)
                    continue
                self._flush(lines)
                lines = []
                if kind == "event":
                    if val[0] == "plan":
                        self._plan_started(val[1])
                elif kind == "plans":
                    spec, plans = val
                    self.plans, self.plans_for = plans, spec
                    self.strategy.configure(values=["Automatic (best first)"] + [p.name for p in plans])
                    self.strategy.current(0)
                elif kind == "idle":
                    self._set_busy(False, val)
                    self.time_lbl.configure(text=f"Took {fmt_secs(time.time() - self.t_start)}")
                elif kind == "done":
                    self._done(val)
                elif kind == "ask":
                    self._ask_downloads(*val)
                elif kind == "fail":
                    self._ask_retry(*val)
                elif kind == "tools":
                    self._waiting = max(0, self._waiting - 1)
                    self._fill_tools(val)
                elif kind == "update":
                    self._waiting = max(0, self._waiting - 1)
                    self._update_result(*val)
        except queue.Empty:
            pass
        self._flush(lines)
        if self.busy and time.time() - self._last_tick >= 1:
            self._last_tick = time.time()
            self._tick()
        if self.busy or self._waiting or not self.q.empty():
            self.after(80, self._pump)

    def _done(self, val):
        self.last = val
        ok = val["ok"]
        why = val.get("why") or []
        text = ("Build succeeded" + (f" — {len(val['artifacts'])} file(s)" if val["artifacts"] else "")) if ok \
            else ("Build failed" + (f": {why[0]}" if why else ""))
        self._set_busy(False, text)
        self.status.configure(foreground="#006400" if ok else "#C00000")
        self.time_lbl.configure(text=f"Took {fmt_secs(val.get('secs', time.time() - self.t_start))}")
        self.out_btn.configure(state="normal" if val.get("output") else "disabled")
        self.logfile_btn.configure(state="normal" if val.get("log") else "disabled")
        if ok:
            self.prog.configure(value=1000)
            play_done_sound()

    def _flush(self, lines):
        if not lines:
            return
        self.full_log += lines
        if len(self.full_log) > 200_000:
            del self.full_log[:50_000]
        self.log.configure(state="normal")
        at_end = self.log.yview()[1] > 0.995
        for m in lines:
            low = m.lower()
            tag = ("err" if (not m.startswith("  ") and ("error" in low or "failed" in low)) or
                   low.startswith("  error") or " error " in low or m.startswith("  >>") else
                   "ok" if "succeeded" in low or ": ready" in low else
                   "head" if m.startswith("===") or m.startswith("[step") else
                   "dim" if m.startswith("  ") else "")
            self.log.insert("end", m + "\n", tag)
        n = int(self.log.index("end-1c").split(".")[0])
        if n > MAX_LOG_LINES:
            self.log.delete("1.0", f"{n - MAX_LOG_LINES}.0")
        if at_end:
            self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.full_log = []
        self.status.configure(foreground=GREY)
        self.time_lbl.configure(text="")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    @property
    def _from_history(self):
        spec = self.url.get().strip()
        return self._history_estimate(spec) is not None

    # ------------------------------------------------------------ other tabs
    def _tab_changed(self, _e):
        tab = self.nb.tab(self.nb.select(), "text")
        if tab == "History":
            self._fill_history()
        elif tab == "Toolchains" and not self.tools.get_children():
            self._refresh_tools()

    def _fill_history(self):
        self.hist.delete(*self.hist.get_children())
        self._hist = build.history(self._work())
        for i, h in enumerate(self._hist):
            t = h["time"]
            self.hist.insert("", "end", iid=str(i), values=(
                f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[9:11]}:{t[11:13]}", h["spec"],
                "✔ OK" if h["ok"] else "✘ Failed", h.get("count", 0),
                fmt_secs(h["secs"]) if h.get("secs") else "—"))

    def _hist_sel(self):
        s = self.hist.selection()
        return self._hist[int(s[0])] if s else None

    def _hist_open(self, key):
        h = self._hist_sel()
        if h:
            open_path(h.get(key))

    def _hist_rebuild(self):
        h = self._hist_sel()
        if h:
            self.url.set(h["spec"])
            self.nb.select(0)
            self.start_build()

    def _hist_clear(self):
        if not build.history(self._work()):
            return
        if messagebox.askyesno(APP_NAME, "Clear the build history?\n\nBuilt files and log files stay on disk."):
            build.clear_history(self._work())
            self._fill_history()

    def _refresh_tools(self):
        self.tools.delete(*self.tools.get_children())
        self.tools.insert("", "end", values=("checking…", "", ""))
        self._waiting += 1
        threading.Thread(target=lambda: self.q.put(("tools", toolchain.snapshot())), daemon=True).start()
        self.after(80, self._pump)

    def _fill_tools(self, snap):
        self.tools.delete(*self.tools.get_children())
        for k, ok in sorted(snap.items(), key=lambda kv: (not kv[1], kv[0])):
            wid = toolchain.TOOLS[k][1]
            how = f"winget: {wid}" if wid else ("npm / corepack" if k in ("pnpm", "yarn") else "manual install")
            self.tools.insert("", "end", iid=k, values=(k, "✔ Installed" if ok else "— Missing", how),
                              tags=("ok" if ok else "no",))

    def _install_tool(self):
        s = self.tools.selection()
        if not s or self.busy:
            return
        key = s[0]
        self.nb.select(0)
        self._clear_log()
        self.estimate = None
        self._set_busy(True, f"Installing {key}…")

        def work():
            toolchain.ensure(key, lambda m: self.q.put(("log", m)), True)
            self.q.put(("tools", toolchain.snapshot()))
            self.q.put(("idle", f"{key}: done."))
        threading.Thread(target=work, daemon=True).start()

    def check_updates(self):
        self.upd_btn.configure(state="disabled", text="Checking…")
        self._waiting += 1
        threading.Thread(target=lambda: self.q.put(("update", updater.check())), daemon=True).start()
        self.after(80, self._pump)

    def _update_result(self, status, msg, rel):
        self.upd_btn.configure(state="normal", text="Check for Updates")
        self.upd_lbl.configure(text=msg, foreground="#006400" if status == "latest" else
                               "#C00000" if status == "error" else "#0033CC")
        if status == "update":
            self.upd_lbl.configure(text=msg + " Installing…")
            self.upd_btn.configure(state="disabled", text="Updating…")

            def work():
                try:
                    updater.apply(rel)
                    self.after(0, self._close)
                except Exception as e:
                    self.after(0, lambda: (self.upd_lbl.configure(text=f"Update failed: {e}", foreground="#C00000"),
                                           self.upd_btn.configure(state="normal", text="Check for Updates")))
            threading.Thread(target=work, daemon=True).start()

    def _close(self):
        if self.busy and not messagebox.askyesno(APP_NAME, "A build is running. Stop it and quit?"):
            return
        if self.builder and self.busy:
            self.builder.cancel()
        self.settings["geometry"] = self.geometry()
        self.settings["workdir"] = self._work()
        save_settings(self.settings)
        self.destroy()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        # own taskbar identity, so Windows shows the hammer instead of Python's icon
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TasinPy.RepoHammer")
    except Exception:
        pass
    App().mainloop()
