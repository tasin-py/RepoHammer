# Changelog

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
