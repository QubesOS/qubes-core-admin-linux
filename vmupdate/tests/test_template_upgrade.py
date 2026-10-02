#!/usr/bin/python3
# coding=utf-8
import io
import logging
import re
import time
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
from tqdm import tqdm

import qubesadmin.exc

from vmupdate import template_upgrade
from vmupdate.agent.source.common.exit_codes import EXIT
from vmupdate.agent.source.common.process_result import ProcessResult
from vmupdate.agent.source.status import (
    FinalStatus,
    FormatedLine,
    StatusInfo,
)
from vmupdate.tests.conftest import TestApp as _TestApp
from vmupdate.tests.conftest import TestVM as _TestVM

# Captured at import time, before the quiet_logging autouse fixture can
# replace it. Tests that need to exercise the real setup_logging restore
# this reference explicitly.
_REAL_SETUP_LOGGING = template_upgrade.setup_logging


class CloneApp(_TestApp):
    def __init__(self) -> None:
        super().__init__()
        self.clone_calls: list[tuple[str, str]] = []

    def clone_vm(self, source_vm, new_name) -> _TestVM:
        self.clone_calls.append((source_vm.name, new_name))
        clone = _TestVM(new_name, self, klass=source_vm.klass)
        clone.features.update(source_vm.features)
        # A freshly cloned qube hasn't been started, so rollback() shouldn't
        # try to force it down. (TestVM defaults running=True.)
        clone.running = False
        return clone


def add_template(app, name="fedora-41", **features) -> _TestVM:
    vm = _TestVM(name, app, klass="TemplateVM")
    vm.features.update(
        {
            "os-distribution": "fedora",
            "os-version": "41",
            "template-name": name,
            "template-epoch": "0",
            "template-version": "41",
            "template-release": "20250101",
            "template-reponame": "@commandline",
            "template-buildtime": "2025-01-01 00:00:00",
            "template-license": "GPLv3+",
            "template-url": "http://www.qubes-os.org",
            "template-summary": "Qubes OS template for fedora-41",
            "template-description": "Qubes OS template for fedora-41.",
        }
    )
    vm.features.update(features)
    return vm


def add_standalone(app, name="fedora-41-standalone", **features) -> _TestVM:
    vm = _TestVM(name, app, klass="StandaloneVM")
    vm.features.update(
        {
            "os-distribution": "fedora",
            "os-version": "41",
        }
    )
    vm.features.update(features)
    return vm


@pytest.fixture(autouse=True)
def quiet_logging(monkeypatch) -> None:
    monkeypatch.setattr(template_upgrade, "setup_logging", lambda *_: Mock())


@pytest.mark.parametrize(
    "scenario, expected",
    [
        ("missing-qube", "No such qube"),
        ("non-template", "only TemplateVMs and StandaloneVMs"),
        ("missing-os-version", "missing os-distribution / os-version"),
        ("non-numeric-os-version", "Non-numeric distro version"),
        ("unsupported-distro", "Unsupported distro"),
        ("derivative-distro", "Kicksecure derivative"),
    ],
)
def test_validation_errors(scenario, expected, capsys) -> None:
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
    elif scenario == "derivative-distro":
        add_template(
            app,
            **{
                "os-distribution": "Kicksecure",
                "os-distribution-like": "debian",
                "os-version": "17",
            },
        )

    retcode = template_upgrade.main(["--template", template_name], app)

    assert retcode == EXIT.ERR_USAGE
    assert expected in capsys.readouterr().err


@pytest.mark.parametrize(
    "source, current, target, override, expected",
    [
        ("fedora-41", "41", "42", None, "fedora-42"),
        ("debian-12", "12", "13", None, "debian-13"),
        ("fedora-41-minimal", "41", "42", None, "fedora-42-minimal"),
        ("custom", "41", "42", None, "custom-42"),
        ("custom", "41", "42", "my-template", "my-template"),
        # "12" must not match inside the digit run "121".
        ("debian-121", "12", "13", None, "debian-121-13"),
        # Only the last stand-alone occurrence is replaced.
        ("fedora-41-extras-41", "41", "42", None, "fedora-41-extras-42"),
    ],
)
def test_clone_name_derivation(
    source, current, target, override, expected
) -> None:
    assert (
        template_upgrade.derive_clone_name(source, current, target, override)
        == expected
    )


def test_dry_run_does_not_mutate(capsys) -> None:
    app = CloneApp()
    vm = add_template(
        app,
        "debian-12",
        **{
            "os-distribution": "debian",
            "os-version": "12",
        },
    )
    before = dict(vm.features)

    retcode = template_upgrade.main(
        ["--template", "debian-12", "--dry-run"], app
    )

    assert retcode == EXIT.OK
    assert app.clone_calls == []
    assert vm.features == before
    assert "would clone debian-12 -> debian-13" in capsys.readouterr().out


def test_rejects_derivative_distro(capsys) -> None:
    """A qube matched only via os-distribution-like has its own version
    scheme, so target = version + 1 would be meaningless; refuse it."""
    app = CloneApp()
    add_template(
        app,
        "kicksecure-17",
        **{
            "os-distribution": "Kicksecure",
            "os-distribution-like": "debian",
            "os-version": "17",
        },
    )

    retcode = template_upgrade.main(["--template", "kicksecure-17"], app)

    assert retcode == EXIT.ERR_USAGE
    err = capsys.readouterr().err
    assert "Kicksecure derivative" in err
    assert "does not match debian's releases" in err
    assert app.clone_calls == []


def test_finalize_failure_does_not_roll_back(monkeypatch, capsys) -> None:
    # a metadata-write failure after a successful in-VM upgrade must keep
    # the upgraded clone; only run_agent failures trigger rollback
    app = CloneApp()
    add_template(app)

    def fake_update_qube(
        qube, agent_args, **kwargs
    ) -> tuple[str, ProcessResult]:
        return qube.name, ProcessResult(EXIT.OK)

    def failing_finalize(self) -> None:
        raise template_upgrade.qubesadmin.exc.QubesException(
            "feature write failed"
        )

    monkeypatch.setattr(template_upgrade, "update_qube", fake_update_qube)
    monkeypatch.setattr(
        template_upgrade.TemplateUpgrader, "finalize", failing_finalize
    )

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.OK
    assert "fedora-42" in app.domains  # upgraded clone kept
    assert "Set them manually" in capsys.readouterr().err


def test_detect_distro_prefers_os_distribution_over_distro_like() -> None:
    # priority is os-distribution first, then os-distribution-like -- never
    # alphabetical: a Fedora template listing debian in distro-like must
    # take the Fedora path
    app = CloneApp()
    vm = add_template(app, **{"os-distribution-like": "debian"})
    upgrader = template_upgrade.TemplateUpgrader(app, Mock(), Mock())
    upgrader.source_vm = vm

    assert upgrader._detect_distro() == ("fedora", "41")


def test_unsupported_distro_message_lists_supported_families(capsys) -> None:
    app = CloneApp()
    add_template(app, **{"os-distribution": "arch"})

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.ERR_USAGE
    assert (
        "supported distro families are: Debian, Fedora"
        in capsys.readouterr().err
    )


def test_success_applies_metadata(monkeypatch, capsys) -> None:
    app = CloneApp()
    add_template(
        app,
        **{
            "template-summary": "Qubes OS template for fedora-41",
            "template-description": "Qubes OS template for fedora-41.",
        },
    )
    monkeypatch.setattr(
        template_upgrade.TemplateUpgrader, "run_agent", lambda self: None
    )

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.OK
    assert (
        "Upgrade complete. New template: fedora-42" in capsys.readouterr().out
    )
    clone = app.domains["fedora-42"]
    assert clone.features["template-name"] == "fedora-42"
    # qvm-template parses this one with strptime(DATE_FMT).
    datetime.strptime(
        clone.features["template-installtime"], template_upgrade.DATE_FMT
    )
    # EVR feeds qvm-template's repo comparisons, so it stays as inherited.
    assert clone.features["template-epoch"] == "0"
    assert clone.features["template-version"] == "41"
    assert clone.features["template-release"] == "20250101"
    # Provenance, though, is now this tool and this moment.
    assert clone.features["template-reponame"] == "@qvm-template-upgrade"
    assert clone.features["template-buildtime"] != "2025-01-01 00:00:00"
    assert (
        clone.features["template-buildtime"]
        == clone.features["template-installtime"]
    )
    assert clone.features["os-distribution"] == "fedora"
    # finalize() backstops os-version even if qubes.PostInstall's feature
    # refresh failed; the in-VM agent verified the new release.
    assert clone.features["os-version"] == "42"
    # Inherited summary/description mention the source name; qvm-template
    # list must describe the clone, not the qube it was upgraded from.
    assert (
        clone.features["template-summary"] == "Qubes OS template for fedora-42"
    )
    assert (
        clone.features["template-description"]
        == "Qubes OS template for fedora-42."
    )


def test_upgraded_template_keeps_every_feature_qvm_template_reads(
    monkeypatch,
) -> None:
    """qvm-template's query_local() subscripts these directly, so an upgraded
    template missing any of them makes `qvm-template list` raise KeyError
    instead of just describing the qube oddly."""
    app = CloneApp()
    add_template(app)
    monkeypatch.setattr(
        template_upgrade.TemplateUpgrader, "run_agent", lambda self: None
    )

    template_upgrade.main(["--template", "fedora-41"], app)

    features = app.domains["fedora-42"].features
    for required in (
        "template-name",
        "template-epoch",
        "template-version",
        "template-release",
        "template-reponame",
        "template-buildtime",
        "template-license",
        "template-url",
        "template-summary",
        "template-description",
    ):
        assert required in features, required
    # qvm-template parses this one with strptime(DATE_FMT).
    datetime.strptime(features["template-buildtime"], template_upgrade.DATE_FMT)


def test_custom_template_description_left_untouched(monkeypatch) -> None:
    """A summary/description that doesn't mention the source name is user
    text and must survive the upgrade unchanged."""
    app = CloneApp()
    add_template(app, **{"template-description": "My hardened browser base"})
    monkeypatch.setattr(
        template_upgrade.TemplateUpgrader, "run_agent", lambda self: None
    )

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.OK
    clone = app.domains["fedora-42"]
    assert clone.features["template-description"] == "My hardened browser base"


def test_standalone_without_template_name_left_alone(
    monkeypatch, capsys
) -> None:
    """A standalone that never had template-name doesn't get one invented."""
    app = CloneApp()
    add_standalone(app)
    monkeypatch.setattr(
        template_upgrade.TemplateUpgrader, "run_agent", lambda self: None
    )

    retcode = template_upgrade.main(["--template", "fedora-41-standalone"], app)

    assert retcode == EXIT.OK
    assert (
        "Upgrade complete. New standalone: fedora-42-standalone"
        in capsys.readouterr().out
    )
    clone = app.domains["fedora-42-standalone"]
    assert clone.klass == "StandaloneVM"
    assert "template-name" not in clone.features
    assert "template-installtime" not in clone.features
    # The os-version backstop applies to standalones too.
    assert clone.features["os-version"] == "42"


def test_standalone_template_name_left_untouched(monkeypatch) -> None:
    """A standalone's template-* features are never rewritten; the clone
    keeps whatever it inherited from the source."""
    app = CloneApp()
    add_standalone(app, **{"template-name": "fedora-41"})
    monkeypatch.setattr(
        template_upgrade.TemplateUpgrader, "run_agent", lambda self: None
    )

    retcode = template_upgrade.main(["--template", "fedora-41-standalone"], app)

    assert retcode == EXIT.OK
    clone = app.domains["fedora-42-standalone"]
    # The clone inherits the source value; the tool does not touch it.
    assert clone.features["template-name"] == "fedora-41"
    assert "template-installtime" not in clone.features


def test_run_agent_success_invokes_transport(monkeypatch) -> None:
    """A successful agent run upgrades the clone (not the source) in
    single-qube VM mode and tells the agent the exact target release."""
    app = CloneApp()
    add_template(app)
    captured: dict[str, Any] = {}

    def fake_update_qube(
        qube, agent_args, **kwargs
    ) -> tuple[str, ProcessResult]:
        captured["qube"] = qube
        captured["agent_args"] = agent_args
        captured["kwargs"] = kwargs
        return qube.name, ProcessResult(EXIT.OK)

    monkeypatch.setattr(template_upgrade, "update_qube", fake_update_qube)

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.OK
    assert captured["qube"].name == "fedora-42"
    assert captured["kwargs"]["dom0"] is False
    assert captured["kwargs"]["show_progress"] is True
    assert captured["agent_args"].version_upgrade == "42"
    assert captured["agent_args"].display_name is None
    # success path still applies post-upgrade metadata
    assert app.domains["fedora-42"].features["template-name"] == "fedora-42"


def test_run_agent_failure_rolls_back_clone(monkeypatch, capsys) -> None:
    """A non-zero agent exit becomes an UpgradeError and the clone is
    removed (the wired replacement for the old NotImplementedError stub)."""
    app = CloneApp()
    add_template(app)

    def fake_update_qube(
        qube, agent_args, **kwargs
    ) -> tuple[str, ProcessResult]:
        return qube.name, ProcessResult(EXIT.ERR_VM_UPDATE)

    monkeypatch.setattr(template_upgrade, "update_qube", fake_update_qube)

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.ERR
    assert "fedora-42" not in app.domains
    assert "version-upgrade agent failed" in capsys.readouterr().err


def _add_debian_template(app, name="debian-12") -> _TestVM:
    return add_template(
        app,
        name,
        **{
            "os-distribution": "debian",
            "os-version": "12",
            "template-version": "12",
        },
    )


def test_run_agent_success_debian_invokes_transport(monkeypatch) -> None:
    """Verify Debian template upgrade works end-to-end."""
    app = CloneApp()
    _add_debian_template(app)
    captured: dict[str, Any] = {}

    def fake_update_qube(
        qube, agent_args, **kwargs
    ) -> tuple[str, ProcessResult]:
        captured["qube"] = qube
        captured["agent_args"] = agent_args
        return qube.name, ProcessResult(EXIT.OK)

    monkeypatch.setattr(template_upgrade, "update_qube", fake_update_qube)

    retcode = template_upgrade.main(["--template", "debian-12"], app)

    assert retcode == EXIT.OK
    assert captured["qube"].name == "debian-13"
    assert captured["agent_args"].version_upgrade == "13"
    assert app.domains["debian-13"].features["template-name"] == "debian-13"


@pytest.mark.parametrize(
    "keep_on_failure, expect_clone_removed",
    [
        (False, True),
        (True, False),
    ],
)
def test_failure_cleanup(
    monkeypatch, keep_on_failure, expect_clone_removed
) -> None:
    app = CloneApp()
    add_template(app)

    def fail_agent(self) -> None:
        raise template_upgrade.UpgradeError("agent failed")

    monkeypatch.setattr(
        template_upgrade.TemplateUpgrader, "run_agent", fail_agent
    )
    args = ["--template", "fedora-41"]
    if keep_on_failure:
        args.append("--keep-new-on-failure")

    retcode = template_upgrade.main(args, app)

    assert retcode == EXIT.ERR
    assert ("fedora-42" not in app.domains) is expect_clone_removed


def test_rejects_existing_clone_name(capsys) -> None:
    """If the target clone name already exists, validation fails before
    anything is mutated."""
    app = CloneApp()
    add_template(app)
    add_template(app, name="fedora-42", **{"os-version": "42"})

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.ERR_USAGE
    assert "already exists" in capsys.readouterr().err
    assert app.clone_calls == []


def test_main_clone_failure(monkeypatch, capsys) -> None:
    """If the Admin-API clone call raises, main() reports it as a runtime
    error (EXIT.ERR), not a usage error."""
    app = CloneApp()
    add_template(app)

    def boom(*_a, **_kw) -> None:
        raise qubesadmin.exc.QubesException("storage pool full")

    monkeypatch.setattr(app, "clone_vm", boom)

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.ERR
    assert "clone failed: storage pool full" in capsys.readouterr().err


def test_agent_output_forwards_lines_and_drops_status() -> None:
    """FormatedLine output reaches the log; StatusInfo ticks are dropped."""
    log = Mock()
    sink = template_upgrade._AgentOutput(log)
    qube = Mock()
    qube.name = "fedora-42"

    sink.put(FormatedLine("fedora-42", "out", "Downloading packages"))
    sink.put("fedora-42:err: a plain string line")
    sink.put(StatusInfo.updating(qube, 42.0))
    sink.put(StatusInfo.done(qube, FinalStatus.SUCCESS))

    # Only the two human-readable lines are logged.
    assert log.info.call_count == 2


def test_rollback_noop_when_no_clone() -> None:
    """rollback() before clone() ran is a safe no-op."""
    upgrader = template_upgrade.TemplateUpgrader(CloneApp(), Mock(), Mock())
    upgrader.rollback()  # must not raise


def test_rollback_handles_delete_failure() -> None:
    """Rollback logs and ignores VM deletion errors."""
    # Use MagicMock to support __delitem__ side effect.
    app = MagicMock()
    app.domains.__delitem__.side_effect = qubesadmin.exc.QubesException(
        "VM is running"
    )
    log = Mock()
    upgrader = template_upgrade.TemplateUpgrader(app, Mock(), log)
    upgrader.cloned_qube = Mock(name="fedora-42")
    upgrader.cloned_qube.name = "fedora-42"
    upgrader.cloned_qube.get_power_state.return_value = "Halted"

    upgrader.rollback()  # must not raise

    log.error.assert_called_once()


def test_rollback_kills_clone_before_delete() -> None:
    """A failed clone is disposable, so rollback kills it before deletion."""
    app = MagicMock()
    upgrader = template_upgrade.TemplateUpgrader(app, Mock(), Mock())
    upgrader.cloned_qube = Mock()
    upgrader.cloned_qube.name = "fedora-42"
    upgrader.cloned_qube.get_power_state.return_value = "Halted"

    upgrader.rollback()

    upgrader.cloned_qube.kill.assert_called_once_with()
    app.domains.__delitem__.assert_called_once_with("fedora-42")


def test_rollback_waits_for_halt_before_delete(monkeypatch) -> None:
    """kill() returns before teardown completes; deleting too early fails
    with "domain is not halted", so rollback polls until the clone halts."""
    app = MagicMock()
    upgrader = template_upgrade.TemplateUpgrader(app, Mock(), Mock())
    upgrader.cloned_qube = Mock()
    upgrader.cloned_qube.name = "fedora-42"
    upgrader.cloned_qube.get_power_state.side_effect = [
        "Running",
        "Running",
        "Halted",
    ]
    monkeypatch.setattr(template_upgrade.time, "sleep", lambda _s: None)

    upgrader.rollback()

    assert upgrader.cloned_qube.get_power_state.call_count == 3
    app.domains.__delitem__.assert_called_once_with("fedora-42")


def test_rollback_deletes_clone_if_already_halted() -> None:
    """kill() raises QubesVMNotStartedError when the clone is already down;
    deletion must still run."""
    app = MagicMock()
    upgrader = template_upgrade.TemplateUpgrader(app, Mock(), Mock())
    upgrader.cloned_qube = Mock()
    upgrader.cloned_qube.name = "fedora-42"
    upgrader.cloned_qube.get_power_state.return_value = "Halted"
    upgrader.cloned_qube.kill.side_effect = (
        qubesadmin.exc.QubesVMNotStartedError("already halted")
    )

    upgrader.rollback()

    app.domains.__delitem__.assert_called_once_with("fedora-42")


def _reset_template_upgrade_logger() -> None:
    for name in ("vm-template-upgrade", template_upgrade.AGENT_LOGGER):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True


def test_setup_logging_is_idempotent(tmp_path, monkeypatch) -> None:
    """Calling setup_logging twice must not duplicate handlers."""
    monkeypatch.setattr(template_upgrade, "setup_logging", _REAL_SETUP_LOGGING)
    monkeypatch.setattr(
        template_upgrade,
        "LOG_PATH",
        str(tmp_path / "qvm-template-upgrade.log"),
    )
    _reset_template_upgrade_logger()

    log1 = template_upgrade.setup_logging("INFO")
    handler_count = len(log1.handlers)
    log2 = template_upgrade.setup_logging("INFO")

    assert log1 is log2
    assert len(log2.handlers) == handler_count
    assert log2.propagate is False


def test_setup_logging_tolerates_missing_log_dir(tmp_path, monkeypatch) -> None:
    """A missing log directory degrades to stderr-only, not a crash."""
    monkeypatch.setattr(template_upgrade, "setup_logging", _REAL_SETUP_LOGGING)
    monkeypatch.setattr(
        template_upgrade,
        "LOG_PATH",
        str(tmp_path / "nope" / "qvm-template-upgrade.log"),
    )
    _reset_template_upgrade_logger()

    log = template_upgrade.setup_logging("INFO")

    # The file handler should have been skipped; stderr stays.
    assert not any(isinstance(h, logging.FileHandler) for h in log.handlers)
    assert any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
        for h in log.handlers
    )


def test_setup_logging_agent_logger_is_file_only(tmp_path, monkeypatch) -> None:
    """Agent output logger should only write to file, not stderr."""
    monkeypatch.setattr(template_upgrade, "setup_logging", _REAL_SETUP_LOGGING)
    monkeypatch.setattr(
        template_upgrade,
        "LOG_PATH",
        str(tmp_path / "qvm-template-upgrade.log"),
    )
    _reset_template_upgrade_logger()

    template_upgrade.setup_logging("DEBUG")

    agent_log = logging.getLogger(template_upgrade.AGENT_LOGGER)
    assert agent_log.propagate is False
    assert agent_log.handlers  # the shared file handler
    assert all(isinstance(h, logging.FileHandler) for h in agent_log.handlers)


def test_agent_output_logs_progress_ticks_at_debug() -> None:
    """Progress StatusInfo ticks must land in the log at DEBUG so a run's
    progress stream (monotonicity, where it stalled) can still be audited
    afterwards, even without a progress bar."""
    log = MagicMock()
    sink = template_upgrade._AgentOutput(log)
    qube = Mock()
    qube.name = "fedora-42-xfce"

    sink.put(StatusInfo.updating(qube, 42.5))
    sink.put(FormatedLine("fedora-42-xfce", "out", "hello"))

    log.debug.assert_called_once_with("%s progress: %s", "fedora-42-xfce", 42.5)
    log.info.assert_called_once()


# _AgentOutput -- live progress bar


def _bar_sink() -> tuple[MagicMock, MagicMock, template_upgrade._AgentOutput]:
    """An _AgentOutput with mock log and mock bar, bar at 0%."""
    log = MagicMock()
    bar = MagicMock()
    bar.n = 0.0
    return log, bar, template_upgrade._AgentOutput(log, progress_bar=bar)


def test_agent_output_advances_progress_bar() -> None:
    _log, bar, sink = _bar_sink()
    qube = Mock()
    qube.name = "fedora-42-xfce"

    sink.put(StatusInfo.updating(qube, 25.0))

    bar.update.assert_called_once_with(25.0)


def test_agent_output_bar_never_regresses() -> None:
    """The transport can replay a lower percent (phase handoffs round down);
    the bar must only ever move forward."""
    _log, bar, sink = _bar_sink()
    bar.n = 50.0
    qube = Mock()
    qube.name = "fedora-42-xfce"

    sink.put(StatusInfo.updating(qube, 40.0))

    bar.update.assert_not_called()


def test_agent_output_bar_ignores_non_numeric_status() -> None:
    """done/pending StatusInfo carries a FinalStatus or None, not a percent;
    it must not be fed into the bar arithmetic."""
    _log, bar, sink = _bar_sink()
    qube = Mock()
    qube.name = "fedora-42-xfce"

    sink.put(StatusInfo.pending(qube))
    sink.put(StatusInfo.done(qube, FinalStatus.SUCCESS))

    bar.update.assert_not_called()


def test_agent_output_lines_print_above_the_bar() -> None:
    """Streamed lines go through the bar's write so tqdm reprints the bar
    below each line instead of letting the line overwrite it."""
    log, bar, sink = _bar_sink()

    sink.put(FormatedLine("fedora-42-xfce", "out", "hello"))

    log.info.assert_called_once()
    bar.write.assert_called_once_with("fedora-42-xfce:out: hello")
    bar.update.assert_not_called()


def test_agent_output_streams_lines_without_a_bar(capsys) -> None:
    """With no bar, agent lines stream to stdout (matching the
    qubes-vm-update convention) and still land in the log."""
    log = MagicMock()
    sink = template_upgrade._AgentOutput(log)

    sink.put(FormatedLine("fedora-42-xfce", "out", "Installing: bash"))

    assert "Installing: bash" in capsys.readouterr().out
    log.info.assert_called_once()


def test_lines_stream_to_stdout_while_ticks_drive_the_bar(capsys) -> None:
    """Verify stdout line streaming alongside progress bar updates."""

    class FakeTty(io.StringIO):
        def isatty(self) -> bool:  # tqdm asks the output stream
            return True

    out = FakeTty()
    bar = tqdm(
        total=100.0,
        desc="fedora-42-xfce (fedora 41 -> 42)",
        file=out,
        bar_format="{desc} {percentage:5.1f}% |{bar}| [{elapsed}]",
        mininterval=0,  # count every redraw; no wall-clock coalescing
    )
    sink = template_upgrade._AgentOutput(MagicMock(), progress_bar=bar)
    qube = Mock()
    qube.name = "fedora-42-xfce"

    ticks = 100
    lines_per_tick = 50  # 5000 package lines total
    for i in range(ticks):
        sink.put(StatusInfo.updating(qube, float(i + 1)))
        for j in range(lines_per_tick):
            sink.put(
                FormatedLine("fedora-42-xfce", "out", f"Installing pkg-{i}-{j}")
            )
    sink.close()

    assert out.isatty() is True
    rendered = out.getvalue()
    streamed = capsys.readouterr().out
    assert rendered.count("\r") <= ticks + 5
    assert "Installing" not in rendered
    assert streamed.count("Installing") == ticks * lines_per_tick


def test_agent_output_beat_returns_when_bar_is_none() -> None:
    """_beat safely exits if progress_bar is None."""
    sink = template_upgrade._AgentOutput(MagicMock(), progress_bar=None)
    sink._beat()


def test_agent_output_put_handles_bar_disappearing() -> None:
    """put safely exits if progress_bar becomes None before lock acquisition."""
    sink = template_upgrade._AgentOutput(MagicMock(), progress_bar=MagicMock())
    qube = Mock()
    qube.name = "fedora-42-xfce"

    def clear_bar():
        sink.progress_bar = None

    mock_lock = MagicMock()
    mock_lock.__enter__.side_effect = clear_bar
    sink._bar_lock = mock_lock
    sink.put(StatusInfo.updating(qube, 50.0))


def test_agent_output_close_when_bar_lock_acquisition_times_out() -> None:
    """close logs a debug message and exits if acquiring _bar_lock times out."""
    log = MagicMock()
    bar = MagicMock()
    sink = template_upgrade._AgentOutput(log, progress_bar=bar)
    mock_lock = MagicMock()
    mock_lock.acquire.return_value = False
    sink._bar_lock = mock_lock
    sink.close()

    log.debug.assert_called_with(
        "progress bar busy; leaving its line unfinished"
    )
    bar.close.assert_not_called()


# _AgentOutput -- heartbeat that keeps an idle bar alive


@pytest.mark.parametrize(
    "quiet, expected",
    [
        (0.0, ""),
        # Comfortably below QUIET_AFTER: a tighter margin would turn
        # scheduler jitter into a spurious failure.
        (25.0, ""),
        # The recorded fedora 41 -> 42 run's two dead zones: 59s on 0.0% and
        # 3m41s on 99.9%.
        (59.0, "waiting 59s"),
        (221.0, "waiting 3m41s"),
        (3600.0, "waiting 60m00s"),
    ],
)
def test_quiet_note_reports_a_stalled_percentage(quiet, expected) -> None:
    _log, _bar, sink = _bar_sink()
    sink._last_advance = time.monotonic() - quiet

    assert sink._quiet_note() == expected


def test_heartbeat_repaints_a_bar_the_agent_has_stopped_feeding(
    monkeypatch,
) -> None:
    """The elapsed clock must keep moving while the agent is silent, or a
    long transaction is indistinguishable from a hang."""
    monkeypatch.setattr(
        template_upgrade._AgentOutput, "HEARTBEAT_INTERVAL", 0.01
    )
    out = io.StringIO()
    bar = tqdm(
        total=100.0,
        desc="fedora-42-xfce",
        file=out,
        bar_format="{desc} {percentage:5.1f}% |{bar}| [{elapsed}]{postfix}",
        mininterval=0,
    )
    sink = template_upgrade._AgentOutput(MagicMock(), progress_bar=bar)

    sink.start()
    try:
        deadline = time.monotonic() + 5.0
        # No put() at all: every redraw here comes from the heartbeat.
        while out.getvalue().count("\r") < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        sink.close()

    assert out.getvalue().count("\r") >= 3


def test_waiting_note_renders_with_a_single_separator() -> None:
    """tqdm prepends ", " to a non-empty postfix itself, so _quiet_note must
    not add its own spacing on top of it."""
    out = io.StringIO()
    bar = tqdm(
        total=100.0,
        desc="fedora-42-xfce",
        file=out,
        bar_format="{desc} {percentage:5.1f}% |{bar:10}| [{elapsed}]{postfix}",
        mininterval=0,
        ncols=80,
    )
    sink = template_upgrade._AgentOutput(MagicMock(), progress_bar=bar)
    sink._last_advance = time.monotonic() - 221

    bar.set_postfix_str(sink._quiet_note(), refresh=False)
    bar.refresh()
    frames = [f for f in out.getvalue().split("\r") if f.strip()]
    sink.close()

    # The elapsed clock is real time and not the point here; what must hold
    # is that exactly one ", " separates it from the note.
    assert re.search(r"\[\d\d:\d\d\], waiting 3m41s$", frames[-1]), frames[-1]


def test_heartbeat_survives_nothing_but_stops_on_a_broken_terminal(
    monkeypatch,
) -> None:
    """Heartbeat thread terminates cleanly on terminal write errors."""
    monkeypatch.setattr(
        template_upgrade._AgentOutput, "HEARTBEAT_INTERVAL", 0.01
    )
    log = MagicMock()
    bar = MagicMock()
    bar.n = 0.0
    bar.refresh.side_effect = OSError("terminal went away")
    sink = template_upgrade._AgentOutput(log, progress_bar=bar)

    sink.start()
    thread = sink._heartbeat
    assert thread is not None
    thread.join(timeout=5.0)

    assert not thread.is_alive(), "heartbeat should stop, not spin on errors"
    log.debug.assert_called_once()
    assert "heartbeat" in log.debug.call_args.args[0]
    sink.close()


def test_final_line_carries_no_waiting_residue() -> None:
    """Whatever the bar said while stalled, the line left on screen after
    close() must not still claim to be waiting."""
    out = io.StringIO()
    bar = tqdm(
        total=100.0,
        desc="fedora-42-xfce",
        file=out,
        bar_format="{desc} {percentage:5.1f}% |{bar:10}| [{elapsed}]{postfix}",
        mininterval=0,
        ncols=80,
    )
    sink = template_upgrade._AgentOutput(MagicMock(), progress_bar=bar)
    sink._last_advance = time.monotonic() - 300
    bar.set_postfix_str(sink._quiet_note(), refresh=False)
    bar.refresh()

    sink.close()

    assert "waiting" not in out.getvalue().split("\r")[-1]


def test_heartbeat_is_not_started_without_a_bar() -> None:
    """Non-TTY runs have no bar to repaint."""
    sink = template_upgrade._AgentOutput(MagicMock())

    sink.start()

    assert sink._heartbeat is None
    sink.close()


def test_close_stops_the_heartbeat_thread(monkeypatch) -> None:
    monkeypatch.setattr(
        template_upgrade._AgentOutput, "HEARTBEAT_INTERVAL", 0.01
    )
    _log, bar, sink = _bar_sink()

    sink.start()
    thread = sink._heartbeat
    assert thread is not None and thread.is_alive()
    sink.close()

    assert not thread.is_alive()
    assert sink._heartbeat is None


def test_progress_clears_the_waiting_note() -> None:
    """Once the percentage moves again the bar must stop claiming to wait."""
    _log, bar, sink = _bar_sink()
    qube = Mock()
    qube.name = "fedora-42-xfce"
    sink._last_advance = time.monotonic() - 300

    sink.put(StatusInfo.updating(qube, 25.0))

    bar.set_postfix_str.assert_called_once_with("", refresh=False)
    assert sink._quiet_note() == ""


def test_agent_output_close_closes_bar_once() -> None:
    _log, bar, sink = _bar_sink()

    sink.close()
    sink.close()

    bar.close.assert_called_once_with()


def test_make_progress_bar_skipped_when_stderr_not_a_tty(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        template_upgrade.sys.stderr, "isatty", lambda: False, raising=False
    )
    assert template_upgrade.make_progress_bar("desc") is None


def test_make_progress_bar_on_a_tty(monkeypatch) -> None:
    monkeypatch.setattr(
        template_upgrade.sys.stderr, "isatty", lambda: True, raising=False
    )
    bar = template_upgrade.make_progress_bar("desc")
    try:
        assert bar is not None
        assert bar.total == 100.0
        # A half-hour bar has to survive the terminal being resized.
        assert bar.dynamic_ncols
    finally:
        if bar is not None:
            bar.close()


def test_run_agent_closes_bar_even_on_failure(monkeypatch) -> None:
    """The bar's in-place line must be finished before rollback/error output
    prints, or the messages land on top of it."""
    app = CloneApp()
    add_template(app)
    bar = MagicMock()
    bar.n = 0.0
    monkeypatch.setattr(
        template_upgrade, "make_progress_bar", lambda *a, **kw: bar
    )

    def fake_update_qube(
        qube, agent_args, **kwargs
    ) -> tuple[str, ProcessResult]:
        return qube.name, ProcessResult(EXIT.ERR_VM_UPDATE)

    monkeypatch.setattr(template_upgrade, "update_qube", fake_update_qube)

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.ERR
    bar.close.assert_called_once_with()


def test_default_run_streams_output(monkeypatch, capsys) -> None:
    """Agent lines stream to stdout by default, so a failure needs no
    separate output replay."""
    app = CloneApp()
    add_template(app)

    def fake_update_qube(qube, agent_args, **kwargs):
        kwargs["status_notifier"].put(
            FormatedLine(qube.name, "out", "Installing: bash")
        )
        return qube.name, ProcessResult(EXIT.OK)

    monkeypatch.setattr(
        template_upgrade, "make_progress_bar", lambda description: None
    )
    monkeypatch.setattr(template_upgrade, "update_qube", fake_update_qube)

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.OK
    assert "Installing: bash" in capsys.readouterr().out


# disk-space warning suppression around the agent run


def _capture_disk_feature(captured, code=EXIT.OK):
    """A fake update_qube that records the clone and the suppression
    feature's value at the moment the agent runs."""

    def fake_update_qube(
        qube, agent_args, **kwargs
    ) -> tuple[str, ProcessResult]:
        captured["qube"] = qube
        captured["during"] = qube.features.get(
            template_upgrade.DISK_SPACE_NOTIFY_FEATURE
        )
        return qube.name, ProcessResult(code)

    return fake_update_qube


def test_disk_space_warning_suppressed_during_agent_run(monkeypatch) -> None:
    """The clone's qui-disk-space warning is off while the agent runs and
    the feature is absent again after a successful run."""
    app = CloneApp()
    add_template(app)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        template_upgrade, "update_qube", _capture_disk_feature(captured)
    )

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.OK
    assert captured["during"] == "1"
    clone = app.domains["fedora-42"]
    assert template_upgrade.DISK_SPACE_NOTIFY_FEATURE not in clone.features


def test_disk_space_warning_prior_value_restored(monkeypatch) -> None:
    """A pre-existing feature value on the source (inherited by the clone)
    is restored after the run instead of being deleted."""
    app = CloneApp()
    add_template(app, **{template_upgrade.DISK_SPACE_NOTIFY_FEATURE: "0"})
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        template_upgrade, "update_qube", _capture_disk_feature(captured)
    )

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.OK
    assert captured["during"] == "1"
    clone = app.domains["fedora-42"]
    assert clone.features[template_upgrade.DISK_SPACE_NOTIFY_FEATURE] == "0"


def test_disk_space_warning_restored_on_agent_failure(monkeypatch) -> None:
    """The finally path restores the feature even when the agent fails."""
    app = CloneApp()
    add_template(app)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        template_upgrade,
        "update_qube",
        _capture_disk_feature(captured, code=EXIT.ERR_VM_UPDATE),
    )

    retcode = template_upgrade.main(
        ["--template", "fedora-41", "--keep-new-on-failure"], app
    )

    assert retcode == EXIT.ERR
    assert captured["during"] == "1"
    clone = captured["qube"]
    assert template_upgrade.DISK_SPACE_NOTIFY_FEATURE not in clone.features


def test_disk_space_suppression_failure_does_not_block_agent(
    monkeypatch,
) -> None:
    """If setting the feature raises, the agent still runs and the upgrade succeeds."""

    class BoomFeatures(dict):
        def __setitem__(self, key, value) -> None:
            if key == template_upgrade.DISK_SPACE_NOTIFY_FEATURE:
                raise qubesadmin.exc.QubesException("features unavailable")
            super().__setitem__(key, value)

    class BoomApp(CloneApp):
        def clone_vm(self, source_vm, new_name) -> _TestVM:
            clone = super().clone_vm(source_vm, new_name)
            clone.features = BoomFeatures(clone.features)
            return clone

    app = BoomApp()
    add_template(app)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        template_upgrade, "update_qube", _capture_disk_feature(captured)
    )

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.OK
    assert captured["during"] is None  # never got suppressed
    assert captured["qube"].name == "fedora-42"


def test_disk_space_warning_restore_error_handled(monkeypatch) -> None:
    """If restoring features raises QubesException, a warning is logged without failing."""

    class DeleteBoomFeatures(dict):
        def __delitem__(self, key) -> None:
            raise qubesadmin.exc.QubesException("cannot delete feature")

    class BoomApp(CloneApp):
        def clone_vm(self, source_vm, new_name) -> _TestVM:
            clone = super().clone_vm(source_vm, new_name)
            clone.features = DeleteBoomFeatures(clone.features)
            return clone

    app = BoomApp()
    add_template(app)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        template_upgrade, "update_qube", _capture_disk_feature(captured)
    )

    retcode = template_upgrade.main(["--template", "fedora-41"], app)
    assert retcode == EXIT.OK


# CLI arguments


def test_version_reports_vmupdate_package(capsys) -> None:
    with patch.object(
        template_upgrade.metadata, "version", return_value="4.4.1"
    ) as package_version:
        with pytest.raises(SystemExit) as excinfo:
            template_upgrade.get_parser().parse_args(["--version"])

    assert excinfo.value.code == 0
    assert capsys.readouterr().out == "qvm-template-upgrade 4.4.1\n"
    package_version.assert_called_once_with("qubes-vmupdate")


def test_version_tolerates_uninstalled_package(capsys) -> None:
    """A source checkout without package metadata (CI, PYTHONPATH runs in
    dom0) must still build the parser and answer --version."""
    with patch.object(
        template_upgrade.metadata,
        "version",
        side_effect=template_upgrade.metadata.PackageNotFoundError,
    ):
        with pytest.raises(SystemExit) as excinfo:
            template_upgrade.get_parser().parse_args(["--version"])

    assert excinfo.value.code == 0
    assert capsys.readouterr().out == "qvm-template-upgrade unknown\n"


def test_log_level_is_case_insensitive() -> None:
    app = CloneApp()
    add_template(app)

    retcode = template_upgrade.main(
        ["--template", "fedora-41", "--dry-run", "--log", "debug"], app
    )

    assert retcode == EXIT.OK


def test_invalid_log_level_is_a_usage_error(capsys) -> None:
    """A bad --log value is an argparse error, not a raw ValueError from
    Logger.setLevel."""
    app = CloneApp()
    add_template(app)

    with pytest.raises(SystemExit) as excinfo:
        template_upgrade.main(
            ["--template", "fedora-41", "--log", "VERBOSE"], app
        )

    assert excinfo.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_keyboard_interrupt_returns_sigint(monkeypatch, capsys) -> None:
    """Ctrl+C during the upgrade returns 130 as the manpage documents and
    warns that the clone may remain."""
    app = CloneApp()
    add_template(app)

    def interrupt(self) -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        template_upgrade.TemplateUpgrader, "run_agent", interrupt
    )

    retcode = template_upgrade.main(["--template", "fedora-41"], app)

    assert retcode == EXIT.SIGINT == 130
    assert "interrupted" in capsys.readouterr().err
    # The clone is left for the user to inspect or remove.
    assert "fedora-42" in app.domains
