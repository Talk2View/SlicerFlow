import os
import shutil

import check_cmake_lists
from conftest import REPO


def _copy(tmp_path):
    shutil.copytree(os.path.join(REPO, "SlicerFlow"), str(tmp_path / "SlicerFlow"),
                    ignore=shutil.ignore_patterns("__pycache__", "Testing"))
    return str(tmp_path)


def test_the_repo_lists_every_module_file():
    assert check_cmake_lists.problems(REPO) == []


def test_an_unlisted_script_and_resource_are_reported(tmp_path):
    repo = _copy(tmp_path)
    open(os.path.join(repo, "SlicerFlow", "SlicerFlowLib", "extra.py"), "w").close()
    open(os.path.join(repo, "SlicerFlow", "Resources", "Icons", "extra.png"), "w").close()
    assert check_cmake_lists.problems(repo) == [
        "not in MODULE_PYTHON_SCRIPTS: SlicerFlowLib/extra.py",
        "not in MODULE_PYTHON_RESOURCES: Resources/Icons/extra.png",
    ]


def test_a_listed_file_that_is_missing_is_reported(tmp_path):
    repo = _copy(tmp_path)
    os.remove(os.path.join(repo, "SlicerFlow", "SlicerFlowLib", "host.py"))
    assert check_cmake_lists.problems(repo) == ["listed but missing: SlicerFlowLib/host.py"]


def test_testing_files_are_not_module_files(tmp_path):
    repo = _copy(tmp_path)
    os.makedirs(os.path.join(repo, "SlicerFlow", "Testing", "Python"))
    open(os.path.join(repo, "SlicerFlow", "Testing", "Python", "test_x.py"), "w").close()
    assert check_cmake_lists.problems(repo) == []
