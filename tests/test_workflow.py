"""The GitHub Actions workflow, read as text (it never runs here: the repo has no remote)."""

import os
import re

from conftest import REPO

WORKFLOW = os.path.join(REPO, ".github", "workflows", "tests.yml")


def _workflow():
    with open(WORKFLOW, encoding="utf-8") as fh:
        return fh.read()


def test_every_env_key_is_used():
    """A key nobody reads looks like a pin and holds nothing: the Slicer version was one,
    while the archive names and their hashes were written out in the matrix."""
    text = _workflow()
    block = re.search(r"^env:\n((?:(?:  \w+:.*|  #.*|)\n)+)", text, re.MULTILINE)
    assert block, "no top-level env: block in the workflow"
    names = re.findall(r"^  (\w+):", block.group(1), re.MULTILINE)
    assert names, "the env: block defines nothing"
    for name in names:
        assert "env." + name in text, "env.{} is defined and never used".format(name)
