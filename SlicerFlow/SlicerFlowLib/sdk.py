"""Installing the Talk2View Python SDK into Slicer's Python.

The module installs the SDK release it is tested against from PyPI (``REQUIREMENT``, one
exact version). ``needs_install()`` is True when the SDK is missing or another version is
installed. An editable checkout of the SDK
is left alone, whatever its version, so a developer can run the module on SDK changes.

Only ``install()`` needs Slicer; the rest runs in any Python.
"""

import importlib
import importlib.metadata
import importlib.util
import json
import logging
import sys

logger = logging.getLogger("SlicerFlow")

#: The SDK release on PyPI this module is built and tested against.
SDK_VERSION = "0.5.1"
#: One exact version, so nothing else can be resolved for it (the same defence as
#: ``sounddevice`` below), and ``--only-binary :all:`` so pip never builds from source.
REQUIREMENT = "talk2view==" + SDK_VERSION
PIP_ARGS = ["--only-binary", ":all:", REQUIREMENT]

#: Set once ``ensure_sounddevice()`` has failed, so an offline machine (or one PyPI cannot
#: be reached from) does not repeat pip's retry/backoff, in a modal progress window, on
#: every chat start for a feature that may never be used. Cleared only by a fresh process.
_sounddevice_unavailable = False


def installed():
    """True when an SDK with the UI server's options can be imported."""
    try:
        importlib.import_module("talk2view.ui.options")
    except ImportError:
        return False
    return True


def version():
    try:
        return importlib.import_module("talk2view").__version__
    except ImportError:
        return None


def is_editable(direct_url_text):
    """True when pip's ``direct_url.json`` text says the install is an editable checkout."""
    try:
        data = json.loads(direct_url_text or "")
    except ValueError:
        return False
    dir_info = data.get("dir_info") if isinstance(data, dict) else None
    return isinstance(dir_info, dict) and dir_info.get("editable") is True


def installed_editable():
    try:
        dist = importlib.metadata.distribution("talk2view")
    except importlib.metadata.PackageNotFoundError:
        return False
    return is_editable(dist.read_text("direct_url.json"))


def should_install(importable, current_version, editable):
    """The decision, given: can the SDK be imported, its version, and whether it is an
    editable checkout."""
    if not importable:
        return True
    if editable:
        return False
    return current_version != SDK_VERSION


def needs_install():
    return should_install(installed(), version(), installed_editable())


def install(force=False):
    """pip-install ``REQUIREMENT`` from PyPI. ``force`` reinstalls it over whatever copy is
    installed."""
    import slicer

    if force:
        slicer.util.pip_install(["--force-reinstall", "--no-deps"] + PIP_ARGS)
    # Also the dependencies: a forced reinstall above leaves them out, so a missing one
    # (httpx) is put back here.
    slicer.util.pip_install(PIP_ARGS)
    forget_imports()
    importlib.invalidate_caches()


def ensure_sounddevice(*, install=True, slicer_module=None, find_spec=None, import_module=None):
    """pip-install ``sounddevice`` (PortAudio; the macOS and Windows wheels carry the
    library) when it is missing. False when it could not be installed or imported: the
    chat then shows no mic button. With ``install`` False (the chat starting by itself
    with Slicer, which never runs pip) a missing ``sounddevice`` is just False. ``slicer_module``/``find_spec``/``import_module`` are
    for ``tests/`` (no Slicer there): a real call supplies none of them.

    Pinned to one exact version, with ``--only-binary :all:`` so pip can never fall back
    to building from source inside Slicer. Not installed with ``--require-hashes``: that
    needs a hash for every package pip would otherwise resolve on its own, including
    ``sounddevice``'s own dependencies (``cffi``, ``pycparser``) that this call does not
    pin -- doing it properly needs a requirements/constraints file listing (and updating)
    all of them, which is more than this one call justifies today. The exact pin is the
    defence in its place: nothing but ``sounddevice==0.5.6`` can be resolved.

    A failed attempt is remembered for the rest of this process (module-level
    ``_sounddevice_unavailable``): retrying pip on every chat start on a machine that
    cannot reach PyPI would otherwise repeat its retry/backoff, in a modal progress
    window, for a feature the user may never touch. A success needs no such cache:
    ``find_spec`` below is already immediate once ``sounddevice`` is actually importable.

    ``slicer.util.pip_install`` does not require the main thread for a plain blocking call
    like this one (off it, it just disables its progress dialog and still runs pip
    synchronously -- ``slicer/packaging.py``); ``Runtime._capture()``, this function's only
    caller, happens to run on the main thread anyway, which is why the dialog shows.
    """
    global _sounddevice_unavailable
    if _sounddevice_unavailable:
        return False
    if slicer_module is None:
        import slicer as slicer_module
    if find_spec is None:
        find_spec = importlib.util.find_spec
    if import_module is None:
        import_module = importlib.import_module

    if find_spec("sounddevice") is None:
        if not install:
            return False
        try:
            slicer_module.util.pip_install(["--only-binary", ":all:", "sounddevice==0.5.6"])
        except Exception:
            _sounddevice_unavailable = True
            logger.warning("SlicerFlow: sounddevice could not be installed; no dictation this session")
            return False
        importlib.invalidate_caches()
    try:
        import_module("sounddevice")
    except Exception:
        _sounddevice_unavailable = True
        logger.warning("SlicerFlow: sounddevice could not be imported; no dictation this session")
        return False
    return True


def forget_imports():
    """Drop imported SDK modules (and ours, which import them) so the next import is fresh."""
    for name in list(sys.modules):
        if name == "talk2view" or name.startswith("talk2view."):
            del sys.modules[name]
        elif name.startswith("SlicerFlowLib.") and name != "SlicerFlowLib.sdk":
            del sys.modules[name]
