# SlicerFlow

SlicerFlow adds an AI assistant to [3D Slicer](https://www.slicer.org). Chat with it in a
panel beside your views, and it looks at and changes your scene for you: it loads data,
sets layouts and views, thresholds and measures segments, places markups and, when you
allow it, looks at a screenshot of a view.

![SlicerFlow in 3D Slicer: the assistant loaded the MRHead sample and showed it in 3D](Screenshots/1.png)

The chat is in the **SlicerFlow by Talk2View** panel on the right of the window. The
**SlicerFlow** module (**Modules → Talk2View → SlicerFlow**) starts and stops it and
lists the assistant's tools, which you can switch off or extend with your own.

- **Needs:** 3D Slicer 5.12 or newer, and the internet. The assistant runs on
  [Talk2View](https://talk2view.com)'s online service: chat as a guest, or sign in with a
  free Talk2View account.
- **Your data:** nothing is sent until you press **Start chat**. Once you do, what you type
  or dictate goes to Talk2View and the AI models it uses to answer, together with what the
  assistant's tools find in your scene: the names of what is loaded (a volume loaded from
  DICOM is often named after the patient), sizes, spacing, value ranges, segment names and
  measurements, and any screenshot you approve. The descriptions of the tools and skills
  the assistant can use, your own included, are sent too. The image data itself never
  leaves your computer except in a screenshot you approve. See the
  [Talk2View privacy policy](https://talk2view.com/legal/privacy-policy).
- **Not for clinical use.** SlicerFlow is for research and education. Answers can be
  wrong: check them before you act on them.
- **License:** MIT (`LICENSE.txt`). The bundled Space Grotesk fonts are under the SIL Open
  Font License 1.1.

## Getting started

1. Install SlicerFlow from Slicer's **Extensions Manager** and restart Slicer.
2. The **SlicerFlow by Talk2View** panel opens on the right and says what the chat sends.
   Press **Start chat**. The first time, this installs the Talk2View SDK
   (`talk2view==0.5.1`) from PyPI into Slicer's Python. From then on the chat starts by
   itself with Slicer.
3. Type a message. Try *"Load the MRHead sample and show it in 3D"*, *"Threshold the bone
   and tell me its volume"*, or *"Take a screenshot of the 3D view and tell me what you
   see"*.

Closing the panel leaves the chat running; **View → SlicerFlow by Talk2View** brings it
back. When something stops the chat (no internet, the chat already open in another Slicer
window), a line at the top of the panel says so, with a button to fix it.

## What it can do

The tools come in packs: Scene & data, Views, Volumes, Segmentation, Markups and
Screenshots. They are all on; switch any of them off under **Tools** in the module panel.

The tools marked "asks first" wait for your approval in the chat, and each asks every
time: there is no "always allow".

| Tool | What it does |
|---|---|
| `get_scene_context` | Lists the volumes, segmentations, models and markups, the layout and where each slice view is |
| `list_sample_data` | Lists Slicer's sample data sets |
| `load_sample_data` (asks first) | Downloads and loads a sample data set. 3D Slicer stops responding while it downloads, a minute or so the first time |
| `load_file` (asks first) | Loads a volume, segmentation, model or markups file from your computer (not scenes or DICOM folders) |
| `set_layout` | conventional, four-up, 3d-only, red-only, yellow-only, green-only, dual-3d |
| `navigate_slice` | Moves a slice view to an offset, or by an amount |
| `center_views` | Resets the slice and 3D views |
| `set_window_level` | Window/level of a volume |
| `set_volume_rendering` | Shows or hides volume rendering, with an optional preset |
| `set_segment_display` | Shows or hides segments, and sets their opacity or color |
| `threshold_segment` (asks first) | Makes a segment from a value range; replaces a segment with the same name |
| `measure_segment_volumes` | Measures segment volumes in cm³ |
| `add_markup_point` | Adds a point markup |
| `capture_view` (asks first) | Takes a screenshot of a view and sends it to the assistant |

No SlicerFlow tool runs Python code, clears the scene or saves files.

**Screenshots** leave your computer, and the corner text of a slice view can show patient
details, so `capture_view` asks every time. SlicerFlow sends the images in a message of
their own after the reply ("(Sent by SlicerFlow) The screenshots capture_view took: …").
Looking at images needs a model that supports them.

**Dictation:** on Windows and Linux, a mic button appears in the chat when a microphone is
present (Linux also needs `sudo apt install libportaudio2`), and Talk2View transcribes
what you record. macOS has no mic button: the official Slicer app does not declare that
it uses the microphone, so macOS will not let it record.

## Your account

- **Guests:** your first message starts a guest session, within the limits Talk2View sets
  for guests. You stay the same guest on this computer until someone signs in, so on a
  shared computer the next person does too.
- **Sign in** from the chat's account button, with email and password, Apple or Google.
  Apple and Google open your web browser, which must run on the same computer as Slicer.
  Forgot your password? Reset it at https://talk2view.com/reset-password.
- **Chats** are kept on this computer, in the chat's **Chats** list.
- **One chat at a time per user account:** a second Slicer window of yours shows a message
  instead of a second chat, because the two would sign each other out.

## Skills

Skills are notes the assistant reads when a request needs them, in the
[Agent Skills](https://agentskills.io/specification) format (a folder with a `SKILL.md`).
SlicerFlow ships four: CT window presets, segmenting by threshold, measuring and reporting
volumes, and exploring the sample data.

Manage your own in the chat's **Skills** view (the sparkles button): write one, ask the
assistant to "save this as a skill", or import a `.md` file or skill folder. You can also
customize a bundled skill, turn skills on or off, and delete them. Your skills live in
`<Slicer settings folder>/Talk2View/skills/`; one with the same name as a bundled skill
replaces it.

## Your own tools

Each tool pack is a Python file in `<Slicer settings folder>/Talk2View/tools/`.
SlicerFlow's own packs are there too (`slicerflow_scene.py`, `slicerflow_view.py` and so
on), so you can read and copy them. A pack is functions with the Talk2View SDK's `@tool`
decorator, listed in `TOOLS`, with an optional `TITLE`; each function's signature and
docstring are what the assistant sees.

Manage packs in the module panel under **Tools**: select one to see its tools,
**Open in editor**, **Show in folder**, or turn it on or off. **New…** writes a commented example and
**Import…** copies a `.py` file in. A pack runs inside 3D Slicer with
full access to your scene and your files, so turning one on asks first. Changes load when
you press **Restart chat**.

- Your tools ask for approval before every call. `@tool(permission=False)` skips that; use
  it only for a tool that only reads.
- If you edit one of SlicerFlow's packs, it is marked **Edited**, it is off until you turn
  it on, and updates leave it alone. **Restore original** puts SlicerFlow's version back
  and keeps yours as `.py.bak`.
- Packages Slicer does not have go in `REQUIRES = {"import name": "pip package"}`; the
  panel offers **Install packages**.
- Tools run on Slicer's main thread, so Slicer waits until one returns. Don't call
  `slicer.app.processEvents()` in one.
- A pack that fails to load shows its error in the panel, and the chat starts without it.
- With more than 30 tools, the assistant chooses among them less reliably.

## Developers

Running SlicerFlow from a checkout, its tests, its settings and how it fits together are
in [DEVELOPING.md](DEVELOPING.md).
