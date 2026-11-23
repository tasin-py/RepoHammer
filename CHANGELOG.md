# Changelog

## 1.3.0

- If RepoHammer can't recognise how a project builds, it now offers the commands from the project's README. You tick which ones run; nothing runs without your OK.
- Commands that need admin rights, run a script from the internet, delete files, install programs for the whole PC, or only work on Linux/macOS are marked and start unticked.
- Python commands from a README run in a private environment (`.rf-venv`) inside the project, so they don't change your own Python.
- **Detect** lists the README's commands when nothing else is recognised.
- On the command line you pick the commands by number. `--no-ask` never runs README commands.

## 1.2.0

- When a build strategy fails, RepoHammer now asks what to do: **Retry**, **Try another way** (choose which strategy) or **Stop**. It shows why it failed, what it fixed and how long the attempt took. Untick **Ask when a build fails** to keep the old automatic behaviour.
- On the command line, the same question appears as `[R]etry, [T]ry the next way, [S]top`. Use `--no-ask` to skip it.
- The download checkbox is now called **Ask before downloads**, to make room for the new option.

## 1.1.0

- RepoHammer now asks before it downloads anything. After it works out how to build a repo, it lists the tools it needs to download with their approximate sizes. Press **Y** to download them or **N** to skip them, and RepoHammer then tries other ways to build that don't need them.
- The same question covers every download: Git, .NET SDKs, Python versions, Inno Setup and the automatic fixes.
- **Detect** shows what the first strategy would download.
- On the command line, a `[Y/n]` prompt appears. Use `--yes` to allow downloads without asking.
- The Source box starts empty each time the app opens.
- Fixed the Toolchains tab sometimes staying on "checking…".
- New name and icon: RepoHammer, with a hammer and lightning icon.
- A chime plays when a build succeeds, instead of the Windows error sound.

## 0.1.0

First version.

- Builds a GitHub repo on your own PC: paste a link, RepoHammer clones it, works out how it builds, and compiles it.
- Replays the repo's own GitHub Actions build job locally when it has one.
- Uses the repo's own build script (`build.ps1`, `build.cmd`, `build.sh`) when it has one.
- Recognises .NET, Visual Studio, Rust, Go, Java and Kotlin (Maven, Gradle), C and C++ (CMake, Meson, Make), Zig, Node.js (npm, pnpm, yarn, bun, Electron, Tauri), Python (wheel, PyInstaller), Flutter, Deno and Docker.
- Tries the next approach when one fails, and fixes common failures by itself, for example missing .NET Framework reference assemblies.
- Says in plain words why a build failed when it can't be fixed.
- Copies the finished files into one output folder, with a log for every build.
- Windows XP-style window with build history, a tool list and an estimated time.
- Command line: `python app.py build owner/repo`.
