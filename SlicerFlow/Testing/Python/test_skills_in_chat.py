"""A running chat loads the bundled skills, and rescans when the page opens its list."""

import json
import os
import unittest
import urllib.request
import uuid

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)

from sf_testing import UNREACHABLE_ENGINE, wait_until
from SlicerFlowLib import lifecycle, paths, skills

BUNDLED = {"ct-window-presets", "explore-sample-data", "measure-and-report-volumes", "segment-by-threshold"}


def _command(runtime, body):
    token = runtime.server.url.split("#t=", 1)[1]
    request = urllib.request.Request(
        "http://127.0.0.1:{}/command".format(runtime.server.port),
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status


class SkillsInChatTest(unittest.TestCase):
    def tearDown(self):
        lifecycle.stop()

    def _labels(self, runtime):
        # A bundled skill the user's own copy replaces stays listed, flagged "overridden"
        # (the page's "Replaced by yours"); it is not the one in effect, so it is left out.
        return {
            entry["name"]: entry["label"]
            for entry in runtime.t2v.skills.state()["local"]
            if not entry.get("overridden")
        }

    def _replaced(self, runtime):
        return {
            entry["name"]
            for entry in runtime.t2v.skills.state()["local"]
            if entry.get("overridden")
        }

    def test_bundled_skills_load_and_the_skills_list_rescans_the_user_folder(self):
        from SlicerFlowLib.runtime import Runtime

        runtime = Runtime("pk_test_skills", UNREACHABLE_ENGINE, lambda url: None)
        lifecycle.start(runtime)
        self.assertEqual(set(self._labels(runtime)), BUNDLED)
        folder = skills.ensure_user_dir()
        self.assertEqual(folder, paths.user_skills_dir())
        with open(os.path.join(folder, "ct-window-presets.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: ct-window-presets\ndescription: mine\n---\n# Mine\n")
        self.addCleanup(os.remove, os.path.join(folder, "ct-window-presets.md"))
        self.assertEqual(_command(runtime, {"id": str(uuid.uuid4()), "type": "skillsList"}), 202)
        # "user": the label talk2view.skills gives an entry from the SDK's own
        # FolderSkillStore, layered over the bundled folders (talk2view.skills.rescan).
        wait_until(lambda: self._labels(runtime).get("ct-window-presets") == "user",
                   what="the rescan to pick up the user's skill")
        self.assertEqual(self._replaced(runtime), {"ct-window-presets"})
