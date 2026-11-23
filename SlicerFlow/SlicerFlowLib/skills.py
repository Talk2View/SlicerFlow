"""SlicerFlow's skills: the ones the extension ships (read-only, in the Agent Skills
layout <name>/SKILL.md) and the user's own folder, which the Talk2View SDK keeps
(``UIServer(skills_dir=...)``): the chat's Skills view writes, edits, imports and deletes
there. ``pick_import`` is the host's file/folder dialog for Import, run on Slicer's main
thread."""

import os

from . import paths

BUNDLED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Resources", "Skills"
)
USER_README = """Your own SlicerFlow skills.

Manage them in the chat's Skills view (the sparkles button in the chat header): write one,
ask the assistant to "save this as a skill", or import a file. Each skill is a folder here,
<name>/SKILL.md, in the Agent Skills format (agentskills.io):

---
name: my-skill
description: What it does, and when the assistant should use it
---
Then the instructions for the assistant, in Markdown.

A skill here replaces a bundled skill with the same name.
"""


def ensure_user_dir(user_dir=None):
    folder = user_dir if user_dir is not None else paths.user_skills_dir()
    os.makedirs(folder, exist_ok=True)
    readme = os.path.join(folder, "README.txt")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as fh:
            fh.write(USER_README)
    return folder


def _dialog():
    import qt  # Slicer's PythonQt; imported here so unit tests run without Slicer

    box = qt.QMessageBox()
    box.setText("Import a skill file (.md) or a skill folder?")
    file_button = box.addButton("File…", qt.QMessageBox.AcceptRole)
    folder_button = box.addButton("Folder…", qt.QMessageBox.AcceptRole)
    box.addButton(qt.QMessageBox.Cancel)
    box.exec_()
    if box.clickedButton() == file_button:
        return qt.QFileDialog.getOpenFileName(None, "Import skill", "", "Skill (*.md *.markdown)")
    if box.clickedButton() == folder_button:
        return qt.QFileDialog.getExistingDirectory(None, "Import skill folder")
    return ""


def pick_import():
    """The path to import, or None when the user cancelled."""
    return _dialog() or None
