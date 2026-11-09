import pathlib
import subprocess
import tomllib
from pathlib import Path
from typing import Callable

import pytest

from vmupdate import qubes_group_update
from vmupdate.qubes_group_update import Group, Options


@pytest.fixture
def config_file(tmp_path, monkeypatch) -> Callable[..., Path]:
    path = tmp_path / "vm-update.toml"
    monkeypatch.setattr(qubes_group_update, "CONFIG_PATH", str(path))

    def write(text: str):
        path.write_text(text, encoding="utf-8")
        return path

    return write


class TestLoadConfig:
    @staticmethod
    def test_load_config_parses_valid_toml(config_file) -> None:
        config_file('[dev]\nvms = ["work", "personal"]\n')

        options, groups = qubes_group_update.load_config()
        assert options.logging_level == "info"
        assert options.notify_on_failure == True
        assert groups == {
            "dev": Group(vms=["work", "personal"])
        }

    @staticmethod
    def test_load_config_raises_on_malformed_toml(config_file) -> None:
        config_file("[dev\nvms = [")

        with pytest.raises(tomllib.TOMLDecodeError):
            qubes_group_update.load_config()

    @staticmethod
    def test_load_config_parses_logging_level(config_file) -> None:
        config_file('[global_options]\nlogging_level = "debug"\n\n[dev]\nvms = ["work"]\n')

        options, _ = qubes_group_update.load_config()

        assert options.logging_level == "debug"
    @staticmethod
    def test_load_config_returns_only_validated_groups(config_file) -> None:
        config_file('[good]\nvms = ["work"]\n\n[bad]\nvms = "not-a-list"\n')

        options, result = qubes_group_update.load_config()

        assert options.logging_level == "info"
        assert "bad" not in result
        assert result == {"good": Group(vms=["work"])}


def test_notify_update_failure_calls_notify_send(monkeypatch) -> None:
    called = []
    def fake_run(cmd, **kwargs):
        called.append(cmd)
    monkeypatch.setattr(subprocess, "run", fake_run)
    qubes_group_update.notify_update_failure(["work", "personal"])

    assert called == [
        [
            "notify-send",
            "-u",
            "critical",
            "Qubes VM Update Failed",
            "Update failed for 2 qubes.",
        ]
    ]



