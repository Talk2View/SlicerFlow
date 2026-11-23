# Developing SlicerFlow

How to run SlicerFlow from a checkout, test it, move it to a new Talk2View SDK release, and
how its parts fit together. For what SlicerFlow does, see [README.md](README.md).

## Run it from a checkout

1. Start Slicer with the module folder on its module path (here the checkout is
   `~/SlicerFlow`):

   ```bash
   /Applications/Slicer.app/Contents/MacOS/Slicer \
     --additional-module-paths ~/SlicerFlow/SlicerFlow
   ```

   To make this permanent, use **Edit → Application Settings → Modules → Additional
   module paths** and add `~/SlicerFlow/SlicerFlow`.
2. The **SlicerFlow by Talk2View** panel opens on the right. Press **Start chat** (the
   first time; after that the chat starts with Slicer). The extension's own partner key
   ships in `Resources/partner.json`, so there is nothing to enter; to use another, see
   "Partner key" in the README. The first start installs the Talk2View SDK and `httpx`
   from PyPI into Slicer's Python.
3. Send a message as a guest, or sign in with your Talk2View account in the chat.

**Reload** (developer mode) stops the chat, takes the chat panel down and reloads
`SlicerFlowLib` too; the reloaded module builds the panel again, with the chat stopped
(press **Start chat** in it).

## Tests

```bash
scripts/unit-tests.sh           # no Slicer: tests/, with the pinned SDK from PyPI (needs uv)
scripts/slicer-tests.sh         # inside 3D Slicer: SlicerFlow/Testing/Python
scripts/slicer-tests.sh 'test_tools_*.py'
python3 scripts/check_cmake_lists.py   # the CMake lists match the module's files
```

`scripts/slicer-tests.sh` starts a new Slicer (on macOS with `open -n`, so a Slicer you
have open is left alone), runs the suite with a main window, and writes
`build/slicer-tests/result.json` and `tests.log`. The tests use a temporary Talk2View
folder and Slicer's temporary settings, never your sign-in, and the chat never starts by
itself there (`--testing`, and `SLICERFLOW_NO_AUTOSTART=1`): the tests start it. The end-to-end test runs the
whole chat against a scripted engine (`Testing/Python/fake_engine.py`).

GitHub Actions (`.github/workflows/tests.yml`) runs the unit tests, then the Slicer suite
inside official 3D Slicer 5.12.4 builds on Linux and Windows, pinned by revision and
SHA-512.

## Update the SDK

The module installs one exact release of the Talk2View Python SDK from PyPI:
`talk2view==0.5.1`, set by `SDK_VERSION` in `SlicerFlow/SlicerFlowLib/sdk.py`. The SDK
and its chat page live in
[github.com/Talk2View/talk2view-python](https://github.com/Talk2View/talk2view-python).

Starting the chat installs that release when the SDK is missing or another version is
installed (such as the wheel an earlier extension shipped); there is no button for it. To
move to a new release, change `SDK_VERSION` and the version in this section, then run both
test suites.

To run the module on unreleased SDK changes, build the checkout's chat page
(`cd embed && npm ci && npm run build`), then install the checkout in Slicer's Python as an
editable install, from Slicer's Python console:
`slicer.util.pip_install(["-e", "<talk2view-python checkout>"])`. The module leaves an
editable SDK alone, whatever its version. For the unit tests,
`SF_SDK=<talk2view-python checkout> scripts/unit-tests.sh` uses it in place of the release.

## How it fits together

```
Slicer main thread                      background threads
──────────────────                      ──────────────────
ChatDock (the SlicerFlow by Talk2View panel)
  └─ qSlicerWebWidget ──HTTP+SSE──▶ talk2view.ui.UIServer (127.0.0.1, per-launch token)
SlicerFlowWidget (module panel: settings, Start/Stop)
SlicerFlowLib.host.SlicerHost             ├─ CommandRouter ─▶ FollowUpController ─▶ engine
  (qt.QTimer drains the queue)  ◀─post──  ├─ skillsList ─▶ t2v.skills.rescan() (the SDK's own)
SlicerFlowLib.tools (Slicer API)          ├─ skillImport ─▶ SlicerFlowLib.skills.pick_import()
                                           └─ sf-follow-up: screenshots ─▶ upload + send
```

- `SlicerFlowLib/chat_dock.py`: the SlicerFlow by Talk2View panel on the right: its web
  view, status line, starting by itself, and starting and stopping the chat.
- `SlicerFlowLib/lifecycle.py`: the one chat per Slicer process (it survives Reload), the
  quit hook, and the lock that keeps a second process off the same sign-in.
- `SlicerFlowLib/runtime.py`: builds the SDK client, the controller, the follow-ups and
  the UI server (wired to the bundled and the user's skills), and stops them in order.
- `SlicerFlowLib/host.py`: the `HostAdapter`: the main-thread queue, the web view URL,
  links, clipboard, theme and token storage.
- `SlicerFlowLib/tools/`: the Slicer tools, and how the chat labels them.
- `SlicerFlowLib/followup.py`: `capture_view`'s screenshots, sent in a follow-up message.
- `SlicerFlowLib/skills.py`: the bundled skills (read-only), and the file/folder picker
  for the Skills view's Import; the SDK keeps the user's own folder (`skills_dir`).
- `SlicerFlowLib/user_tools.py`: the tool packs in the user's folder: SlicerFlow's own
  (written there and kept current unless edited), the user's, their on/off state
  (`tools.json`), `REQUIRES`, reading a pack's tools from its source, and loading the ones
  that are on, each on its own.
- `SlicerFlowLib/tools_panel.py`: the module panel's **Tools** section: every pack, the
  selected one's tools and file, Open in editor, Restore original.
- `SlicerFlowLib/panel_style.py`: the module panel's Talk2View look: the brand colours,
  the bundled Space Grotesk (`Resources/Fonts/`, SIL OFL 1.1) and the Teal main button.
- `scripts/generate_brand_icons.py`: draws the module's icons from the Talk2View
  logomark's path (the app icon tile and the panel's Teal mark); a unit test fails when
  the icons in the repo differ from what it draws.
- `SlicerFlowLib/config.py`: the partner key, the engine and the minimum Slicer version.
- `SlicerFlowLib/sdk.py`: installs the pinned SDK release from PyPI into Slicer's Python.
- `SlicerFlowLib/paths.py`: where the sign-in, the lock, and the user's skills and tools
  live.

## Verifying the microphone on a real macOS launch

`test_audio_probe.py`'s child is a poor stand-in for how an end-user actually launches
Slicer: it runs `Contents/bin/PythonSlicer`, not `Contents/MacOS/Slicer`, and TCC can
attribute its microphone request to whatever launched the test run rather than to Slicer's
own bundle. To see what a real double-click gets, on a Mac of your own (never a
Slicer instance already running):

1. Start from a clean TCC state for Slicer only -- this resets that one client alone,
   nothing else on the machine:
   ```bash
   tccutil reset Microphone $(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" \
     /Applications/Slicer5.12.4.app/Contents/Info.plist)
   ```
2. Launch a fresh instance through LaunchServices, so Slicer is its own responsible
   process (a Finder double-click is equivalent; `open` is just scriptable):
   ```bash
   open -n /Applications/Slicer5.12.4.app --args --python-script <probe.py>
   ```
   where `<probe.py>` opens the microphone **in-process** (`sounddevice`, the way the module's
   dictation does it -- not a child process), reads about a second of audio, and writes
   `{"plistKey": ..., "exception": ..., "peak": ...}` to a file before quitting.
3. Read three things afterwards:
   - the probe's file -- a peak of 0 with no exception is silent denial; a peak above 0 is
     a real grant from somewhere (repeat from a fresh login session to rule out one
     inherited rather than Slicer's own);
   - `~/Library/Logs/DiagnosticReports/Slicer*` for a crash whose reason mentions
     "privacy-sensitive data without a usage description" (the documented worst case, if
     it ever happens);
   - `sudo log show --last 5m --predicate 'subsystem == "com.apple.TCC"'` for the
     `kTCCServiceMicrophone` decision and which client it named.

Until this has actually been run, treat "macOS terminates the app" as the documented,
unconfirmed worst case, not an observed fact.
