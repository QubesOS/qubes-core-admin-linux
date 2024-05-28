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

from vmupdate.tests.conftest import generate_vm_variations
from vmupdate.agent.source.status import FinalStatus
from vmupdate.vmupdate import main
from vmupdate import vmupdate


@patch('os.chmod')
@patch('os.chown')
@patch('logging.FileHandler')
@patch('logging.getLogger')
def test_no_options_do_nothing(_logger, _log_file, _chmod, _chown, test_qapp):
    args = []
    test_qapp.domains = ()
    assert main(args, test_qapp) == 100


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
    run_up_app = updatable & app & is_running
    default = updatable & ((templ | stand) | (is_running & (disp | app)))

    AdminVM = next(iter(admin))
    TemplVM = next(iter(templ))
    StandVM = next(iter(stand))
    UpStandVM = next(iter(updatable & stand))
    NUpStandVM = next(iter(domains["updatable"][False] & stand))
    RunUpAppVM = next(iter(updatable & app & is_running))
    RunNUpAppVM = next(iter(domains["updatable"][False] & is_running & app))
    NRunAppVM = next(iter(domains["is_running"][False] & app))

    expected = {
        (): default,
        ("--all",): default,
        ("--all", "--apps",): default,
        ("--all", "--templates",): default,
        ("--all", "--standalones",): default,
        ("--all", "--skip", UpStandVM.name,): default - {UpStandVM},
        ("--all", "--targets", UpStandVM.name,): default,
        ("--all", "--targets", RunNUpAppVM.name,): default | {RunNUpAppVM},
        ("--all", "--targets", NRunAppVM.name,): default | {NRunAppVM},
        ("--all", "--targets", NUpStandVM.name,): default | {NUpStandVM},
        ("--apps",): updatable & app & is_running,
        ("--templates",): updatable & templ,
        ("--standalones",): updatable & stand,
        ("--templates", "--apps",): updatable & (templ | (app & is_running)),
        ("--templates", "--standalones",): updatable & (templ | stand),
        ("--templates", "--standalones", "--apps",):
            updatable & (templ | stand | (app & is_running)),
        ("--standalones", "--skip", StandVM.name,):
            (updatable & stand) - {StandVM},
        ("--standalones", "--skip", TemplVM.name,): (updatable & stand),
        ("--standalones", "--targets", UpStandVM.name,): (updatable & stand),
        ("--standalones", "--targets", NUpStandVM.name,):
            (updatable & stand) | {NUpStandVM},
        ("--standalones", "--targets", TemplVM.name,):
            (updatable & stand) | {TemplVM},
        ("--apps", "--skip", RunUpAppVM.name,):  run_up_app - {RunUpAppVM},
        ("--apps", "--skip", RunNUpAppVM.name,):  run_up_app,
        ("--apps", "--skip", NRunAppVM.name,): run_up_app,
        ("--apps", "--skip", StandVM.name,): run_up_app,
        ("--apps", "--targets", RunUpAppVM.name,):
            (updatable & app & is_running),
        ("--apps", "--targets", RunNUpAppVM.name,):
            (updatable & app & is_running) | {RunNUpAppVM},
        ("--apps", "--targets", NRunAppVM.name,):
            (updatable & app & is_running) | {NRunAppVM},
        ("--apps", "--targets", TemplVM.name,):
            (updatable & app & is_running) | {TemplVM},
        ("--targets", RunUpAppVM.name,): {RunUpAppVM},
        ("--targets", RunNUpAppVM.name,): {RunNUpAppVM},
        ("--targets", NRunAppVM.name,): {NRunAppVM},
        ("--targets", StandVM.name,): {StandVM},
        ("--targets", AdminVM.name,): 100,  # dom0 skipped, user warning
        ("--targets", "unknown",): 128,
        ("--targets", f"{TemplVM.name},{StandVM.name}",): {TemplVM, StandVM},
        ("--targets", f"{TemplVM.name},{TemplVM.name}",): 128,
        ("--targets", TemplVM.name, "--skip", TemplVM.name,): {},
        ("--targets", f"{TemplVM.name},{StandVM.name}", "--skip", TemplVM.name,): {StandVM},
    }

    failed = {}
    for args, selected in expected.items():
        if isinstance(selected, int):
            feed = {}
            expected_exit = selected
        else:
            feed = {vm.name: {'statuses': [FinalStatus.SUCCESS], 'retcode': 0}
                    for vm in selected}
            if feed:
                expected_exit = 0
            else:
                expected_exit = 100

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
            feed = {vm.name: {'statuses': [FinalStatus.SUCCESS], 'retcode': 0}
                    for vm in selected}
            monkeypatch.setattr(
                vmupdate, "preselect_targets", lambda *_: selected)
            if feed:
                expected_exit = 0
            else:
                expected_exit = 100

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
