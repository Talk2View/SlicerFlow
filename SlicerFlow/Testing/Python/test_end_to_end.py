"""The whole chat inside Slicer against a scripted engine, driven through the page's wire.

Sign in, ask; the engine calls set_layout (it runs on Slicer's main thread) and then
capture_view, which waits for the approval the test gives as the page would. After the
reply, the screenshot is uploaded and sent in the follow-up message, which the engine
answers. Last, the page opens its skills list, which rescans the skill folders.
"""

import json
import os
import tempfile
import textwrap
import unittest
import urllib.request
import uuid

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)
import slicer

from fake_engine import DONE, STOP, FakeEngine, text, tool_call
from sf_testing import make_volume, wait_until
from talk2view import Decision
from SlicerFlowLib import lifecycle
from SlicerFlowLib.tools import TOOLS

#: The skills the extension ships in Resources/Skills.
BUNDLED = {"ct-window-presets", "explore-sample-data", "measure-and-report-volumes",
           "segment-by-threshold"}
#: The label talk2view.skills gives a bundled-folder entry (talk2view.skills.rescan).
BUNDLED_LABEL = "bundled"
#: A user's tool pack (``SlicerFlowLib.user_tools``) with one tool that asks first.
USER_PACK = textwrap.dedent('''
    import slicer
    from talk2view import tool


    @tool
    def add_named_point(name: str) -> str:
        """Add a markup point list with one point at the origin.

        Args:
            name: The point list's name.
        """
        node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", name)
        node.AddControlPoint(0, 0, 0)
        return "Added " + name


    TOOLS = [add_named_point]
''')


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        slicer.mrmlScene.Clear(0)
        make_volume("CT")
        slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutConventionalView)
        # This class's methods share one fake engine identity ("user-1"), and the chat
        # list restores the newest stored conversation whenever a fresh Runtime starts
        # with nothing on screen -- so without a SLICERFLOW_HOME of its own, a later
        # test method here would pick up an earlier one's real, still-on-disk
        # conversation instead of starting clean. Deleting conversations.json and
        # auth.json between tests would now work too (talk2view.storage.FileStorage's
        # cache is validated against the file's stat, so a
        # missing file is noticed), but a path neither test has touched isolates the
        # rest of SLICERFLOW_HOME as well -- metrics.jsonl, the lock file -- for one
        # mkdtemp and no extra teardown.
        saved_home = os.environ.get("SLICERFLOW_HOME")
        os.environ["SLICERFLOW_HOME"] = tempfile.mkdtemp(prefix="sf-e2e-home-")
        self.addCleanup(
            lambda: os.environ.__setitem__("SLICERFLOW_HOME", saved_home) if saved_home is not None
            else os.environ.pop("SLICERFLOW_HOME", None)
        )
        self.engine = FakeEngine().start()
        self.addCleanup(self.engine.stop)
        self.addCleanup(lifecycle.stop)
        from SlicerFlowLib.runtime import Runtime

        self.runtime = Runtime("pk_test_e2e", self.engine.url, lambda url: None)
        lifecycle.start(self.runtime)

    def command(self, type_, **fields):
        body = dict(fields, id=str(uuid.uuid4()), type=type_)
        token = self.runtime.server.url.split("#t=", 1)[1]
        request = urllib.request.Request(
            "http://127.0.0.1:{}/command".format(self.runtime.server.port),
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 202)

    def state(self):
        return self.runtime.controller.snapshot()

    def sign_in(self):
        self.command("login", email="user@example.com", password="secret")
        # The same 20 s the chat waits get: this one also absorbs the tail of Runtime.start()
        # (the UI server, the registration thread, the first config fetch), and on a heavily
        # loaded machine the default 10 s was not enough for that.
        wait_until(lambda: self.runtime.t2v.auth.is_authenticated(), timeout_s=20,
                   what="the sign-in")

    def approval(self):
        return self.state()["pendingApproval"] or {}

    def test_tools_approval_and_the_screenshot_follow_up(self):
        self.engine.script(
            [tool_call("set_layout", "tc-1", {"layout": "four-up"})],
            [tool_call("capture_view", "tc-2", {"view": "3d"})],
            [text("Four-up it is."), STOP, DONE],
            [text("I can see the 3D view."), STOP, DONE],
        )
        self.sign_in()

        self.command("send", text="Switch to four-up and look at the 3D view", attachmentIds=[])
        wait_until(lambda: self.state()["pendingApproval"] is not None, timeout_s=20,
                   what="the capture_view approval")
        approval = self.state()["pendingApproval"]
        self.assertEqual(approval["toolName"], "capture_view")
        self.assertEqual(slicer.app.layoutManager().layout, slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)

        self.command("approve", toolCallId=approval["toolCallId"], action="once")

        def replied():
            state = self.state()
            last = state["messages"][-1]
            return not state["isLoading"] and last["content"] == "I can see the 3D view."

        wait_until(replied, timeout_s=20, what="the reply to the screenshot follow-up")
        messages = self.state()["messages"]
        users = [m for m in messages if m["role"] == "user"]
        assistants = [m for m in messages if m["role"] == "assistant"]
        self.assertEqual(users[0]["content"], "Switch to four-up and look at the 3D view")
        self.assertTrue(users[1]["content"].startswith("(Sent by SlicerFlow)"))
        # Numbered: two screenshots of one view in a reply would otherwise share a filename.
        self.assertEqual([a["filename"] for a in users[1]["attachments"]], ["3d-view-1.png"])
        steps = [step["name"] for m in assistants for step in m.get("steps", [])]
        self.assertEqual(steps, ["set_layout", "capture_view"])
        self.assertIn("Four-up it is.", [m["content"] for m in assistants])
        self.assertIsNone(self.state()["error"])

        # The engine hears the tools from the registration thread, or from the
        # re-registration the first session triggers: whichever gets there first.
        # save_skill is the SDK's own builtin, added because the Skills view is wired up
        # (Runtime always gives UIServer a skills_dir).
        wait_until(lambda: self.engine.tools, what="the tool registration")
        self.assertEqual(set(self.engine.tools), {tool.name for tool in TOOLS} | {"save_skill"})
        resumes = self.engine.bodies("/resume")
        self.assertEqual([r["tool_call_id"] for r in resumes], ["tc-1", "tc-2"])
        self.assertEqual(resumes[0]["result"], "Layout set to four-up.")
        self.assertFalse(resumes[1]["is_error"])
        self.assertTrue(self.engine.uploads, "the screenshot never reached the engine")
        upload = self.engine.uploads[0]
        self.assertEqual((upload["filename"], upload["mime_type"]), ("3d-view-1.png", "image/png"))
        self.assertTrue(upload["data"].startswith(b"\x89PNG"))
        follow_up = self.engine.bodies("/messages")[-1]["messages"][-1]
        self.assertEqual(follow_up["role"], "user")
        self.assertEqual(follow_up["content"][1], {"type": "attachment", "attachment_id": upload["id"]})

        # Opening the skills list rescans the skill folders on the router thread, and the
        # page reads what it found from the skills slice of the chat state.
        self.command("skillsList")
        wait_until(lambda: self._bundled_skills() == BUNDLED, what="the bundled skills")

    def test_a_destructive_tool_never_becomes_always_allowed(self):
        """"Always allow" for `capture_view` must not buy silent slice-view uploads for the
        rest of the conversation. The chat page does not draw the button, but the page is
        not the gate: the decision arrives over a localhost command anything on this
        computer can post, and the page itself may be replaced. So the second call must ask
        again.
        """
        self.engine.script(
            [tool_call("capture_view", "tc-1", {"view": "3d"})],
            [tool_call("capture_view", "tc-2", {"view": "3d"})],
            [text("Both of them."), STOP, DONE],
            [text("I can see them."), STOP, DONE],  # the screenshot follow-up's own turn
        )
        self.sign_in()
        self.command("send", text="Take two screenshots of the 3D view", attachmentIds=[])
        wait_until(lambda: self.approval().get("toolCallId") == "tc-1", timeout_s=20,
                   what="the first capture_view approval")

        self.command("approve", toolCallId="tc-1", action="always")

        wait_until(lambda: self.approval().get("toolCallId") == "tc-2", timeout_s=20,
                   what="the second capture_view approval")
        self.assertEqual(self.approval()["toolName"], "capture_view")
        self.assertEqual(self.state()["alwaysAllowedTools"], [])
        self.command("approve", toolCallId="tc-2", action="deny")
        wait_until(lambda: not self.state()["isLoading"], timeout_s=20, what="the run to end")
        # The first call was allowed once, so exactly one screenshot was taken and uploaded.
        self.assertEqual([r["tool_call_id"] for r in self.engine.bodies("/resume")],
                         ["tc-1", "tc-2"])
        wait_until(lambda: self.engine.uploads, timeout_s=20, what="the screenshot upload")
        self.assertEqual(len(self.engine.uploads), 1)

    def test_load_file_asks_every_time_too(self):
        """No tool that asks may be always allowed. `load_file` reads a file from the
        user's disk into the scene, which can be patient data, so one click must not stand
        for every path the model names for the rest of the conversation either."""
        path = os.path.join(tempfile.mkdtemp(), "head.nrrd")
        slicer.util.saveNode(slicer.util.getNode("CT"), path)
        self.engine.script(
            [tool_call("load_file", "tc-1", {"path": path})],
            [tool_call("load_file", "tc-2", {"path": path})],
            [text("Loaded twice."), STOP, DONE],
        )
        self.sign_in()
        self.command("send", text="Load that file, twice", attachmentIds=[])
        wait_until(lambda: self.approval().get("toolCallId") == "tc-1", timeout_s=20,
                   what="the load_file approval")

        self.command("approve", toolCallId="tc-1", action="always")

        wait_until(lambda: self.approval().get("toolCallId") == "tc-2", timeout_s=20,
                   what="the second load_file approval")
        self.assertEqual(self.approval()["toolName"], "load_file")
        self.assertEqual(self.state()["alwaysAllowedTools"], [])
        self.command("approve", toolCallId="tc-2", action="once")
        wait_until(lambda: not self.state()["isLoading"], timeout_s=20, what="the run to end")
        resumes = self.engine.bodies("/resume")
        self.assertEqual([r["tool_call_id"] for r in resumes], ["tc-1", "tc-2"])
        self.assertFalse(any(r["is_error"] for r in resumes), resumes)

    def test_a_call_with_a_gemini_length_id_can_be_allowed(self):
        """Gemini carries its thought signature inside the tool call id, so the id runs to
        kilobytes. Allow once on such a call reaches the engine with the id unchanged."""
        call_id = "call_" + "s" * 4000
        path = os.path.join(tempfile.mkdtemp(), "head.nrrd")
        slicer.util.saveNode(slicer.util.getNode("CT"), path)
        self.engine.script(
            [tool_call("load_file", call_id, {"path": path})],
            [text("Loaded."), STOP, DONE],
        )
        self.sign_in()
        self.command("send", text="Load that file", attachmentIds=[])
        wait_until(lambda: self.approval().get("toolCallId") == call_id, timeout_s=20,
                   what="the load_file approval")

        self.command("approve", toolCallId=call_id, action="once")

        wait_until(lambda: not self.state()["isLoading"], timeout_s=20, what="the run to end")
        resumes = self.engine.bodies("/resume")
        self.assertEqual([r["tool_call_id"] for r in resumes], [call_id])
        self.assertFalse(resumes[0]["is_error"], resumes)

    def _approve_always_against(self, pending):
        """Answer "always" with no ``toolCallId``, as an in-process caller may, while
        ``snapshot()`` reports ``pending`` as the card up."""
        controller = self.runtime.controller
        live = controller.snapshot
        controller.snapshot = lambda: dict(live(), pendingApproval=pending)
        try:
            controller.approve(Decision(action="always"))
        finally:
            del controller.snapshot  # the instance attribute; the method is back

    def test_an_always_is_refused_however_the_pending_card_reads(self):
        """``approve()`` is public and an in-process caller may pass no ``toolCallId`` (the
        SDK's own page may). Nothing about the answer may depend on reading the
        card, because that read can have moved on between the reading and the answering.
        The worst of them keeps the right ``toolCallId`` and names another tool: pinning
        the answer to the id does not help when the id is the only part still right. So an
        "always" is answered as "once" whatever the snapshot says.
        """
        self.engine.script(
            [tool_call("capture_view", "tc-1", {"view": "3d"})],
            [tool_call("capture_view", "tc-2", {"view": "3d"})],
            [tool_call("capture_view", "tc-3", {"view": "3d"})],
            [text("Three looks."), STOP, DONE],
            [text("I can see them."), STOP, DONE],  # the screenshot follow-up's own turn
        )
        self.sign_in()
        self.command("send", text="Take three screenshots of the 3D view", attachmentIds=[])
        liars = [
            # This call, under another name: the pin lands on the right card, and the name
            # is what a lookup would have trusted.
            lambda card: dict(card, toolName="set_layout"),
            # A card the conversation has moved past.
            lambda _card: {"toolCallId": "tc-gone", "toolName": "set_layout",
                           "arguments": {}, "kind": "client"},
            # No card at all.
            lambda _card: None,
        ]
        for number, lie in enumerate(liars, 1):
            call_id = "tc-{}".format(number)
            wait_until(lambda: self.approval().get("toolCallId") == call_id, timeout_s=20,
                       what="the {} approval".format(call_id))
            self._approve_always_against(lie(self.approval()))
            self.assertEqual(self.state()["alwaysAllowedTools"], [],
                             "an always got through on card {}".format(call_id))
        wait_until(lambda: not self.state()["isLoading"], timeout_s=20, what="the run to end")
        self.assertEqual(self.state()["alwaysAllowedTools"], [])
        # Answered as "once" each time, so all three calls ran.
        self.assertEqual([r["tool_call_id"] for r in self.engine.bodies("/resume")],
                         ["tc-1", "tc-2", "tc-3"])

    def _bundled_skills(self):
        local = self.state()["skills"]["local"]
        return {entry["name"] for entry in local if entry["label"] == BUNDLED_LABEL}

    def test_a_users_tool_is_registered_asks_first_and_runs(self):
        from SlicerFlowLib import paths, user_tools
        from SlicerFlowLib.runtime import Runtime

        folder = user_tools.ensure_user_dir(paths.user_tools_dir())
        with open(user_tools.pack_path(folder, "points"), "w", encoding="utf-8") as fh:
            fh.write(USER_PACK)
        user_tools.write_state(paths.user_tools_file(), user_tools.ToolState(enabled={"points"}))
        lifecycle.stop()  # setUp's chat started before the pack existed
        self.runtime = Runtime("pk_test_e2e", self.engine.url, lambda url: None)
        lifecycle.start(self.runtime)
        self.engine.script(
            [tool_call("add_named_point", "tc-1", {"name": "FromUserTool"})],
            [text("Added it."), STOP, DONE],
        )
        self.sign_in()

        # The schemas go to the engine again when the message starts a session (the POST at
        # start was refused: nobody had signed in yet).
        self.command("send", text="Add a point list called FromUserTool", attachmentIds=[])
        wait_until(lambda: "add_named_point" in self.engine.tools, timeout_s=20,
                   what="the user tool's registration")
        wait_until(lambda: self.approval().get("toolCallId") == "tc-1", timeout_s=20,
                   what="the add_named_point approval")
        self.assertEqual(self.approval()["toolName"], "add_named_point")
        self.command("approve", toolCallId="tc-1", action="once")

        wait_until(lambda: slicer.mrmlScene.GetFirstNodeByName("FromUserTool") is not None,
                   timeout_s=20, what="the user tool to run")
