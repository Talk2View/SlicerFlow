"""``config.migrate_settings``: the Talk2Slicer settings carry over to SlicerFlow once.

The extension was called Talk2Slicer, and saved its settings under ``Talk2Slicer/``. A user
who upgrades keeps their partner key, engine URL and whether the chat panel was shown.
"""

from SlicerFlowLib import config


class FakeSettings:
    """The part of ``qt.QSettings`` the migration uses, over a dict."""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def contains(self, key):
        return key in self.values

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


def test_the_new_keys_are_under_slicerflow_and_each_has_its_legacy_key():
    assert config.SETTINGS_KEY == "SlicerFlow/PartnerKey"
    assert config.SETTINGS_URL == "SlicerFlow/EngineUrl"
    assert config.SETTINGS_PANEL_SHOWN == "SlicerFlow/ChatPanelShown"
    assert config.LEGACY_SETTINGS == {
        "SlicerFlow/PartnerKey": "Talk2Slicer/PartnerKey",
        "SlicerFlow/EngineUrl": "Talk2Slicer/EngineUrl",
        "SlicerFlow/ChatPanelShown": "Talk2Slicer/ChatPanelShown",
    }


def test_each_legacy_value_is_copied_and_the_legacy_keys_are_kept():
    legacy = {
        "Talk2Slicer/PartnerKey": "pk_saved",
        "Talk2Slicer/EngineUrl": "https://saved.example.com",
        "Talk2Slicer/ChatPanelShown": "false",
    }
    settings = FakeSettings(legacy)
    copied = config.migrate_settings(settings)
    assert sorted(copied) == sorted(config.LEGACY_SETTINGS)
    assert settings.value(config.SETTINGS_KEY) == "pk_saved"
    assert settings.value(config.SETTINGS_URL) == "https://saved.example.com"
    assert settings.value(config.SETTINGS_PANEL_SHOWN) == "false"
    for key, value in legacy.items():  # an older build still finds its own
        assert settings.value(key) == value


def test_a_new_value_is_never_overwritten():
    settings = FakeSettings({
        "Talk2Slicer/PartnerKey": "pk_old",
        "Talk2Slicer/EngineUrl": "https://old.example.com",
        config.SETTINGS_KEY: "pk_new",
        config.SETTINGS_URL: "",  # saved empty is still a value
    })
    assert config.migrate_settings(settings) == []
    assert settings.value(config.SETTINGS_KEY) == "pk_new"
    assert settings.value(config.SETTINGS_URL) == ""


def test_only_what_was_saved_is_copied():
    settings = FakeSettings({"Talk2Slicer/ChatPanelShown": False})
    assert config.migrate_settings(settings) == [config.SETTINGS_PANEL_SHOWN]
    assert settings.value(config.SETTINGS_PANEL_SHOWN) is False
    assert not settings.contains(config.SETTINGS_KEY)
    assert not settings.contains(config.SETTINGS_URL)


def test_nothing_to_migrate_on_a_fresh_install():
    settings = FakeSettings()
    assert config.migrate_settings(settings) == []
    assert not any(settings.contains(key) for key in config.LEGACY_SETTINGS)
    assert settings.contains(config.SETTINGS_MIGRATED)  # and it will not run again


def test_it_runs_once_so_a_value_removed_later_does_not_come_back():
    settings = FakeSettings({"Talk2Slicer/PartnerKey": "pk_old"})
    assert config.migrate_settings(settings) == [config.SETTINGS_KEY]
    del settings.values[config.SETTINGS_KEY]  # the panel's "Use defaults"
    assert config.migrate_settings(settings) == []
    assert not settings.contains(config.SETTINGS_KEY)
    assert settings.value("Talk2Slicer/PartnerKey") == "pk_old"


def test_a_key_the_new_build_already_had_does_not_come_back_once_removed():
    settings = FakeSettings({"Talk2Slicer/PartnerKey": "pk_old", config.SETTINGS_KEY: "pk_new"})
    assert config.migrate_settings(settings) == []
    del settings.values[config.SETTINGS_KEY]
    assert config.migrate_settings(settings) == []
    assert not settings.contains(config.SETTINGS_KEY)


def test_the_legacy_env_var_names_are_read_when_the_new_ones_are_unset():
    assert config.LEGACY_ENV == {
        "SLICERFLOW_HOME": "TALK2SLICER_HOME",
        "SLICERFLOW_NO_AUTOSTART": "TALK2SLICER_NO_AUTOSTART",
    }
    assert config.getenv({"TALK2SLICER_HOME": "/old"}, "SLICERFLOW_HOME") == "/old"
    assert config.getenv({"SLICERFLOW_HOME": "/new"}, "SLICERFLOW_HOME") == "/new"
    assert config.getenv({}, "SLICERFLOW_HOME") is None
    assert config.getenv({}, "SLICERFLOW_HOME", "x") == "x"


def test_the_new_env_var_wins_when_both_are_set():
    environ = {"SLICERFLOW_HOME": "/new", "TALK2SLICER_HOME": "/old"}
    assert config.getenv(environ, "SLICERFLOW_HOME") == "/new"
    environ = {"SLICERFLOW_NO_AUTOSTART": "0", "TALK2SLICER_NO_AUTOSTART": "1"}
    assert config.getenv(environ, "SLICERFLOW_NO_AUTOSTART") == "0"


def test_the_chat_does_not_start_by_itself_with_either_no_autostart_name():
    assert config.ENV_NO_AUTOSTART == "SLICERFLOW_NO_AUTOSTART"
    assert not config.autostart_allowed(False, {"SLICERFLOW_NO_AUTOSTART": "1"}, {})
    assert not config.autostart_allowed(False, {"TALK2SLICER_NO_AUTOSTART": "1"}, {})
    # Both set: the new one wins, either way.
    assert config.autostart_allowed(
        False, {"SLICERFLOW_NO_AUTOSTART": "0", "TALK2SLICER_NO_AUTOSTART": "1"}, {})
    assert not config.autostart_allowed(
        False, {"SLICERFLOW_NO_AUTOSTART": "1", "TALK2SLICER_NO_AUTOSTART": "0"}, {})
