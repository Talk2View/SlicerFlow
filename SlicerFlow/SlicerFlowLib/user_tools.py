"""The tool packs in ``<home>/tools/``: SlicerFlow's own, written there as
``slicerflow_<pack>.py`` and kept current unless the user edits one (``sync_builtins``), and
the user's own, one "tool pack" per Python file. A pack is written like a module of
``SlicerFlowLib.tools``: ``@tool`` functions in a ``TOOLS`` list, and optionally ``TITLE``
(what the panel calls it), ``PRESENTATION`` (how the chat shows them) and ``REQUIRES`` (the
packages it needs, ``{"import name": "pip package"}``).

Loading a pack runs its code, so only the packs the user turned on (``tools.json``) are
loaded, and only when a chat starts (``Runtime``). Everything else here (listing, the
on/off state, ``REQUIRES``, new and imported files) reads files and runs no pack code, so
the module panel can show packs without running them. A pack that fails to load carries
its error and no tools; the other packs, and the chat, start regardless.

Nothing here imports ``slicer``, and ``talk2view`` only inside a function: the module panel
uses this file before the SDK is installed, and the unit tests run without 3D Slicer.
"""

import ast
import hashlib
import importlib.util
import inspect
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import types
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("SlicerFlow")

#: Above this many tools in one chat the assistant chooses between them less reliably.
MANY_TOOLS = 30
#: What ``new_pack`` accepts: the file's name, and the template's tool name prefix.
NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
#: A loaded pack's module name in ``sys.modules`` is this plus the pack's name.
MODULE_PREFIX = "slicerflow_user_tools_"
#: SlicerFlow's built-in tool modules (``SlicerFlowLib/tools``), read from the source.
BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
#: SlicerFlow's own packs in the user's tools folder are ``slicerflow_<module>.py``.
BUILTIN_PREFIX = "slicerflow_"


class PackError(Exception):
    """A pack that breaks a rule. The message says which rule, and how to fix it."""


@dataclass
class Pack:
    """What loading one pack gave. ``tools`` is empty unless it loaded; ``missing`` maps the
    import names it needs and Slicer lacks to their pip packages (the pack is not run)."""

    name: str
    path: str
    tools: list = field(default_factory=list)
    presentation: dict = field(default_factory=dict)
    missing: dict = field(default_factory=dict)
    error: Optional[str] = None
    builtin: bool = False
    title: str = ""  # what the panel calls it: a built-in pack's TITLE, else its name

    def __post_init__(self):
        if not self.title:
            self.title = self.name


@dataclass
class BuiltinInfo:
    """A built-in pack as its source declares it, read without importing it."""

    name: str
    title: str
    tools: list
    asking: list  # the tools declared @tool(permission=True)


@dataclass
class ToolInfo:
    """One tool as its pack's source declares it. ``permission`` is the literal after
    ``permission=`` when it is True or False, else None (the pack's default applies)."""

    name: str
    summary: str = ""  # the first paragraph of its docstring, on one line
    permission: Optional[bool] = None


@dataclass
class PackInfo:
    """A pack as its source declares it, read without running it (``read_pack``)."""

    title: str = ""
    tools: list = field(default_factory=list)
    presentation: dict = field(default_factory=dict)


USER_README = """Your SlicerFlow tools.

Each .py file here is a tool pack: tools the assistant can call, written in Python with
the Talk2View SDK's @tool decorator. The slicerflow_*.py files are SlicerFlow's own packs,
written here so you can read them and change them; the other .py files are yours. Turn a
pack on or off in the SlicerFlow module panel, under "Tools"; New there writes a commented
example to start from. A pack runs inside 3D Slicer with full access to your scene and
your files, so turn on only packs you trust.

SlicerFlow's packs: SlicerFlow updates them when it updates, unless you edited one. An
edited file is yours from then on: the panel marks it "Edited", it is off until you turn
it on, and "Restore original" puts SlicerFlow's version back (yours is kept as .py.bak).
Deleting one of them only makes it come back; to stop using a pack, switch it off.

The rules:
- Put the pack's tools in TOOLS = [...]. Each needs a docstring: the assistant reads it.
  An optional TITLE = "..." is what the panel calls the pack.
- A tool asks for your approval before every call. @tool(permission=False) runs it
  without asking: use that only for a tool that reads and never changes anything.
  (SlicerFlow's own tools each say permission=True or permission=False, so an edited
  copy keeps them as they were.)
- Tools run on Slicer's main thread: Slicer stops responding until one returns.
- No "from __future__ import annotations".
- A tool's name may not repeat one of SlicerFlow's or another pack's.
- Packages Slicer does not have go in REQUIRES = {"import name": "pip package"}.

Changes load when the chat restarts.
"""

#: ``new_pack`` writes this with ``{name}`` replaced (``str.replace``, so the braces in the
#: example need no escaping).
TEMPLATE = '''"""{name}: your own SlicerFlow tools.

Each @tool function in TOOLS is a tool the assistant can call. Its signature and docstring
are what the assistant sees: the first paragraph says what the tool does, and Args says
what each argument means. Return a string: the assistant reads it. Raise an exception
(ValueError("...")) to tell the assistant what went wrong.

- A tool asks for your approval before every call. @tool(permission=False) runs it without
  asking: use that only for a tool that reads and never changes anything.
- Tools run on Slicer's main thread, so Slicer stops responding until one returns. Keep
  them quick, and never call slicer.app.processEvents().
- No "from __future__ import annotations": the tool schemas need real type annotations.
- Packages this file imports that Slicer does not have go in REQUIRES, as
  {"import name": "pip package"}; the module panel offers to install them.

Save, then press "Restart chat" under "Tools" in the SlicerFlow module panel.
"""

import slicer

from talk2view import tool

REQUIRES = {}


@tool(permission=False)
def {name}_count_volumes() -> str:
    """Count the volumes loaded in the scene."""
    count = len(slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode"))
    return "{} volume(s) loaded.".format(count)


TOOLS = [{name}_count_volumes]

#: Optional: what the chat shows while a tool runs, and the warning on its approval card
#: ({"activity": "...", "destructive": True, "warning": "..."}).
PRESENTATION = {
    "{name}_count_volumes": {"activity": "Counting volumes"},
}
'''


def scan(folder):
    """The pack names in ``folder``: its ``*.py`` files, except those whose names start with
    ``_`` or ``.``, sorted. That is the order packs load in, which decides a name clash."""
    if not os.path.isdir(folder):
        return []
    return sorted(
        entry[:-3]
        for entry in os.listdir(folder)
        if entry.endswith(".py")
        and not entry.startswith(("_", "."))
        and os.path.isfile(os.path.join(folder, entry))
    )


def pack_path(folder, name):
    return os.path.join(folder, name + ".py")


#: The failures already logged, as ``(what, path)``: the panel syncs on every refresh, and
#: a folder that refuses once refuses every time.
_warned = set()


def _warn_once(what, path, message, *args):
    """Log ``message`` the first time ``what`` fails for ``path`` in this process."""
    if (what, path) not in _warned:
        _warned.add((what, path))
        logger.warning(message, *args)


def _read(path):
    """The file's text, with universal newlines and without a UTF-8 byte order mark: an
    editor that saves either way has not changed what the file says."""
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def _write(path, text):
    """Write ``text`` whole or not at all: a half-written pack would load as a broken one."""
    temporary = path + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(temporary, path)
    except OSError:
        if os.path.isfile(temporary):
            os.remove(temporary)
        raise


def _tree(path):
    return ast.parse(_read(path), path)


def _assigned(tree, name):
    """The value node of the module-level ``name = ...``, or None."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return node.value
    return None


def digest(text):
    """The sha256 of a pack's source, as ``tools.json`` records what SlicerFlow wrote. The text
    is read by ``_read``, so a file saved with other line endings, or with a byte order
    mark, is unchanged."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def builtin_order(tools_dir):
    """``BUILTIN_PACKS`` as ``tools/__init__.py`` lists it, read from the source."""
    return list(ast.literal_eval(_assigned(_tree(os.path.join(tools_dir, "__init__.py")), "BUILTIN_PACKS")))


def builtin_sources(tools_dir):
    """``{name: source}`` of the built-in packs, in ``BUILTIN_PACKS`` order."""
    return {name: _read(os.path.join(tools_dir, name + ".py")) for name in builtin_order(tools_dir)}


def builtin_stem(name):
    """The file name, without .py, of a built-in pack in the user's tools folder."""
    return BUILTIN_PREFIX + name


@dataclass
class BuiltinFile:
    """One of SlicerFlow's packs as it stands in the user's tools folder (``sync_builtins``).
    ``edited``: the file is not what SlicerFlow wrote, so it is the user's now. ``newer``:
    edited, and SlicerFlow's own version has changed since it wrote the file."""

    name: str  # the pack's module name, "scene"
    stem: str  # its file name without .py, "slicerflow_scene"
    path: str
    edited: bool = False
    newer: bool = False


def sync_builtins(folder, state, sources):
    """Bring SlicerFlow's packs in ``folder`` up to date with ``sources`` (``builtin_sources``),
    leaving the user's edits alone. Returns ``(files, changed)``: a ``BuiltinFile`` per pack
    in ``BUILTIN_PACKS`` order, and whether ``state`` was altered (then write it).

    A file that is missing is written. A file that is still what SlicerFlow last wrote
    (``state.written``) is overwritten when SlicerFlow's version changed. A file that differs
    from both is edited: it is never touched. A file SlicerFlow has no record of is edited
    too, unless it matches SlicerFlow's version. A fresh file is governed by ``builtin_off``
    alone, so its stem leaves ``enabled``. A folder that refuses a read or a write is logged
    (once per file in this process) and skipped: the pack still loads from the extension."""
    files = []
    changed = False
    for name, source in sources.items():
        stem = builtin_stem(name)
        path = pack_path(folder, stem)
        bundled = digest(source)
        recorded = state.written.get(name)
        try:
            current = digest(_read(path))
        except FileNotFoundError:
            current = None
        except UnicodeDecodeError:
            current = ""  # no digest: not what SlicerFlow wrote, so edited
        except OSError as exc:
            _warn_once("read", path, "SlicerFlow: %s could not be read (%s)", path, exc)
            files.append(BuiltinFile(name, stem, path))
            continue
        if current is not None and current != bundled and current != recorded:
            files.append(BuiltinFile(name, stem, path, edited=True, newer=recorded != bundled))
            continue
        written = current == bundled
        if not written:
            try:
                _write(path, source)
                written = True
            except OSError as exc:
                _warn_once("write", path, "SlicerFlow: %s could not be written (%s)", path, exc)
        if written and recorded != bundled:
            state.written[name] = bundled
            changed = True
        if stem in state.enabled:
            state.enabled.discard(stem)
            changed = True
        files.append(BuiltinFile(name, stem, path))
    return files, changed


def restore_builtin(folder, state, name, source):
    """Put SlicerFlow's version of a pack back in place of the user's edited file, which is
    kept beside it as ``<stem>.py.bak`` (replacing an older one). The pack is governed by
    ``builtin_off`` again, so its stem leaves ``enabled``. Returns the pack's path; raises
    ``OSError`` when the folder refuses, and then the user's file is where it was.

    SlicerFlow's version is written beside the pack first, and only then is the user's
    file moved to ``.bak`` and SlicerFlow's moved into its place: a folder that refuses
    the write leaves everything as it was."""
    stem = builtin_stem(name)
    path = pack_path(folder, stem)
    temporary = path + ".tmp"
    moved = False
    try:
        with open(temporary, "w", encoding="utf-8") as fh:
            fh.write(source)
        if os.path.exists(path):
            os.replace(path, path + ".bak")
            moved = True
        os.replace(temporary, path)
    except OSError:
        if os.path.isfile(temporary):
            os.remove(temporary)
        if moved and not os.path.exists(path):
            os.replace(path + ".bak", path)
        raise
    state.written[name] = digest(source)
    state.enabled.discard(stem)
    return path


@dataclass
class ToolState:
    """What ``tools.json`` holds: the user's packs that are on (they are off by default), the
    built-in packs that are off (they are on by default), and for each built-in pack the
    digest of the file SlicerFlow last wrote into the tools folder (``sync_builtins``)."""

    enabled: set = field(default_factory=set)
    builtin_off: set = field(default_factory=set)
    written: dict = field(default_factory=dict)


def _names(data, key):
    names = data.get(key) if isinstance(data, dict) else None
    if not isinstance(names, list):
        return set()
    return {name for name in names if isinstance(name, str)}


def _digests(data):
    found = data.get("builtin_written") if isinstance(data, dict) else None
    if not isinstance(found, dict):
        return {}
    return {name: digest for name, digest in found.items()
            if isinstance(name, str) and isinstance(digest, str)}


def read_state(path):
    """The on/off state. A missing, unreadable or hand-broken ``tools.json`` means every user
    pack off and every built-in pack on; it never stops the chat."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return ToolState()
    except (OSError, ValueError) as exc:
        logger.warning("SlicerFlow: %s could not be read, so your tool packs are off and the "
                       "built-in ones on (%s)", path, exc)
        return ToolState()
    return ToolState(_names(data, "enabled"), _names(data, "builtin_off"), _digests(data))


def write_state(path, state):
    """Save the on/off state, whole or not at all. Raises ``OSError`` when the file cannot
    be replaced: for what the user just did, which the panel says failed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write(path, json.dumps({"enabled": sorted(state.enabled),
                             "builtin_off": sorted(state.builtin_off),
                             "builtin_written": dict(sorted(state.written.items()))}, indent=2))


def save_state(path, state):
    """``write_state`` for what SlicerFlow records by itself (``sync_builtins``): a file that
    cannot be replaced is logged and left as it is, so it stops neither the chat nor the
    module panel. The record is made again next time."""
    try:
        write_state(path, state)
    except OSError as exc:
        _warn_once("state", path, "SlicerFlow: %s could not be saved (%s)", path, exc)


def ensure_user_dir(folder):
    """The folder, made if need be, with SlicerFlow's README.txt in it, kept current. A
    README the folder refuses to take is logged, never raised: the chat starts regardless."""
    os.makedirs(folder, exist_ok=True)
    readme = os.path.join(folder, "README.txt")
    try:
        current = _read(readme)
    except (OSError, ValueError):
        current = None
    if current != USER_README:
        try:
            _write(readme, USER_README)
        except OSError as exc:
            _warn_once("write", readme, "SlicerFlow: %s could not be written (%s)", readme, exc)
    return folder


def new_pack(folder, name):
    """Write the template as ``<name>.py`` and return its path."""
    if not NAME.fullmatch(name):
        raise ValueError(
            "A tool pack's name is lowercase letters, digits and _, starting with a letter, "
            "at most 40 characters: {!r} is not.".format(name)
        )
    if name.startswith(BUILTIN_PREFIX):
        raise ValueError("Names starting with {} are SlicerFlow's own packs: choose another.".format(
            BUILTIN_PREFIX))
    path = pack_path(ensure_user_dir(folder), name)
    if os.path.exists(path):
        raise FileExistsError("You already have a tool pack named {}.".format(name))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(TEMPLATE.replace("{name}", name))
    return path


def import_pack(folder, source, replace=False):
    """Copy ``source``, a .py file, into ``folder`` and return the pack's name."""
    filename = os.path.basename(source)
    name, extension = os.path.splitext(filename)
    if extension != ".py":
        raise ValueError("A tool pack is a .py file, and {} is not.".format(filename))
    if name.startswith(("_", ".")):
        raise ValueError("A tool pack's name may not start with _ or . ({}).".format(filename))
    target = pack_path(ensure_user_dir(folder), name)
    if os.path.exists(target) and not replace:
        raise FileExistsError("You already have a tool pack named {}.".format(name))
    shutil.copyfile(source, target)
    return name


def requires(path):
    """The pack's ``REQUIRES``, read from its source without running it: a pack that imports
    a missing package at the top would otherwise fail before it could say what it needs.
    Raises ``PackError`` for what the source alone shows is wrong."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    found = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__" and any(
            alias.name == "annotations" for alias in node.names
        ):
            raise PackError(
                "Remove 'from __future__ import annotations': the tool schemas are built "
                "from real type annotations."
            )
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "REQUIRES" for target in node.targets
        ):
            try:
                found = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError):
                found = None
            if not isinstance(found, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in found.items()
            ):
                raise PackError(
                    'REQUIRES must be a dict like {"skimage": "scikit-image"}: '
                    "import name, then pip package."
                )
            # Each value goes to pip as one argument: a package, never an option.
            for package in found.values():
                if not package.strip() or package.strip().startswith("-") or len(package.split()) > 1:
                    raise PackError(
                        "REQUIRES names pip packages only, one per entry: {!r} is not "
                        "one.".format(package)
                    )
    return found


def missing_packages(required, find_spec=importlib.util.find_spec):
    """The entries of ``required`` whose import name cannot be found."""
    missing = {}
    for module, package in required.items():
        try:
            found = find_spec(module) is not None
        except (ImportError, ValueError):  # a missing parent package, or a bad name
            found = False
        if not found:
            missing[module] = package
    return missing


def check_requires(path):
    """``(missing, problem)``: the packages the pack needs and Slicer lacks, and why its
    source could not be read (None when it could). Runs none of the pack's code."""
    try:
        return missing_packages(requires(path)), None
    except (OSError, SyntaxError, ValueError, PackError) as exc:
        return {}, _describe(exc)


def load_packs(folder, enabled, reserved, skip=()):
    """Load the packs in ``folder`` that are in ``enabled``, in name order. ``reserved`` maps
    the tool names already taken to their owner; each loaded pack's names join it, so a
    later pack cannot reuse an earlier one's. A name in ``enabled`` with no file is skipped,
    and so is a name in ``skip`` (SlicerFlow's own files, which the caller loads itself)."""
    taken = dict(reserved)
    packs = []
    for name in scan(folder):
        if name not in enabled or name in skip:
            continue
        pack = load_pack(name, pack_path(folder, name), taken)
        taken.update((tool.name, "the tool pack " + name) for tool in pack.tools)
        packs.append(pack)
    return packs


def load_pack(name, path, reserved):
    """Load one pack. Never raises for anything in the pack: its problem goes in
    ``Pack.error``, and a pack missing a package (``Pack.missing``) is not run at all."""
    pack = Pack(name, path)
    pack.missing, pack.error = check_requires(path)
    if pack.error or pack.missing:
        return pack
    try:
        module = _run(name, path)
    except (Exception, SystemExit) as exc:
        # The user's code: whatever it raises is that pack's problem, never the chat's.
        # SystemExit too, so a pack calling sys.exit() cannot stop the chat from starting.
        pack.error = _describe(exc)
        return pack
    try:
        pack.tools, pack.presentation = _check(module, reserved)
    except PackError as exc:
        pack.error = str(exc)
    except Exception as exc:
        # Reading the pack's attributes runs its code too (a module __getattr__, a
        # property): that is still this pack's problem, never the chat's.
        pack.error = _describe(exc)
    return pack


def builtin_pack(name, module, reserved, path=""):
    """The ``Pack`` for one of SlicerFlow's built-in modules (``tools.MODULES``): the same
    checks a user's pack gets, except that its tools keep their own ``permission``. The
    module is imported once for the whole Slicer process, so a tool made to ask here would
    ask for the rest of the session. ``path`` is the pack's file in the user's tools folder
    (``sync_builtins``), what the module panel shows and opens. A failure is a bug in
    SlicerFlow; it is kept like a user pack's, for the panel to show."""
    pack = Pack(name, path or getattr(module, "__file__", "") or "", builtin=True,
                title=getattr(module, "TITLE", "") or name)
    try:
        pack.tools, pack.presentation = _check(module, reserved, ask_by_default=False)
    except PackError as exc:
        pack.error = str(exc)
    return pack


def pack_status(enabled, missing, pack=None, problem=None):
    """``(kind, text)`` for the module panel's Status column: what the user can expect of a
    pack. ``kind`` is "error", "needs", "off", "pending" (on, not loaded yet) or "on", for
    the colour. ``missing`` and ``problem`` are ``check_requires``'s; ``pack`` is what the
    running chat made of it (None when no chat runs, or it was turned on since)."""
    if problem:
        return "error", "Error: " + problem
    if missing:
        return "needs", "Needs " + ", ".join(sorted(missing.values()))
    if not enabled:
        return "off", "Off"
    if pack is None:
        return "pending", "Loads on next start"
    if pack.error:
        return "error", "Error: " + pack.error
    return "on", _on_count(len(pack.tools))


def _literal(tree, name):
    node = _assigned(tree, name)
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return None


def _permission(function):
    for decorator in function.decorator_list:
        if isinstance(decorator, ast.Call):
            for keyword in decorator.keywords:
                if (keyword.arg == "permission" and isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, bool)):
                    return keyword.value.value
    return None


def _summary(docstring):
    paragraph = (docstring or "").strip().split("\n\n")[0]
    return " ".join(paragraph.split())


def read_pack(path):
    """What a pack's source declares, without running it: ``TITLE``, the ``TOOLS`` names with
    each one's docstring summary and ``permission``, and ``PRESENTATION``. Only what is
    written plainly counts (a name in ``TOOLS``, a literal dict); the rest is left out, and
    a file that cannot be read or parsed gives an empty ``PackInfo`` (``check_requires``
    says why). The module panel shows this for every pack, on or off."""
    try:
        tree = _tree(path)
    except (OSError, SyntaxError, ValueError):
        return PackInfo()
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    title = _literal(tree, "TITLE")
    presentation = _literal(tree, "PRESENTATION")
    listed = _assigned(tree, "TOOLS")
    names = ([element.id for element in listed.elts if isinstance(element, ast.Name)]
             if isinstance(listed, (ast.List, ast.Tuple)) else [])
    tools = []
    for name in names:
        function = functions.get(name)
        if function is None:
            tools.append(ToolInfo(name))
        else:
            tools.append(ToolInfo(name, _summary(ast.get_docstring(function)), _permission(function)))
    return PackInfo(title if isinstance(title, str) else "", tools,
                    presentation if isinstance(presentation, dict) else {})


def read_builtin(tools_dir):
    """The built-in packs in ``BUILTIN_PACKS`` order, read from their source (``read_pack``):
    the module panel lists them before the Talk2View SDK (which the modules import) is
    installed. A module without ``TITLE`` or ``TOOLS`` is a bug in SlicerFlow."""
    infos = []
    for name in builtin_order(tools_dir):
        info = read_pack(os.path.join(tools_dir, name + ".py"))
        if not info.title or not info.tools:
            raise ValueError("the built-in tool module {} has no TITLE or TOOLS".format(name))
        infos.append(BuiltinInfo(name, info.title, [tool.name for tool in info.tools],
                                 [tool.name for tool in info.tools if tool.permission is True]))
    return infos


def builtin_status(on, info):
    """``(kind, text)`` for an unedited built-in pack's Status column, from its source
    (``read_pack``). The Pack column says it is SlicerFlow's, and the panel's tool list
    which of its tools ask first."""
    if not on:
        return "off", "Off"
    return "on", _on_count(len(info.tools))


def _on_count(tools):
    """A loaded pack's Status text: "On · 4 tools", "On · 1 tool"."""
    return "On · {} tool{}".format(tools, "" if tools == 1 else "s")


def _run(name, path):
    """Run the pack's source as a new module. It is compiled from the file every time,
    never from a cached .pyc: an edit saved within the same second as the last load would
    otherwise load the old code. It also leaves no __pycache__ in the user's folder."""
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    module = types.ModuleType(MODULE_PREFIX + name)
    module.__file__ = path
    # dataclasses and pickle look a class's module up in sys.modules.
    sys.modules[module.__name__] = module
    try:
        exec(compile(source, path, "exec"), module.__dict__)
    except BaseException:
        del sys.modules[module.__name__]
        raise
    return module


def _check(module, reserved, ask_by_default=True):
    """``(tools, presentation)`` from a pack that ran, or ``PackError``. A tool without
    ``permission=`` is made to ask first. That happens only after every check passed, so a
    pack that fails changes nothing. (The ``__future__`` import is refused earlier, by
    ``requires``, before the SDK sees string annotations.) ``ask_by_default`` False (a
    built-in pack) leaves every tool's permission as its module declared it."""
    tools = getattr(module, "TOOLS", None)
    if not isinstance(tools, (list, tuple)) or not tools:
        raise PackError("No TOOLS list: put the pack's tools in TOOLS = [...].")
    names = []
    for item in tools:
        schema = getattr(item, "_t2v_tool_schema", None)
        if schema is None:
            raise PackError("{!r} in TOOLS is not decorated with @tool.".format(
                getattr(item, "__name__", item)))
        # The handler's own docstring, not ``schema.description``: the SDK falls back to the
        # function's name there, which tells the assistant nothing.
        if not (inspect.getdoc(getattr(item, "_t2v_handler", item)) or "").strip():
            raise PackError("{} has no docstring: the assistant reads it to know what the "
                            "tool does.".format(schema.name))
        if schema.name in reserved:
            raise PackError("{} is already a tool of {}: rename yours.".format(
                schema.name, reserved[schema.name]))
        if schema.name in names:
            raise PackError("{} is in TOOLS twice.".format(schema.name))
        permission = getattr(item, "_t2v_permission", None)
        # @tool stores whatever it is given; the SDK's handle() refuses it only when the
        # chat starts, and that refusal would stop the whole chat, not just this pack.
        if not (permission is None or isinstance(permission, bool) or callable(permission)):
            raise PackError(
                "{} has permission={!r}: use True, False, or a function "
                "(tool_name, args) -> PermissionResult.".format(schema.name, permission)
            )
        names.append(schema.name)
    presentation = getattr(module, "PRESENTATION", {})
    if not isinstance(presentation, dict):
        raise PackError("PRESENTATION must be a dict: {tool name: {...}}.")
    # Imported here: the module panel uses this file before the SDK is installed.
    from talk2view.ui import ToolPresentation

    for tool_name, spec in presentation.items():
        if tool_name not in names:
            raise PackError("PRESENTATION names {}, which is not in TOOLS.".format(tool_name))
        try:
            ToolPresentation(**spec)
        except (TypeError, ValueError) as exc:
            raise PackError("PRESENTATION for {}: {}".format(tool_name, exc))
    if ask_by_default:
        for item in tools:
            if getattr(item, "_t2v_permission", None) is None:
                item._t2v_permission = True
    return list(tools), dict(presentation)


def _describe(exc):
    if isinstance(exc, SyntaxError):
        return "SyntaxError on line {}: {}".format(exc.lineno, exc.msg)
    if isinstance(exc, PackError):
        return str(exc)
    return "{}: {}".format(type(exc).__name__, exc)


def open_in_editor(path, platform=sys.platform, run=subprocess.Popen, startfile=None):
    """Open ``path`` in a text editor, never running it. A .py file's default action can be
    to run it (the Python Launcher on macOS, py.exe on Windows), so this never asks the
    desktop to "open" the file: macOS ``open -t`` (the default text editor), Windows the
    "edit" verb where an editor registered one, and Notepad otherwise (usually: the
    python.org installer registers "editwithidle", not "edit"), elsewhere ``xdg-open``,
    whose default for a .py file is a text editor on the common desktops.
    Raises ``OSError`` when the desktop's command is missing."""
    if platform == "darwin":
        run(["open", "-t", path])
    elif platform == "win32":
        try:
            (startfile or os.startfile)(path, "edit")
        except OSError:
            run(["notepad", path])
    else:
        run(["xdg-open", path])


def show_in_folder(path, platform=sys.platform, run=subprocess.Popen):
    """Show ``path`` selected in the desktop's file manager (macOS, Windows), or open its
    folder (elsewhere). Raises ``OSError`` when the desktop's command is missing."""
    if platform == "darwin":
        run(["open", "-R", path])
    elif platform == "win32":
        run(["explorer", "/select,", path])
    else:
        run(["xdg-open", os.path.dirname(path)])
