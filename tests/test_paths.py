from SlicerFlowLib import paths


def test_everything_lives_under_slicerflow_home(monkeypatch, tmp_path):
    monkeypatch.setenv("SLICERFLOW_HOME", str(tmp_path / "home"))
    assert paths.home() == str(tmp_path / "home")
    assert paths.token_file() == str(tmp_path / "home" / "auth.json")
    assert paths.lock_file() == str(tmp_path / "home" / "chat.lock")
    assert paths.user_skills_dir() == str(tmp_path / "home" / "skills")
    assert paths.metrics_file() == str(tmp_path / "home" / "metrics.jsonl")
    assert paths.conversations_file() == str(tmp_path / "home" / "conversations.json")
    assert paths.conversations_file() != paths.token_file()


def test_a_relative_or_tilde_home_is_made_absolute(monkeypatch):
    monkeypatch.setenv("SLICERFLOW_HOME", "~/sf-test-home")
    assert paths.home().endswith("/sf-test-home")
    assert not paths.home().startswith("~")


def test_the_legacy_home_is_read_when_the_new_one_is_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("SLICERFLOW_HOME", raising=False)
    monkeypatch.setenv("TALK2SLICER_HOME", str(tmp_path / "old"))
    assert paths.ENV_HOME == "SLICERFLOW_HOME"
    assert paths.home() == str(tmp_path / "old")


def test_the_new_home_wins_when_both_are_set(monkeypatch, tmp_path):
    monkeypatch.setenv("SLICERFLOW_HOME", str(tmp_path / "new"))
    monkeypatch.setenv("TALK2SLICER_HOME", str(tmp_path / "old"))
    assert paths.home() == str(tmp_path / "new")


def test_the_users_tools_live_under_slicerflow_home(monkeypatch, tmp_path):
    monkeypatch.setenv("SLICERFLOW_HOME", str(tmp_path / "home"))
    assert paths.user_tools_dir() == str(tmp_path / "home" / "tools")
    assert paths.user_tools_file() == str(tmp_path / "home" / "tools.json")
