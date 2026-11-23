"""The module is registered, its bundled files are in place, and the SDK imports."""

import os
import unittest

import sf_testing  # noqa: F401  (isolates SLICERFLOW_HOME first)
import slicer

from sf_testing import MODULE_DIR


class ModuleLoadsTest(unittest.TestCase):
    def test_module_is_registered_under_talk2view(self):
        module = slicer.modules.slicerflow
        self.assertEqual(module.title, "SlicerFlow")
        self.assertIn("Talk2View", module.categories)

    def test_the_icon_exists(self):
        icon = os.path.join(MODULE_DIR, "Resources", "Icons", "SlicerFlow.png")
        self.assertTrue(os.path.isfile(icon), icon)

    def test_the_sdk_imports_with_the_ui_server(self):
        from SlicerFlowLib import sdk

        self.assertTrue(sdk.installed())
        import talk2view.ui

        self.assertTrue(hasattr(talk2view.ui, "UIServer"))

    def test_the_sdk_ships_the_chat_page(self):
        from talk2view.ui.static import default_static_dir

        self.assertTrue((default_static_dir() / "index.html").is_file())

    def test_home_is_isolated(self):
        home = os.environ["SLICERFLOW_HOME"]
        settings_dir = os.path.dirname(slicer.app.slicerUserSettingsFilePath)
        self.assertFalse(home.startswith(settings_dir), home)
