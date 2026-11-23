"""SlicerFlow's skills wiring: bundled skills in the spec layout, the user's folder,
and the import picker."""

import os

from talk2view.skill_format import parse, validate
from SlicerFlowLib import skills


def test_bundled_skills_follow_the_spec():
    names = sorted(os.listdir(skills.BUNDLED_DIR))
    assert names == ["ct-window-presets", "explore-sample-data", "measure-and-report-volumes",
                     "segment-by-threshold"]
    for name in names:
        text = open(os.path.join(skills.BUNDLED_DIR, name, "SKILL.md"), encoding="utf-8").read()
        doc, problems = parse(text)
        assert problems == [] and validate(doc, folder=name) == [], name
        assert "Use when" in doc.description, name  # what + when (best practices)
        assert not doc.description.lower().startswith(("i ", "you ")), name


def test_user_readme_points_to_the_skills_view(tmp_path):
    folder = skills.ensure_user_dir(str(tmp_path / "skills"))
    readme = open(os.path.join(folder, "README.txt"), encoding="utf-8").read()
    assert "Skills" in readme and "<name>/SKILL.md" in readme


def test_pick_import_returns_the_dialog_choice(monkeypatch):
    monkeypatch.setattr(skills, "_dialog", lambda: "/tmp/ct-helper")
    assert skills.pick_import() == "/tmp/ct-helper"
    monkeypatch.setattr(skills, "_dialog", lambda: "")
    assert skills.pick_import() is None
