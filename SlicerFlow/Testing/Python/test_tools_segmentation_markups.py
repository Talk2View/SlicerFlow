"""The segmentation and markups tools against a real MRML scene."""

import json

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)
import slicer

from sf_testing import SceneTestCase, make_volume
from SlicerFlowLib.tools import markups, segmentation


def _segment(node, name):
    segmentation_ = node.GetSegmentation()
    return segmentation_.GetSegmentIdBySegmentName(name)


class ThresholdAndMeasureTest(SceneTestCase):
    def setUp(self):
        super().setUp()
        self.volume = make_volume("CT")

    def _break_the_labelmap_write(self):
        """Make the last step of ``threshold_segment`` fail, which is what turns a partly
        done call into lost work. Nothing found an unforced trigger for it; the rule is
        that whatever the failure, the user's segment survives it."""
        original = slicer.util.updateSegmentBinaryLabelmapFromArray
        self.addCleanup(setattr, slicer.util, "updateSegmentBinaryLabelmapFromArray", original)

        def broken(*args, **kwargs):
            raise RuntimeError("the labelmap could not be written")

        slicer.util.updateSegmentBinaryLabelmapFromArray = broken

    def _segment_names(self, node):
        inner = node.GetSegmentation()
        return [inner.GetNthSegment(i).GetName() for i in range(inner.GetNumberOfSegments())]

    def test_a_failed_threshold_keeps_the_segment_it_was_replacing(self):
        """The segment being replaced is the user's work. A call that fails after removing
        it and before filling its replacement would leave an empty segment with the same
        name, which get_scene_context keeps reporting as if the data were still there."""
        segmentation.threshold_segment(50, 150, "Block")
        node = slicer.util.getNode("CT segmentation")
        before = json.loads(segmentation.measure_segment_volumes(["Block"]))["segments"][0]
        self._break_the_labelmap_write()
        with self.assertRaises(RuntimeError):
            segmentation.threshold_segment(-10, 150, "Block")
        self.assertEqual(self._segment_names(node), ["Block"])
        after = json.loads(segmentation.measure_segment_volumes(["Block"]))["segments"][0]
        self.assertEqual(after, before)

    def test_a_failed_threshold_leaves_no_empty_segment_behind(self):
        segmentation.threshold_segment(50, 150, "Block")
        node = slicer.util.getNode("CT segmentation")
        self._break_the_labelmap_write()
        with self.assertRaises(RuntimeError):
            segmentation.threshold_segment(-10, 10, "Air")
        self.assertEqual(self._segment_names(node), ["Block"])

    def test_a_failed_threshold_leaves_no_half_made_segmentation(self):
        self._break_the_labelmap_write()
        with self.assertRaises(RuntimeError):
            segmentation.threshold_segment(50, 150, "Block")
        self.assertEqual(slicer.util.getNodesByClass("vtkMRMLSegmentationNode"), [])

    def test_measuring_leaves_nothing_in_the_users_scene(self):
        """SegmentStatisticsLogic keeps its settings in a scene node, which is hidden but
        saved with the scene. A measurement is a question, not a change."""
        segmentation.threshold_segment(50, 150, "Block")
        before = slicer.mrmlScene.GetNumberOfNodes()
        segmentation.measure_segment_volumes()
        self.assertEqual(slicer.mrmlScene.GetNumberOfNodes(), before)

    def test_measuring_leaves_a_segment_statistics_node_of_the_users_alone(self):
        """``getParameterNode()`` hands back the Segment Statistics module's own node when
        the user has that module open, so only a node this tool made may be removed."""
        import SegmentStatistics

        theirs = SegmentStatistics.SegmentStatisticsLogic().getParameterNode()
        segmentation.threshold_segment(50, 150, "Block")
        segmentation.measure_segment_volumes()
        self.assertIsNotNone(slicer.mrmlScene.GetNodeByID(theirs.GetID()))

    def test_threshold_makes_a_segmentation_and_measure_reads_its_volume(self):
        self.assertEqual(
            segmentation.threshold_segment(50, 150, "Block"),
            "Made segment 'Block' in CT segmentation: 2000 voxels of CT from 50 to 150.")
        measured = json.loads(segmentation.measure_segment_volumes())
        self.assertEqual(measured, {
            "segmentation": "CT segmentation",
            "segments": [{"segment": "Block", "volume_cm3": 2.0, "voxels": 2000}],
        })

    def test_threshold_again_replaces_the_segment(self):
        segmentation.threshold_segment(50, 150, "Block")
        self.assertTrue(segmentation.threshold_segment(-10, 150, "block").startswith("Replaced"))
        node = slicer.util.getNode("CT segmentation")
        self.assertEqual(node.GetSegmentation().GetNumberOfSegments(), 1)
        rows = json.loads(segmentation.measure_segment_volumes(["BLOCK"]))["segments"]
        self.assertEqual(rows[0]["voxels"], 40 * 30 * 20)

    def test_threshold_into_a_named_segmentation(self):
        segmentation.threshold_segment(50, 150, "Block", segmentation_name="Mine")
        segmentation.threshold_segment(-10, 10, "Air", segmentation_name="Mine")
        node = slicer.util.getNode("Mine")
        self.assertEqual(node.GetSegmentation().GetNumberOfSegments(), 2)

    def test_threshold_refusals(self):
        with self.assertRaisesRegex(ValueError, "greater than the maximum"):
            segmentation.threshold_segment(10, 5)
        with self.assertRaisesRegex(ValueError, "its values run from 0.0 to 100.0"):
            segmentation.threshold_segment(500, 600)
        with self.assertRaisesRegex(ValueError, "needs a name"):
            segmentation.threshold_segment(50, 150, " ")

    def test_several_segmentations_need_a_name(self):
        segmentation.threshold_segment(50, 150, "Block", segmentation_name="A")
        segmentation.threshold_segment(50, 150, "Block", segmentation_name="B")
        with self.assertRaisesRegex(ValueError, "several segmentations"):
            segmentation.measure_segment_volumes()
        with self.assertRaisesRegex(ValueError, "no segment named 'Liver'"):
            segmentation.measure_segment_volumes(["Liver"], "A")


class SegmentDisplayTest(SceneTestCase):
    def setUp(self):
        super().setUp()
        make_volume("CT")
        segmentation.threshold_segment(50, 150, "Block")
        segmentation.threshold_segment(-10, 10, "Air")
        self.node = slicer.util.getNode("CT segmentation")
        self.display = self.node.GetDisplayNode()

    def test_hide_one_segment(self):
        self.assertEqual(segmentation.set_segment_display(["air"], visible=False),
                         "CT segmentation (Air): hidden.")
        self.assertFalse(self.display.GetSegmentVisibility(_segment(self.node, "Air")))
        self.assertTrue(self.display.GetSegmentVisibility(_segment(self.node, "Block")))

    def test_opacity_and_color_of_every_segment(self):
        result = segmentation.set_segment_display(opacity=0.3, color="orange")
        self.assertEqual(result, "CT segmentation (Block, Air): opacity 0.3, color #ffa500.")
        block = _segment(self.node, "Block")
        self.assertAlmostEqual(self.display.GetSegmentOpacity3D(block), 0.3)
        color = self.node.GetSegmentation().GetSegment(block).GetColor()
        self.assertEqual([round(c, 3) for c in color], [1.0, 0.647, 0.0])

    def test_display_refusals(self):
        with self.assertRaisesRegex(ValueError, "Say what to change"):
            segmentation.set_segment_display(["Air"])
        with self.assertRaisesRegex(ValueError, "from 0 to 1"):
            segmentation.set_segment_display(opacity=2)
        with self.assertRaisesRegex(ValueError, "Unknown color"):
            segmentation.set_segment_display(color="blurple")
        with self.assertRaisesRegex(ValueError, "Its segments: Block, Air"):
            segmentation.set_segment_display(["Liver"], visible=True)


class MarkupsTest(SceneTestCase):
    def test_points_go_into_one_list(self):
        self.assertEqual(markups.add_markup_point(1, 2, 3, "Tip"),
                         "Added point Tip to Talk2View points at R 1.0, A 2.0, S 3.0.")
        result = markups.add_markup_point(-4.25, 5, 6)
        node = slicer.util.getNode(markups.DEFAULT_LIST)
        self.assertEqual(node.GetNumberOfControlPoints(), 2)
        position = [0.0, 0.0, 0.0]
        node.GetNthControlPointPosition(1, position)
        self.assertEqual(position, [-4.25, 5.0, 6.0])
        self.assertTrue(node.GetNthControlPointLabel(1))  # numbered automatically
        self.assertIn(node.GetNthControlPointLabel(1), result)

    def test_a_list_name_that_is_not_a_point_list(self):
        make_volume("CT")
        with self.assertRaisesRegex(ValueError, "not a point list"):
            markups.add_markup_point(0, 0, 0, list_name="CT")
