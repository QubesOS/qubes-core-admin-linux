# coding=utf-8
from unittest.mock import Mock

import pytest

from vmupdate import template_upgrade
from vmupdate.agent.source.common.exit_codes import EXIT
from vmupdate.tests.conftest import TestApp as _TestApp
from vmupdate.tests.conftest import TestVM as _TestVM


class CloneApp(_TestApp):
    def __init__(self):
        super().__init__()
        self.clone_calls = []

    def clone_vm(self, source_vm, new_name):
        self.clone_calls.append((source_vm.name, new_name))
        clone = _TestVM(new_name, self, klass=source_vm.klass)
        clone.features.update(source_vm.features)
        return clone


def add_template(app, name="fedora-41", **features):
    vm = _TestVM(name, app, klass="TemplateVM")
    vm.features.update({
        "os-distribution": "fedora",
        "os-version": "41",
        "template-name": name,
        "template-epoch": "0",
        "template-version": "41",
        "template-release": "20250101",
        "template-buildtime": "2025-01-01 00:00:00",
    })
    vm.features.update(features)
    return vm


@pytest.fixture(autouse=True)
def quiet_logging(monkeypatch):
    monkeypatch.setattr(template_upgrade, "setup_logging", lambda *_: Mock())


@pytest.mark.parametrize("scenario, expected", [
    ("missing-qube", "No such qube"),
    ("non-template", "only TemplateVMs"),
    ("missing-os-version", "missing os-distribution / os-version"),
    ("non-numeric-os-version", "Non-numeric distro version"),
    ("unsupported-distro", "Unsupported distro"),
])
def test_validation_errors(scenario, expected, capsys):
    app = CloneApp()
    template_name = "fedora-41"
    if scenario == "non-template":
        _TestVM(template_name, app, klass="AppVM", template=add_template(app))
    elif scenario == "missing-os-version":
        add_template(app)
        del app.domains[template_name].features["os-version"]
    elif scenario == "non-numeric-os-version":
        add_template(app, **{"os-version": "rawhide"})
    elif scenario == "unsupported-distro":
        add_template(app, **{"os-distribution": "arch"})

    retcode = template_upgrade.main(["--template", template_name], app)

    assert retcode == EXIT.ERR_USAGE
    assert expected in capsys.readouterr().err


@pytest.mark.parametrize(
    "source, current, target, override, expected",
    [
        ("fedora-41", "41", "42", None, "fedora-42"),
        ("debian-12", "12", "13", None, "debian-13"),
        ("fedora-41-minimal", "41", "42", None, "fedora-42-minimal"),
        ("custom", "41", "42", "my-template", "my-template"),
    ],
)
def test_clone_name_derivation(source, current, target, override, expected):
    assert template_upgrade.derive_clone_name(
        source, current, target, override) == expected


def test_clone_name_derivation_requires_version_without_override():
    with pytest.raises(template_upgrade.UpgradeError):
        template_upgrade.derive_clone_name("custom", "41", "42", None)


def test_dry_run_does_not_mutate(capsys):
    app = CloneApp()
    vm = add_template(app, "ubuntu-22",
                      **{"os-distribution": "ubuntu",
                         "os-distribution-like": "debian",
                         "os-version": "22"})
    before = dict(vm.features)

    retcode = template_upgrade.main(
        ["--template", "ubuntu-22", "--dry-run"], app)

    assert retcode == EXIT.OK
    assert app.clone_calls == []
    assert vm.features == before
    assert "would clone ubuntu-22 -> ubuntu-23" in capsys.readouterr().out


def test_success_applies_metadata(monkeypatch):
    app = CloneApp()
    add_template(app)
    monkeypatch.setattr(template_upgrade, "run_upgrade_agent",
                        lambda *_args: True)

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.OK
    clone = app.domains["fedora-42"]
    assert clone.features["template-name"] == "fedora-42"
    assert clone.features["template-installtime"] != \
        app.domains["fedora-41"].features.get("template-installtime")
    assert clone.features["template-epoch"] == "0"
    assert clone.features["template-version"] == "41"
    assert clone.features["template-release"] == "20250101"
    assert clone.features["template-buildtime"] == "2025-01-01 00:00:00"
    assert clone.features["os-distribution"] == "fedora"
    assert clone.features["os-version"] == "41"


def test_default_stub_fails_and_cleans_clone(capsys):
    app = CloneApp()
    add_template(app)

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.ERR
    assert "fedora-42" not in app.domains
    assert "not implemented yet" in capsys.readouterr().err


@pytest.mark.parametrize("keep_on_failure, expect_clone_removed", [
    (False, True),
    (True, False),
])
def test_failure_cleanup(monkeypatch, keep_on_failure, expect_clone_removed):
    app = CloneApp()
    add_template(app)

    def fail_agent(*_args):
        raise template_upgrade.UpgradeError("agent failed")

    monkeypatch.setattr(template_upgrade, "run_upgrade_agent", fail_agent)
    args = ["--template", "fedora-41"]
    if keep_on_failure:
        args.append("--keep-on-failure")

    retcode = template_upgrade.main(args, app)

    assert retcode == EXIT.ERR
    assert ("fedora-42" not in app.domains) is expect_clone_removed
