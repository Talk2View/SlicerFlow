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

    def test_the_talk2slicer_settings_migrate_through_a_real_qsettings(self):
        import tempfile

        import qt
        from SlicerFlowLib import config

        path = os.path.join(tempfile.mkdtemp(prefix="sf-settings-"), "settings.ini")
        settings = qt.QSettings(path, qt.QSettings.IniFormat)
        settings.setValue("Talk2Slicer/PartnerKey", "pk_old")
        settings.setValue("Talk2Slicer/ChatPanelShown", False)
        settings.setValue(config.SETTINGS_URL, "https://new.example.com")
        settings.setValue("Talk2Slicer/EngineUrl", "https://old.example.com")
        self.assertEqual(
            sorted(config.migrate_settings(settings)),
            sorted([config.SETTINGS_KEY, config.SETTINGS_PANEL_SHOWN]))
        self.assertEqual(settings.value(config.SETTINGS_KEY), "pk_old")
        self.assertFalse(config.setting_is_true(settings.value(config.SETTINGS_PANEL_SHOWN), True))
        self.assertEqual(settings.value(config.SETTINGS_URL), "https://new.example.com")
        self.assertEqual(settings.value("Talk2Slicer/PartnerKey"), "pk_old")
        settings.remove(config.SETTINGS_KEY)
        self.assertEqual(config.migrate_settings(settings), [])
        self.assertFalse(settings.contains(config.SETTINGS_KEY))
