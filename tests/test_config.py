import json

import pytest

from SlicerFlowLib import config

BUNDLED = {"partnerKey": "pk_live_bundled", "engineUrl": "https://engine.example.com"}


def test_the_extension_key_is_used_when_nothing_overrides_it():
    connection = config.resolve({}, BUNDLED)
    assert connection == ("pk_live_bundled", "https://engine.example.com", "extension")


def test_the_environment_beats_the_extension():
    environ = {config.ENV_KEY: "pk_env", config.ENV_URL: "http://127.0.0.1:8000"}
    assert config.resolve(environ, BUNDLED) == ("pk_env", "http://127.0.0.1:8000", "environment")


def test_no_key_anywhere_and_the_default_engine():
    assert config.resolve({}, {}) == ("", config.DEFAULT_ENGINE_URL, "")


def test_load_bundled(tmp_path):
    path = tmp_path / "partner.json"
    assert config.load_bundled(str(path)) == {}
    path.write_text(json.dumps(BUNDLED))
    assert config.load_bundled(str(path)) == BUNDLED
    path.write_text("not json")
    assert config.load_bundled(str(path)) == {}
    path.write_text("[1, 2]")
    assert config.load_bundled(str(path)) == {}


def test_the_shipped_partner_file_has_both_fields():
    from conftest import REPO
    import os

    shipped = config.load_bundled(os.path.join(REPO, "SlicerFlow", "Resources", "partner.json"))
    assert set(shipped) == {"partnerKey", "engineUrl"}
    assert shipped["engineUrl"] == config.DEFAULT_ENGINE_URL


@pytest.mark.parametrize("url", [
    "https://engine.talk2view.com", "http://127.0.0.1:8000", "http://localhost:8000",
])
def test_good_engine_urls(url):
    assert config.engine_url_problem(url) is None


@pytest.mark.parametrize("url", [
    "http://engine.talk2view.com", "ftp://x", "engine.talk2view.com", "https://", "http://[::1",
    # Loopback, but neither the README nor the refusal message offers it, and the two have
    # to say the same thing as the code.
    "http://[::1]:8000",
])
def test_bad_engine_urls(url):
    assert config.engine_url_problem(url)


def test_the_minimum_slicer_is_5_12():
    assert config.slicer_supported(5, 12) and config.slicer_supported(6, 0)
    assert not config.slicer_supported(5, 10)
    message = config.unsupported_message("5.10.0")
    assert "5.12" in message and "5.10.0" in message and "download.slicer.org" in message


def test_connection_problem():
    assert config.connection_problem(config.Connection("", "https://engine.example.com", "")) == config.NO_PARTNER_KEY
    assert "https://" in config.connection_problem(config.Connection("pk", "http://example.com", "settings"))
    assert config.connection_problem(config.Connection("pk", "https://engine.example.com", "extension")) is None


def test_the_chat_never_starts_by_itself_in_testing_mode_or_when_turned_off():
    assert config.autostart_allowed(False, {}, {})
    assert not config.autostart_allowed(True, {}, {})
    assert not config.autostart_allowed(False, {config.ENV_NO_AUTOSTART: "1"}, {})
    assert not config.autostart_allowed(False, {config.ENV_NO_AUTOSTART: "yes"}, {})
    for off in ("", "0", " "):
        assert config.autostart_allowed(False, {config.ENV_NO_AUTOSTART: off}, {})


@pytest.mark.parametrize("name, value", [
    ("pythonScript", "/path/to/batch.py"),
    ("pythonCode", "print(1)"),
    ("exitAfterStartup", True),
    ("noMainWindow", True),
    ("runPythonAndExit", True),
])
def test_the_chat_never_starts_by_itself_in_a_scripted_or_windowless_run(name, value):
    options = dict.fromkeys(config.SCRIPTED_RUN_OPTIONS)
    assert config.autostart_allowed(False, {}, options)
    options[name] = value
    assert not config.autostart_allowed(False, {}, options)


def test_command_options_tolerates_a_missing_property():
    class Options:  # a Slicer without some of them
        pythonScript = ""
        noMainWindow = False

    read = config.command_options(Options())
    assert set(read) == set(config.SCRIPTED_RUN_OPTIONS)
    assert read["pythonScript"] == "" and read["noMainWindow"] is False
    assert read["exitAfterStartup"] is None
    assert config.autostart_allowed(False, {}, read)


def test_setting_is_true():
    assert config.setting_is_true(None, True) is True
    assert config.setting_is_true(None, False) is False
    assert config.setting_is_true("false", True) is False
    assert config.setting_is_true("true", False) is True
    assert config.setting_is_true(False, True) is False
    assert config.setting_is_true(True, False) is True
