"""Where SlicerFlow keeps its files: ``<Slicer settings dir>/Talk2View/``.

    auth.json           the Talk2View sign-in (``talk2view.FileStorage``: 0600, atomic writes)
    conversations.json  the chat list (``talk2view.FileStorage``, same as above), separate
                         from auth.json so a reply does not rewrite the token file
    chat.lock   held while a chat runs (``lifecycle.ChatLock``)
    skills/     the user's own skills, which win over the bundled ones by name
    tools/      the tool packs (``user_tools``), one .py file each: SlicerFlow's own as
                 slicerflow_<pack>.py, kept current unless edited, and the user's own
    tools.json  which packs are on, and what SlicerFlow last wrote into tools/
    metrics.jsonl  one line of timings per chat message (``perf``), and metrics.jsonl.1

One directory per user account, shared by every 3D Slicer version and process that user
runs. ``SLICERFLOW_HOME`` (or the older ``TALK2SLICER_HOME``) replaces it (the test runner
points it at a temporary directory). This module imports ``slicer`` only when it has to, so
it runs without Slicer.
"""

import os

from .config import ENV_HOME, getenv


def home():
    override = getenv(os.environ, ENV_HOME)
    if override:
        return os.path.abspath(os.path.expanduser(override))
    import slicer

    return os.path.join(os.path.dirname(slicer.app.slicerUserSettingsFilePath), "Talk2View")


def token_file():
    return os.path.join(home(), "auth.json")


def conversations_file():
    return os.path.join(home(), "conversations.json")


def lock_file():
    return os.path.join(home(), "chat.lock")


def user_skills_dir():
    return os.path.join(home(), "skills")


def user_tools_dir():
    return os.path.join(home(), "tools")


def user_tools_file():
    return os.path.join(home(), "tools.json")


def metrics_file():
    return os.path.join(home(), "metrics.jsonl")
