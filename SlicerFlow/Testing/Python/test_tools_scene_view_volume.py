"""The scene, view and volume tools against a real MRML scene (no network)."""

import json
import os
import pathlib
import tempfile

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)
import slicer

from sf_testing import SceneTestCase, make_volume
from SlicerFlowLib.tools import scene, view, volume

LOCAL_SAMPLE_CATEGORY = "SlicerFlowTest"


class SceneToolsTest(SceneTestCase):
    def test_scene_context_lists_volumes_views_and_layout(self):
        make_volume("CT")
        context = json.loads(scene.get_scene_context())
        self.assertEqual([v["name"] for v in context["volumes"]], ["CT"])
        ct = context["volumes"][0]
        self.assertEqual(ct["dimensions"], [40, 30, 20])
        self.assertEqual(ct["spacing_mm"], [1.0, 2.0, 0.5])
        self.assertEqual(ct["value_range"], [0.0, 100.0])
        self.assertEqual(context["layout"], "conventional")
        self.assertEqual(context["slice_views"]["red"]["range_mm"], [-0.25, 9.75])
        self.assertEqual(context["segmentations"], [])

    def test_scene_context_of_an_empty_scene(self):
        context = json.loads(scene.get_scene_context())
        self.assertEqual(context["volumes"], [])
        self.assertIsNone(context["slice_views"]["red"]["range_mm"])

    def test_list_sample_data_hides_developer_samples(self):
        samples = json.loads(scene.list_sample_data())
        names = {s["name"] for s in samples}
        self.assertIn("MRHead", names)
        self.assertFalse({s["category"] for s in samples} & set(scene.HIDDEN_SAMPLE_CATEGORIES))

    def test_load_sample_data_refuses_an_unknown_name(self):
        with self.assertRaisesRegex(ValueError, "list_sample_data"):
            scene.load_sample_data("NoSuchSample")

    def test_load_sample_data_loads_a_registered_sample(self):
        import SampleData

        source_file = os.path.join(tempfile.mkdtemp(), "sf-local.nrrd")
        slicer.util.saveNode(make_volume("Source"), source_file)
        slicer.mrmlScene.Clear(0)
        cached_name = "sf-local-{}.nrrd".format(os.getpid())
        SampleData.SampleDataLogic.registerCustomSampleDataSource(
            category=LOCAL_SAMPLE_CATEGORY, sampleName="SFLocal",
            uris=pathlib.Path(source_file).as_uri(), fileNames=cached_name, nodeNames="SFLocal")
        cache = slicer.mrmlScene.GetCacheManager().GetRemoteCacheDirectory()
        self.addCleanup(slicer.modules.sampleDataSources.pop, LOCAL_SAMPLE_CATEGORY, None)
        self.addCleanup(lambda: os.path.exists(os.path.join(cache, cached_name))
                        and os.remove(os.path.join(cache, cached_name)))
        self.assertEqual(scene.load_sample_data("sflocal"), "Loaded: SFLocal.")
        self.assertIsNotNone(slicer.mrmlScene.GetFirstNodeByName("SFLocal"))

    def test_load_file_loads_a_volume(self):
        path = os.path.join(tempfile.mkdtemp(), "head.nrrd")
        slicer.util.saveNode(make_volume("Head"), path)
        slicer.mrmlScene.Clear(0)
        self.assertEqual(scene.load_file(path), "Loaded head.nrrd: head.")
        self.assertEqual(len(slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode")), 1)

    def test_load_file_refusals(self):
        folder = tempfile.mkdtemp()
        # An empty path used to be resolved to the working directory ("/" under Slicer) and
        # answered "/ is a folder", which tells the model nothing about what went wrong.
        for empty in ("", "   "):
            with self.assertRaises(ValueError) as caught:
                scene.load_file(empty)
            self.assertIn("Name a file to load", str(caught.exception))
            self.assertNotIn("is a folder", str(caught.exception))
        with self.assertRaisesRegex(ValueError, "is a folder"):
            scene.load_file(folder)
        with self.assertRaisesRegex(ValueError, "There is no file"):
            scene.load_file(os.path.join(folder, "missing.nrrd"))
        scene_file = os.path.join(folder, "work.mrml")
        open(scene_file, "w").close()
        with self.assertRaisesRegex(ValueError, "scene file"):
            scene.load_file(scene_file)
        junk = os.path.join(folder, "junk.nrrd")
        with open(junk, "w") as fh:
            fh.write("not an image")
        with self.assertRaisesRegex(ValueError, "could not load"):
            scene.load_file(junk)


class ViewToolsTest(SceneTestCase):
    def test_set_layout_ignores_case_and_refuses_unknown_names(self):
        self.assertEqual(view.set_layout("Four-Up"), "Layout set to four-up.")
        self.assertEqual(slicer.app.layoutManager().layout, slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpView)
        with self.assertRaisesRegex(ValueError, "Unknown layout"):
            view.set_layout("sideways")

    def test_navigate_slice_absolute_relative_and_outside(self):
        make_volume()
        red = slicer.app.layoutManager().sliceWidget("Red").mrmlSliceNode()
        self.assertEqual(view.navigate_slice(3.0), "The red view is at 3.0 mm.")
        self.assertEqual(red.GetSliceOffset(), 3.0)
        view.navigate_slice(1.5, relative=True)
        self.assertEqual(red.GetSliceOffset(), 4.5)
        self.assertIn("outside the volume, which spans -0.25 to 9.75 mm", view.navigate_slice(50.0, "RED"))
        with self.assertRaisesRegex(ValueError, "Unknown view"):
            view.navigate_slice(0.0, "blue")

    def test_navigate_slice_says_when_the_view_shows_no_volume(self):
        """With nothing loaded the answer used to be "The red view is at 10.0 mm." and no
        more, which reads as if it had moved to something."""
        answer = view.navigate_slice(10.0)
        self.assertIn("The red view is at 10.0 mm.", answer)
        self.assertIn("no volume", answer.lower())

    def test_center_views(self):
        make_volume()
        self.assertEqual(view.center_views(), "Views centered.")


class VolumeToolsTest(SceneTestCase):
    def test_set_window_level(self):
        node = make_volume("CT")
        self.assertEqual(volume.set_window_level(400, 40), "Window 400 / level 40 on CT.")
        display = node.GetDisplayNode()
        self.assertEqual((display.GetWindow(), display.GetLevel()), (400.0, 40.0))
        self.assertFalse(display.GetAutoWindowLevel())
        with self.assertRaisesRegex(ValueError, "greater than 0"):
            volume.set_window_level(0, 40)
        with self.assertRaisesRegex(ValueError, "No volume named"):
            volume.set_window_level(400, 40, "MR")

    def test_set_volume_rendering_with_a_preset_and_hide_it(self):
        node = make_volume("CT")
        self.assertEqual(volume.set_volume_rendering(True, "CT-Bone"), "Volume rendering of CT shown with CT-Bone.")
        logic = slicer.modules.volumerendering.logic()
        self.assertTrue(logic.GetFirstVolumeRenderingDisplayNode(node).GetVisibility())
        volume.set_volume_rendering(False)
        self.assertFalse(logic.GetFirstVolumeRenderingDisplayNode(node).GetVisibility())
        with self.assertRaisesRegex(ValueError, "Unknown preset"):
            volume.set_volume_rendering(True, "CT-Rainbow")

    def test_a_volume_with_no_display_node_gets_one(self):
        """A volume built in code has no display node until something makes one, and
        ``AutoWindowLevelOff`` on nothing raised an AttributeError the model cannot act on.
        Every sibling tool either makes the default display nodes or refuses cleanly."""
        bare = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode", "Bare")
        self.assertIsNone(bare.GetDisplayNode())
        self.assertEqual(volume.set_window_level(400, 40, "Bare"), "Window 400 / level 40 on Bare.")
        display = bare.GetDisplayNode()
        self.assertEqual((display.GetWindow(), display.GetLevel()), (400.0, 40.0))

    def test_an_unknown_preset_leaves_no_nodes_behind(self):
        """The preset is checked before anything is built, so a name the model guessed
        wrong does not litter the scene with rendering nodes on its way to the refusal."""
        make_volume("CT")
        before = slicer.mrmlScene.GetNumberOfNodes()
        with self.assertRaisesRegex(ValueError, "Unknown preset"):
            volume.set_volume_rendering(True, "CT-Rainbow")
        self.assertEqual(slicer.mrmlScene.GetNumberOfNodes(), before)

    def test_no_volume_loaded(self):
        with self.assertRaisesRegex(ValueError, "No volume is loaded"):
            volume.set_window_level(400, 40)
