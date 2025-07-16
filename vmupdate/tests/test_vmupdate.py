# coding=utf-8
#
# The Qubes OS Project, https://www.qubes-os.org
#
# Copyright (C) 2024  Piotr Bartman-Szwarc <prbartman@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301,
# USA.
import itertools

from unittest.mock import patch

import pytest

import qubesadmin
from vmupdate.agent.source.common.exit_codes import EXIT
from vmupdate.tests.conftest import generate_vm_variations, TestVM, Features
from vmupdate.agent.source.status import FinalStatus
from vmupdate.vmupdate import main
from vmupdate import vmupdate


@patch('os.chmod')
@patch('os.chown')
@patch('logging.FileHandler')
@patch('logging.getLogger')
def test_no_options_do_nothing(_logger, _log_file, _chmod, _chown, test_qapp):
    test_qapp.domains = test_qapp.Domains()
    TestVM("dom0", test_qapp, klass="AdminVM")
    args = []
    assert main(args, test_qapp) == EXIT.OK
    args = ['--signal-no-updates']
    assert main(args, test_qapp) == EXIT.OK_NO_UPDATES


@patch('vmupdate.update_manager.TerminalMultiBar.print')
@patch('os.chmod')
@patch('os.chown')
@patch('logging.FileHandler')
@patch('logging.getLogger')
@patch('vmupdate.update_manager.UpdateAgentManager')
@patch('multiprocessing.Pool')
@patch('multiprocessing.Manager')
def test_preselection(
        mp_manager, mp_pool, agent_mng,
        _logger, _log_file, _chmod, _chown, _print,
        test_qapp, test_manager, test_pool, test_agent,
):
    mp_manager.return_value = test_manager
    mp_pool.return_value = test_pool

    domains = generate_vm_variations(
        test_qapp, ["klass", "updatable", "is_running"])

    updatable = domains["updatable"][True]
    is_running = domains["is_running"][True]
    admin = domains["klass"]["AdminVM"]
    templ = domains["klass"]["TemplateVM"]
    stand = domains["klass"]["StandaloneVM"]
    app = domains["klass"]["AppVM"]
    disp = domains["klass"]["DispVM"]
    run_app = app & is_running
    default = updatable & ((templ | stand) | (is_running & (disp | app)))

    AdminVM = next(iter(admin))
    TemplVM = next(iter(templ))
    StandVM = next(iter(stand))
    UpStandVM = next(iter(updatable & stand))
    NUpStandVM = next(iter(domains["updatable"][False] & stand))
    NRunAppVM = next(iter(domains["is_running"][False] & app))

    expected = {
        (): default,
        ("--skip", UpStandVM.name): default - {UpStandVM},
        ("--all",): default,
        ("--all", "--apps",): default,
        ("--all", "--templates",): default,
        ("--all", "--standalones",): default,
        ("--all", "--skip", UpStandVM.name,): default - {UpStandVM},
        ("--all", "--targets", UpStandVM.name,): default,
        ("--all", "--targets", NRunAppVM.name,): default | {NRunAppVM},
        ("--all", "--targets", NUpStandVM.name,): default | {NUpStandVM},
        ("--apps",): app & is_running,
        ("--templates",): updatable & templ,
        ("--standalones",): updatable & stand,
        ("--templates", "--apps",): (updatable & templ) | run_app,
        ("--templates", "--standalones",): updatable & (templ | stand),
        ("--templates", "--standalones", "--apps",):
            (updatable & (templ | stand)) | (app & is_running),
        ("--standalones", "--skip", StandVM.name,):
            (updatable & stand) - {StandVM},
        ("--standalones", "--skip", TemplVM.name,): (updatable & stand),
        ("--standalones", "--targets", UpStandVM.name,): (updatable & stand),
        ("--standalones", "--targets", NUpStandVM.name,):
            (updatable & stand) | {NUpStandVM},
        ("--standalones", "--targets", TemplVM.name,):
            (updatable & stand) | {TemplVM},
        ("--apps", "--skip", NRunAppVM.name,): run_app,
        ("--apps", "--skip", StandVM.name,): run_app,
        ("--apps", "--targets", NRunAppVM.name,): run_app | {NRunAppVM},
        ("--apps", "--targets", TemplVM.name,): run_app | {TemplVM},
        ("--targets", NRunAppVM.name,): {NRunAppVM},
        ("--targets", StandVM.name,): {StandVM},
        # dom0 skipped, user warning
        ("--targets", AdminVM.name,): EXIT.OK_NO_UPDATES,
        ("--targets", "unknown",): EXIT.ERR_USAGE,
        ("--targets", f"{TemplVM.name},{StandVM.name}",): {TemplVM, StandVM},
        ("--targets", f"{TemplVM.name},{TemplVM.name}",): EXIT.ERR_USAGE,
        ("--targets", TemplVM.name, "--skip", TemplVM.name,): {},
        ("--targets", f"{TemplVM.name},{StandVM.name}", "--skip", TemplVM.name,): {StandVM},
    }

    failed = {}
    for args, selected in expected.items():
        if isinstance(selected, int):
            feed = {}
            expected_exit = selected
        else:
            feed = {vm.name: {'statuses': [FinalStatus.SUCCESS],
                              'retcode': EXIT.OK}
                    for vm in selected}
            if feed:
                expected_exit = EXIT.OK
            else:
                expected_exit = EXIT.OK_NO_UPDATES

        unexpected = []
        agent_mng.side_effect = test_agent(feed, unexpected)
        retcode = main(("--force-update", "--just-print-progress", *args), test_qapp)

        failed[args] = {}
        if retcode != expected_exit:
            failed[args]["unexpected exit code"] = retcode
        failed[args]["unexpected vm"] = unexpected
        failed[args]["leftover feed"] = feed
        failed[args] = {key: value
                        for key, value in failed[args].items() if value}

    fails = {args: failed[args] for args in failed if failed[args]}
    assert not fails


@patch('vmupdate.update_manager.TerminalMultiBar.print')
@patch('os.chmod')
@patch('os.chown')
@patch('logging.FileHandler')
@patch('logging.getLogger')
@patch('vmupdate.update_manager.UpdateAgentManager')
@patch('multiprocessing.Pool')
@patch('multiprocessing.Manager')
def test_selection(
        mp_manager, mp_pool, agent_mng,
        _logger, _log_file, _chmod, _chown, _print,
        test_qapp, test_manager, test_pool, test_agent,
        monkeypatch
):
    mp_manager.return_value = test_manager
    mp_pool.return_value = test_pool

    domains = generate_vm_variations(
        test_qapp,
        ["klass", "updates_available", "last_updates_check", "qrexec", "os"])

    all = domains["updatable"][True]
    qlinux = domains["qrexec"][True] & domains["os"]["Linux"]
    to_update = domains["updates_available"][True]
    stale = qlinux & (domains["updates_available"][False] &
             (domains["last_updates_check"][None] |
             domains["last_updates_check"]['2020-01-01 00:00:00']))

    expected = {
        ("--force-update",): all,
        (): to_update | stale,
        ("--update-if-stale", "0"): to_update | stale,
        ("--update-if-stale", "1"): to_update | stale,
        ("--update-if-stale", "7"): to_update | stale,
        ("--update-if-stale", "365"): to_update | stale,
        ("--update-if-available",): to_update,
    }

    failed = {}
    for args, selected in expected.items():
        if isinstance(selected, int):
            feed = {}
            expected_exit = selected
            monkeypatch.setattr(
                vmupdate, "preselect_targets", lambda *_: all)
        else:
            feed = {vm.name: {'statuses': [FinalStatus.SUCCESS],
                              'retcode': EXIT.OK}
                    for vm in selected}
            monkeypatch.setattr(
                vmupdate, "preselect_targets", lambda *_: selected)
            if feed:
                expected_exit = EXIT.OK
            else:
                expected_exit = EXIT.OK_NO_UPDATES

        unexpected = []
        agent_mng.side_effect = test_agent(feed, unexpected)
        retcode = main(("--just-print-progress", *args), test_qapp)

        failed[args] = {}
        if retcode != expected_exit:
            failed[args]["unexpected exit code"] = retcode
        failed[args]["unexpected vm"] = unexpected
        failed[args]["leftover feed"] = feed
        failed[args] = {key: value
                        for key, value in failed[args].items() if value}

    fails = {args: failed[args] for args in failed if failed[args]}
    assert not fails


@patch('vmupdate.update_manager.TerminalMultiBar.print')
@patch('os.chmod')
@patch('os.chown')
@patch('logging.FileHandler')
@patch('logging.getLogger')
@patch('vmupdate.update_manager.UpdateAgentManager')
@patch('multiprocessing.Pool')
@patch('multiprocessing.Manager')
@patch('asyncio.run')
def test_restarting(
        arun, mp_manager, mp_pool, agent_mng,
        _logger, _log_file, _chmod, _chown, _print,
        test_qapp, test_manager, test_pool, test_agent,
        monkeypatch
):
    mp_manager.return_value = test_manager
    mp_pool.return_value = test_pool

    domains = generate_vm_variations(
        test_qapp,
        ["klass", "is_running", "servicevm", "auto_cleanup",
         "updated", "has_template_updated"])

    all = domains["updatable"][True]
    service = domains["servicevm"][True]
    disp = domains["klass"]["DispVM"]
    app = domains["klass"]["AppVM"]
    templ = domains["klass"]["TemplateVM"]
    derived = disp | app
    auto_cleanup = domains["auto_cleanup"][True]
    updated = domains["updated"][FinalStatus.SUCCESS]
    not_updated = all - domains["updated"][FinalStatus.SUCCESS]
    running = domains["is_running"][True]
    template_updated = domains["has_template_updated"][FinalStatus.SUCCESS]
    applicable = (derived & not_updated & running & template_updated
                  ) - (auto_cleanup & disp)

    expected = {
        (): {"halted": set(),
             "restarted": set(),
             "untouched": all},
        ("--no-apply",): {
            "halted": set(),
            "restarted": set(),
            "untouched": all},
        ("--apply-to-sys",): {
            "halted": updated & running & templ,
            "restarted": applicable & service,
            "untouched": all - (updated & running & templ) - (applicable & service)},
        ("--apply-to-all",): {
            "halted": (updated & running & templ) | (applicable - service),
            "restarted": applicable & service,
            "untouched":  all - (updated & running & templ) - applicable},
    }

    failed = {}
    for args, selected in expected.items():
        monkeypatch.setattr(vmupdate, "get_targets", lambda *_: all)
        feed = {vm.name: {'statuses': [vm.update_result],
                          'retcode': None}  # we don't care
                for vm in all}

        unexpected = []
        agent_mng.side_effect = test_agent(feed, unexpected)
        main(("--just-print-progress", *args), test_qapp)

        failed[args] = {}

        failed[args]["unexpected vm"] = unexpected
        failed[args]["leftover feed"] = feed

        halted = {vm for vm in all
                  if vm.shutdown.called and not vm.start.called}
        restarted = {vm for vm in all
                     if vm.shutdown.called and vm.start.called}
        untouched = {vm for vm in all
                     if not vm.shutdown.called and not vm.start.called}
        failed[args]["unexpected restart"] = set(map(
            lambda vm: vm.name, restarted - selected["restarted"]))
        failed[args]["not restarted"] = set(map(
            lambda vm: vm.name, selected["restarted"] - restarted))
        failed[args]["unexpected shutdown"] = set(map(
            lambda vm: vm.name, halted - selected["halted"]))
        failed[args]["not halted"] = set(map(
            lambda vm: vm.name, selected["halted"] - halted))
        failed[args]["unexpected untouched"] = set(map(
            lambda vm: vm.name, untouched - selected["untouched"]))
        failed[args]["unexpected touched"] = set(map(
            lambda vm: vm.name, selected["untouched"] - untouched))

        failed[args] = {key: value
                        for key, value in failed[args].items() if value}

    fails = {args: failed[args] for args in failed if failed[args]}
    assert not fails
    arun.asseert_called()


stat = FinalStatus


@patch('vmupdate.update_manager.TerminalMultiBar.print')
@patch('os.chmod')
@patch('os.chown')
@patch('logging.FileHandler')
@patch('logging.getLogger')
@patch('vmupdate.update_manager.UpdateAgentManager')
@patch('multiprocessing.Pool')
@patch('multiprocessing.Manager')
@pytest.mark.parametrize(
    "tmpl_status, tmpl_retcode, app_status, app_retcode, expected_retcode",
(
    pytest.param(
        stat.NO_UPDATES, EXIT.OK, stat.NO_UPDATES, EXIT.OK,
        EXIT.OK_NO_UPDATES, id="no updates: 2x OK"),
    pytest.param(
        stat.NO_UPDATES, EXIT.OK, stat.NO_UPDATES, EXIT.OK,
        EXIT.OK_NO_UPDATES, id="no updates: tmpl OK"),
    pytest.param(
        stat.NO_UPDATES, EXIT.OK, stat.NO_UPDATES, EXIT.OK,
        EXIT.OK_NO_UPDATES, id="no updates: app OK"),
    pytest.param(
        stat.ERROR, EXIT.OK, stat.NO_UPDATES, EXIT.OK,
        EXIT.ERR, id="error: tmpl"),
    pytest.param(
        stat.SUCCESS, EXIT.OK, stat.ERROR, EXIT.OK,
        EXIT.ERR, id="error: app"),
    pytest.param(
        stat.SUCCESS, EXIT.OK_NO_UPDATES, stat.SUCCESS, EXIT.OK,
        EXIT.ERR_VM_UNHANDLED, id="unhandled retcode"),
    pytest.param(
        stat.SUCCESS, EXIT.ERR_VM, stat.ERROR, EXIT.ERR_VM_PRE,
        EXIT.ERR_VM_PRE, id="vm inside error"),
    pytest.param(
        stat.SUCCESS, EXIT.ERR_VM_UPDATE, stat.ERROR, EXIT.ERR_VM_REFRESH,
        EXIT.ERR_VM_UPDATE, id="vm inside error 2"),
    pytest.param(
        stat.SUCCESS, EXIT.ERR_VM, stat.SUCCESS, EXIT.OK,
        EXIT.ERR_VM, id="vm general inside error"),
    pytest.param(
        stat.CANCELLED, EXIT.OK, stat.SUCCESS, EXIT.OK,
        EXIT.SIGINT, id="cancelled"),
    pytest.param(
        stat.CANCELLED, EXIT.OK, stat.ERROR, EXIT.ERR_VM_UNHANDLED,
        EXIT.SIGINT, id="cancelled with error"),
    pytest.param(
        stat.UNKNOWN, EXIT.OK, stat.SUCCESS, EXIT.OK,
        EXIT.ERR_QREXEX, id="communication error"),
))
def test_return_codes(
        mp_manager, mp_pool, agent_mng,
        _logger, _log_file, _chmod, _chown, _print,
        test_qapp, test_manager, test_pool, test_agent,
        monkeypatch,
        tmpl_status, tmpl_retcode, app_status, app_retcode, expected_retcode
):
    mp_manager.return_value = test_manager
    mp_pool.return_value = test_pool

    _dom0 = TestVM("dom0", test_qapp, klass="AdminVM")
    vm = TestVM("vm", test_qapp, klass="TemplateVM")
    appvm = TestVM("appvm", test_qapp, klass="AppVM", template=vm)

    feed = {
        vm.name: {'statuses': [tmpl_status], 'retcode': tmpl_retcode},
        appvm.name: {'statuses': [app_status], 'retcode': app_retcode}}
    unexpected = []
    agent_mng.side_effect = test_agent(feed, unexpected)

    monkeypatch.setattr(vmupdate, "get_targets", lambda *_: [vm, appvm])

    retcode = main(
        ("--just-print-progress", "--all", "--force-update",
         "--signal-no-updates"), test_qapp)
    assert retcode == expected_retcode


@patch('vmupdate.update_manager.TerminalMultiBar.print')
@patch('os.chmod')
@patch('os.chown')
@patch('logging.FileHandler')
@patch('logging.getLogger')
@patch('vmupdate.update_manager.UpdateAgentManager')
@patch('multiprocessing.Pool')
@patch('multiprocessing.Manager')
def test_error(
        mp_manager, mp_pool, agent_mng,
        _logger, _log_file, _chmod, _chown, _print,
        test_qapp, test_manager, test_pool, test_agent,
        monkeypatch
):
    mp_manager.return_value = test_manager
    mp_pool.return_value = test_pool

    _dom0 = TestVM("dom0", test_qapp, klass="AdminVM")
    vm = TestVM("vm", test_qapp, klass="TemplateVM")
    appvm = TestVM("appvm", test_qapp, klass="AppVM", template=vm)

    feed = {
        vm.name: {'statuses': [FinalStatus.ERROR], 'retcode': EXIT.OK},
        appvm.name: {'statuses': [FinalStatus.NO_UPDATES], 'retcode': EXIT.OK}}
    unexpected = []
    agent_mng.side_effect = test_agent(feed, unexpected)

    monkeypatch.setattr(vmupdate, "get_targets", lambda *_: [vm, appvm])

    retcode = main((
        "--just-print-progress", "--all", "--force-update"), test_qapp)
    assert retcode == EXIT.ERR


@patch('vmupdate.update_manager.TerminalMultiBar.print')
@patch('os.chmod')
@patch('os.chown')
@patch('logging.FileHandler')
@patch('logging.getLogger')
@patch('asyncio.run')
@pytest.mark.parametrize(
    "action, code",
(
    pytest.param("template shutdown", EXIT.ERR_SHUTDOWN_TMPL),
    pytest.param("app shutdown", EXIT.ERR_SHUTDOWN_APP),
    pytest.param("app start", EXIT.ERR_START_APP),
))
def test_error_apply(
        _arun, _logger, _log_file, _chmod, _chown, _print,
        test_qapp, monkeypatch, action, code
):
    _dom0 = TestVM("dom0", test_qapp, klass="AdminVM")
    vm = TestVM("vm", test_qapp, klass="TemplateVM")
    appvm = TestVM(
        "appvm", test_qapp, klass="AppVM", template=vm,
        features=Features("appvm", test_qapp, {'servicevm': True}))

    monkeypatch.setattr(vmupdate, "get_targets", lambda *_: [vm, appvm])
    monkeypatch.setattr(vmupdate, "run_update",
                        lambda *_: [EXIT.OK, {"vm": FinalStatus.SUCCESS}])

    def raiser(*_args, **_kwargs):
        raise qubesadmin.exc.QubesVMError("foo")
    if action == "template shutdown":
        vm.shutdown = raiser
    elif action == "app shutdown":
        appvm.shutdown = raiser
    elif action == "app start":
        appvm.start = raiser
    else:
        raise ValueError()

    retcode = main(("--all", "--force-update", "--apply-to-all"), test_qapp)
    assert retcode == code


@patch('vmupdate.update_manager.TerminalMultiBar.print')
@patch('os.chmod')
@patch('os.chown')
@patch('logging.FileHandler')
@patch('logging.getLogger')
def test_error_usage_wrong_param(
        _logger, _log_file, _chmod, _chown, _print, test_qapp,
):
    _dom0 = TestVM("dom0", test_qapp, klass="AdminVM")

    retcode = main((
        "--just-print-progress", "--targets", 'vm', "--force-update"),
        test_qapp)
    assert retcode == EXIT.ERR_USAGE
