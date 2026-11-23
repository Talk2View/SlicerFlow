"""Tests that run without 3D Slicer (``scripts/unit-tests.sh``).

They import ``SlicerFlowLib`` modules that never import ``slicer`` or ``qt`` at import
time, and the scripts in ``scripts/``.
"""

import ast
import glob
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (os.path.join(REPO, "SlicerFlow"), os.path.join(REPO, "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

TOOLS_DIR = os.path.join(REPO, "SlicerFlow", "SlicerFlowLib", "tools")
#: ``load_sample_data`` downloads on Slicer's main thread, so nothing repaints, Stop cannot
#: be pressed and there is no progress until it is done. The approval card, the model and
#: the README all have to say so, in these words.
FREEZE_WARNING = "stops responding while it downloads"


def _module(path):
    with open(path, encoding="utf-8") as fh:
        return ast.parse(fh.read(), path)


def tool_functions():
    """``{name: ast.FunctionDef}`` for every tool function, read from the source."""
    found = {}
    for path in sorted(glob.glob(os.path.join(TOOLS_DIR, "*.py"))):
        for node in _module(path).body:
            if isinstance(node, ast.FunctionDef) and any(
                (isinstance(d, ast.Call) and getattr(d.func, "id", None) == "tool")
                or getattr(d, "id", None) == "tool"
                for d in node.decorator_list
            ):
                found[node.name] = node
    return found


def tools_that_ask_first():
    """The tools decorated ``@tool(permission=True)``."""
    return {
        name
        for name, node in tool_functions().items()
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and any(
            keyword.arg == "permission" and getattr(keyword.value, "value", None) is True
            for keyword in decorator.keywords
        )
    }


def presentation():
    """Every built-in pack's ``PRESENTATION``, merged and read from the source: it is
    literals all the way down, so the tests that check what the approval card says need no
    3D Slicer."""
    from SlicerFlowLib import user_tools

    merged = {}
    for info in user_tools.read_builtin(TOOLS_DIR):
        for node in _module(os.path.join(TOOLS_DIR, info.name + ".py")).body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "PRESENTATION":
                merged.update(ast.literal_eval(node.value))
    return merged
