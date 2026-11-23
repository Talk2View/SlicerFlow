"""An install from before ``data_storage`` had a file of its own
still has its preferences (and, defensively, any skills a host ever saved with
``t2v.skills.save()`` -- SlicerFlow's own skills are local folders, rescanned fresh on
every start, and never go through that call, so this half is only proven at the file
level, not through a read only a different kind of host would ever make) in the sign-in
file (``auth.json``). ``Runtime._start`` must move both keys into ``conversations.json``
before anything reads either file, so the end-user's font size survives the upgrade
instead of reading back as the default.

Its own ``SLICERFLOW_HOME``: every other in-Slicer test shares one for the whole run
(``run_slicer_tests.py``), and this test writes directly into ``auth.json`` before
starting a ``Runtime`` -- a path no other test needs to take, and one that must not read
back whatever an unrelated test already left in that shared file.
"""

import json
import os
import tempfile
import unittest

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)

from fake_engine import FakeEngine
from SlicerFlowLib import lifecycle, paths


class StorageMigrationTest(unittest.TestCase):
    def setUp(self):
        saved_home = os.environ.get("SLICERFLOW_HOME")
        os.environ["SLICERFLOW_HOME"] = tempfile.mkdtemp(prefix="sf-migration-home-")
        self.addCleanup(
            lambda: os.environ.__setitem__("SLICERFLOW_HOME", saved_home) if saved_home is not None
            else os.environ.pop("SLICERFLOW_HOME", None)
        )
        self.engine = FakeEngine().start()
        self.addCleanup(self.engine.stop)
        self.addCleanup(lifecycle.stop)

    def _write_old_style_auth_file(self):
        """As an install from before this upgrade left it: preferences (a real SlicerFlow
        scenario -- the page's Settings view sets font size through ``t2v.preferences``)
        and skills (synthetic here, for the migration's own sake -- see the module
        docstring) under the sign-in file's own keys, next to a token."""
        os.makedirs(os.path.dirname(paths.token_file()), exist_ok=True)
        with open(paths.token_file(), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "access_token": "tok-old",
                    "preferences": json.dumps({"fontSize": "large"}),
                    "t2v_user_skills": json.dumps(
                        [{"name": "old-skill", "content": "be terse", "enabled": True}]
                    ),
                },
                f,
            )

    def _start(self):
        from SlicerFlowLib.runtime import Runtime

        runtime = Runtime("pk_test_migration", self.engine.url, lambda url: None)
        lifecycle.start(runtime)
        return runtime

    def test_an_old_installs_preferences_survive_the_upgrade_and_skills_move_too(self):
        self._write_old_style_auth_file()

        runtime = self._start()

        # Preferences: read correctly through the SDK, not just moved as bytes --
        # T2VPreferences.get() reads data_storage live, on every call.
        self.assertEqual(runtime.t2v.preferences.get().font_size, "large")

        lifecycle.stop()
        with open(paths.conversations_file(), encoding="utf-8") as f:
            data_raw = f.read()
        self.assertIn("large", data_raw)
        self.assertIn("old-skill", data_raw)  # moved at the file level (see module docstring)
        with open(paths.token_file(), encoding="utf-8") as f:
            auth_raw = json.load(f)
        self.assertNotIn("preferences", auth_raw)
        self.assertNotIn("t2v_user_skills", auth_raw)
        self.assertEqual(auth_raw.get("access_token"), "tok-old")  # untouched

    def test_a_fresh_install_with_nothing_to_migrate_starts_cleanly(self):
        runtime = self._start()
        self.assertIsNone(runtime.t2v.preferences.get().font_size)
