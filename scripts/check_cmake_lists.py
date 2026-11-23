#!/usr/bin/env python3
"""Check that SlicerFlow/CMakeLists.txt installs exactly the module's files.

An extension build installs only what ``MODULE_PYTHON_SCRIPTS`` and
``MODULE_PYTHON_RESOURCES`` list, so a file that is on disk but not listed works in a
developer checkout and is missing from the packaged extension. This script fails when:

- a ``.py`` file under ``SlicerFlow/SlicerFlowLib/`` (or ``SlicerFlow.py``) is not in
  ``MODULE_PYTHON_SCRIPTS``;
- a file under ``SlicerFlow/Resources/`` is not in ``MODULE_PYTHON_RESOURCES``;
- a listed file does not exist.

``${MODULE_NAME}`` is expanded, and so is a variable filled by
``file(GLOB <var> RELATIVE ${CMAKE_CURRENT_SOURCE_DIR} <patterns>)``. Standard library only.

    python3 scripts/check_cmake_lists.py [<repo>]      exit 0: OK, 1: mismatch
"""

import glob
import os
import re
import sys

IGNORED_NAMES = {".DS_Store", ".gitignore"}


def _block(text, name):
    match = re.search(r"set\(\s*" + re.escape(name) + r"\s+(.*?)\)", text, re.DOTALL)
    if match is None:
        raise ValueError("no set({} ...) in CMakeLists.txt".format(name))
    return match.group(1).split()


def _globs(text, module_dir):
    found = {}
    pattern = r"file\(\s*GLOB\s+(\w+)\s+RELATIVE\s+\$\{CMAKE_CURRENT_SOURCE_DIR\}\s+(.*?)\)"
    for var, patterns in re.findall(pattern, text, re.DOTALL):
        files = []
        for pat in patterns.split():
            files.extend(
                os.path.relpath(path, module_dir).replace(os.sep, "/")
                for path in glob.glob(os.path.join(module_dir, pat))
            )
        found[var] = sorted(files)
    return found


def listed(module_dir):
    """(scripts, resources) as CMakeLists.txt lists them, relative to ``module_dir``."""
    with open(os.path.join(module_dir, "CMakeLists.txt"), encoding="utf-8") as fh:
        text = fh.read()
    name = re.search(r"set\(\s*MODULE_NAME\s+(\S+?)\s*\)", text).group(1)
    globs = _globs(text, module_dir)

    def expand(tokens):
        out = []
        for token in tokens:
            var = re.fullmatch(r"\$\{(\w+)\}", token)
            if var and var.group(1) in globs:
                out.extend(globs[var.group(1)])
            else:
                out.append(token.replace("${MODULE_NAME}", name))
        return set(out)

    return expand(_block(text, "MODULE_PYTHON_SCRIPTS")), expand(_block(text, "MODULE_PYTHON_RESOURCES"))


def on_disk(module_dir):
    """(scripts, resources) that exist, relative to ``module_dir``."""
    scripts, resources = set(), set()
    for root, dirs, files in os.walk(module_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        rel_root = os.path.relpath(root, module_dir).replace(os.sep, "/")
        for file_name in files:
            if file_name in IGNORED_NAMES or file_name.endswith(".pyc"):
                continue
            rel = file_name if rel_root == "." else rel_root + "/" + file_name
            if rel.startswith("Resources/"):
                resources.add(rel)
            elif rel.endswith(".py") and (rel.startswith("SlicerFlowLib/") or "/" not in rel):
                scripts.add(rel)
    return scripts, resources


def problems(repo):
    module_dir = os.path.join(repo, "SlicerFlow")
    listed_scripts, listed_resources = listed(module_dir)
    disk_scripts, disk_resources = on_disk(module_dir)
    found = []
    for rel in sorted(disk_scripts - listed_scripts):
        found.append("not in MODULE_PYTHON_SCRIPTS: " + rel)
    for rel in sorted(disk_resources - listed_resources):
        found.append("not in MODULE_PYTHON_RESOURCES: " + rel)
    for rel in sorted((listed_scripts | listed_resources) - (disk_scripts | disk_resources)):
        found.append("listed but missing: " + rel)
    return found


def main(argv):
    repo = os.path.abspath(argv[1] if len(argv) > 1 else os.path.join(os.path.dirname(__file__), ".."))
    found = problems(repo)
    for line in found:
        print(line)
    print("OK" if not found else "{} problem(s)".format(len(found)))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
