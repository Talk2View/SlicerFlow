import json

from SlicerFlowLib import sdk


def test_is_editable_reads_pip_direct_url():
    assert sdk.is_editable(json.dumps({"dir_info": {"editable": True}, "url": "file:///src"})) is True
    wheel = {"archive_info": {"hashes": {"sha256": "bb"}}, "url": "file:///x.whl"}
    for text in (json.dumps(wheel), json.dumps({"dir_info": {}}), None, "", "not json", "[]"):
        assert sdk.is_editable(text) is False


def test_should_install():
    assert sdk.should_install(False, None, False) is True               # missing: install
    assert sdk.should_install(True, "0.2.0", False) is True             # an older extension's wheel
    assert sdk.should_install(True, sdk.SDK_VERSION, False) is False    # the pinned release
    assert sdk.should_install(True, "9.9.9", False) is True             # any other version
    assert sdk.should_install(True, "0.2.0", True) is False             # editable checkout: keep it


def test_the_requirement_is_one_exact_version():
    assert sdk.REQUIREMENT == "talk2view==" + sdk.SDK_VERSION
    assert sdk.PIP_ARGS == ["--only-binary", ":all:", sdk.REQUIREMENT]


class _FakeUtil:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def pip_install(self, requirements):
        self.calls.append(list(requirements))
        if self.fail:
            raise RuntimeError("pip failed")


class _FakeSlicer:
    def __init__(self, fail=False):
        self.util = _FakeUtil(fail=fail)


def test_install_pins_the_release_and_a_forced_one_restores_dependencies(monkeypatch):
    import sys
    import types

    util = _FakeUtil()
    monkeypatch.setitem(sys.modules, "slicer", types.SimpleNamespace(util=util))
    monkeypatch.setattr(sdk, "forget_imports", lambda: None)
    sdk.install()
    assert util.calls == [sdk.PIP_ARGS]
    util.calls.clear()
    sdk.install(force=True)
    assert util.calls == [["--force-reinstall", "--no-deps"] + sdk.PIP_ARGS, sdk.PIP_ARGS]


def test_ensure_sounddevice_pins_an_exact_version_with_only_binary(monkeypatch):
    monkeypatch.setattr(sdk, "_sounddevice_unavailable", False)
    fake = _FakeSlicer()
    ok = sdk.ensure_sounddevice(
        slicer_module=fake,
        find_spec=lambda name: None,          # not installed yet
        import_module=lambda name: object(),  # the install "worked"
    )
    assert ok is True
    assert fake.util.calls == [["--only-binary", ":all:", "sounddevice==0.5.6"]]


def test_an_already_importable_sounddevice_skips_pip_entirely(monkeypatch):
    monkeypatch.setattr(sdk, "_sounddevice_unavailable", False)
    fake = _FakeSlicer()
    ok = sdk.ensure_sounddevice(
        slicer_module=fake, find_spec=lambda name: object(), import_module=lambda name: object()
    )
    assert ok is True
    assert fake.util.calls == []


def test_without_install_a_missing_sounddevice_never_runs_pip(monkeypatch):
    """The chat starting by itself with Slicer: no pip, so no mic until a Start chat."""
    monkeypatch.setattr(sdk, "_sounddevice_unavailable", False)
    fake = _FakeSlicer()
    ok = sdk.ensure_sounddevice(install=False, slicer_module=fake, find_spec=lambda name: None)
    assert ok is False
    assert fake.util.calls == []
    assert sdk._sounddevice_unavailable is False  # a later Start chat may still install it


def test_a_failed_install_is_remembered_for_the_rest_of_the_process(monkeypatch):
    monkeypatch.setattr(sdk, "_sounddevice_unavailable", False)
    fake = _FakeSlicer(fail=True)
    assert sdk.ensure_sounddevice(slicer_module=fake, find_spec=lambda name: None) is False
    assert len(fake.util.calls) == 1
    # A second call, even with a slicer that would now succeed, never touches pip again:
    # the failure is cached for the rest of this process.
    fresh = _FakeSlicer(fail=False)
    assert sdk.ensure_sounddevice(slicer_module=fresh, find_spec=lambda name: None) is False
    assert fresh.util.calls == []


def test_a_failed_import_after_a_successful_install_is_also_remembered(monkeypatch):
    monkeypatch.setattr(sdk, "_sounddevice_unavailable", False)
    fake = _FakeSlicer()

    def _boom(name):
        raise ImportError("no binary for this platform")

    ok = sdk.ensure_sounddevice(slicer_module=fake, find_spec=lambda name: None, import_module=_boom)
    assert ok is False
    assert sdk._sounddevice_unavailable is True
