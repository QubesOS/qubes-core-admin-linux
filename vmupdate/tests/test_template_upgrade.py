#!/usr/bin/python3
# coding=utf-8
import logging
from unittest.mock import MagicMock, Mock

import pytest

import qubesadmin.exc

from vmupdate import template_upgrade
from vmupdate.agent.source.common.exit_codes import EXIT
from vmupdate.tests.conftest import TestApp as _TestApp
from vmupdate.tests.conftest import TestVM as _TestVM

# Captured at import time, before the quiet_logging autouse fixture can
# replace it. Tests that need to exercise the real setup_logging restore
# this reference explicitly.
_REAL_SETUP_LOGGING = template_upgrade.setup_logging


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


def add_standalone(app, name="fedora-41-standalone", **features):
    vm = _TestVM(name, app, klass="StandaloneVM")
    vm.features.update({
        "os-distribution": "fedora",
        "os-version": "41",
    })
    vm.features.update(features)
    return vm


@pytest.fixture(autouse=True)
def quiet_logging(monkeypatch):
    monkeypatch.setattr(template_upgrade, "setup_logging", lambda *_: Mock())


@pytest.mark.parametrize("scenario, expected", [
    ("missing-qube", "No such qube"),
    ("non-template", "only TemplateVMs and StandaloneVMs"),
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
    with pytest.raises(template_upgrade.ValidationError):
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
    monkeypatch.setattr(template_upgrade.TemplateUpgrader, "run_agent",
                        lambda self: None)

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


def test_standalone_without_template_name_left_alone(monkeypatch):
    """A standalone that never had template-name doesn't get one invented."""
    app = CloneApp()
    add_standalone(app)
    monkeypatch.setattr(template_upgrade.TemplateUpgrader, "run_agent",
                        lambda self: None)

    retcode = template_upgrade.main(
        ["--template", "fedora-41-standalone"], app)

    assert retcode == EXIT.OK
    clone = app.domains["fedora-42-standalone"]
    assert clone.klass == "StandaloneVM"
    assert "template-name" not in clone.features
    assert "template-installtime" not in clone.features


def test_standalone_with_template_name_refreshed(monkeypatch):
    """Refresh stale standalone template-name for updater EOL checks."""
    app = CloneApp()
    add_standalone(app, **{"template-name": "fedora-41"})
    monkeypatch.setattr(template_upgrade.TemplateUpgrader, "run_agent",
                        lambda self: None)

    retcode = template_upgrade.main(
        ["--template", "fedora-41-standalone"], app)

    assert retcode == EXIT.OK
    clone = app.domains["fedora-42-standalone"]
    # check_support() resolves this through EOL_DATES.
    assert clone.features["template-name"] == "fedora-42"
    # template-installtime is template-only; standalones don't get one.
    assert "template-installtime" not in clone.features


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

    def fail_agent(self):
        raise template_upgrade.UpgradeError("agent failed")

    monkeypatch.setattr(template_upgrade.TemplateUpgrader, "run_agent",
                        fail_agent)
    args = ["--template", "fedora-41"]
    if keep_on_failure:
        args.append("--keep-on-failure")

    retcode = template_upgrade.main(args, app)

    assert retcode == EXIT.ERR
    assert ("fedora-42" not in app.domains) is expect_clone_removed


def test_rejects_existing_clone_name(capsys):
    """If the target clone name already exists, validation fails before
    anything is mutated."""
    app = CloneApp()
    add_template(app)
    add_template(app, name="fedora-42", **{"os-version": "42"})

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.ERR_USAGE
    assert "already exists" in capsys.readouterr().err
    assert app.clone_calls == []


def test_standalone_template_name_without_version_is_left_alone(monkeypatch):
    """Standalone whose template-name doesn't carry the current version
    (custom string, manual edit) is left untouched."""
    app = CloneApp()
    add_standalone(app, **{"template-name": "my-custom-base"})
    monkeypatch.setattr(template_upgrade.TemplateUpgrader, "run_agent",
                        lambda self: None)

    retcode = template_upgrade.main(
        ["--template", "fedora-41-standalone"], app)

    assert retcode == EXIT.OK
    clone = app.domains["fedora-42-standalone"]
    assert clone.features["template-name"] == "my-custom-base"


def test_main_clone_failure(monkeypatch, capsys):
    """If the Admin-API clone call raises, main() reports it as a runtime
    error (EXIT.ERR), not a usage error."""
    app = CloneApp()
    add_template(app)

    def boom(*_a, **_kw):
        raise qubesadmin.exc.QubesException("storage pool full")

    monkeypatch.setattr(app, "clone_vm", boom)

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.ERR
    assert "clone failed: storage pool full" in capsys.readouterr().err


def test_rollback_noop_when_no_clone():
    """rollback() before clone() ran is a safe no-op."""
    upgrader = template_upgrade.TemplateUpgrader(CloneApp(), Mock(), Mock())
    upgrader.rollback()  # must not raise


def test_rollback_handles_delete_failure():
    """If the Admin-API delete raises, rollback logs and swallows; the
    caller has already decided the upgrade has failed, so re-raising would
    just mask the original error.
    """
    # dict's __delitem__ is looked up on the type, not the instance, so we
    # use a MagicMock for app.domains (which supports __delitem__ as a side
    # effect) instead of trying to patch the test-helper Domains dict.
    app = MagicMock()
    app.domains.__delitem__.side_effect = \
        qubesadmin.exc.QubesException("VM is running")
    upgrader = template_upgrade.TemplateUpgrader(app, Mock(), Mock())
    upgrader.clone_vm = Mock(name="fedora-42")
    upgrader.clone_vm.name = "fedora-42"

    upgrader.rollback()  # must not raise

    upgrader.log.error.assert_called_once()


def _reset_template_upgrade_logger():
    logger = logging.getLogger("vm-template-upgrade")
    logger.handlers.clear()
    logger.propagate = True


def test_setup_logging_is_idempotent(tmp_path, monkeypatch):
    """Calling setup_logging twice must not duplicate handlers."""
    monkeypatch.setattr(template_upgrade, "setup_logging",
                        _REAL_SETUP_LOGGING)
    monkeypatch.setattr(template_upgrade, "LOG_PATH",
                        str(tmp_path / "qvm-template-upgrade.log"))
    _reset_template_upgrade_logger()

    log1 = template_upgrade.setup_logging("INFO")
    handler_count = len(log1.handlers)
    log2 = template_upgrade.setup_logging("INFO")

    assert log1 is log2
    assert len(log2.handlers) == handler_count
    assert log2.propagate is False


def test_setup_logging_tolerates_missing_log_dir(tmp_path, monkeypatch):
    """A missing log directory degrades to stderr-only, not a crash."""
    monkeypatch.setattr(template_upgrade, "setup_logging",
                        _REAL_SETUP_LOGGING)
    monkeypatch.setattr(template_upgrade, "LOG_PATH",
                        str(tmp_path / "nope" / "qvm-template-upgrade.log"))
    _reset_template_upgrade_logger()

    log = template_upgrade.setup_logging("INFO")

    # The file handler should have been skipped; stderr stays.
    assert not any(isinstance(h, logging.FileHandler)
                   for h in log.handlers)
    assert any(isinstance(h, logging.StreamHandler) and
               not isinstance(h, logging.FileHandler)
               for h in log.handlers)
