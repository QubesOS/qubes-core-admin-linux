# coding=utf-8
#
# The Qubes OS Project, https://www.qubes-os.org
#
# Copyright (C) 2026  The Qubes OS Project
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

import logging
import shutil
import sys
from pathlib import Path

import pytest


AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from source.common.exit_codes import EXIT
from source.common.package_manager import AgentType
from source.common.process_result import ProcessResult
from source.guix.guix_cli import GUIXCLI
from source import utils
import entrypoint


def make_executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    path.chmod(0o755)
    return str(path)


def make_manager(tmp_path, monkeypatch):
    guix = make_executable(tmp_path / "guix")
    service_dir = tmp_path / "qubes-service"

    monkeypatch.setattr(GUIXCLI, "GUIX_CANDIDATES", (guix,))
    monkeypatch.setattr(GUIXCLI, "SERVICE_DIR", str(service_dir))
    monkeypatch.setattr(GUIXCLI, "SYSTEM_CONFIG",
                        str(tmp_path / "config.scm"))
    monkeypatch.setattr(GUIXCLI, "SYSTEM_PROFILE",
                        str(tmp_path / "run" / "current-system" / "profile"))
    monkeypatch.setattr(GUIXCLI, "STATE_PATHS", {
        "guix-system": str(tmp_path / "run" / "current-system"),
    })

    manager = GUIXCLI(logging.NullHandler(), logging.DEBUG, AgentType.VM)
    return manager, guix, service_dir


def collect_commands(manager, monkeypatch):
    commands = []

    def run_cmd(command, realtime=True):
        commands.append(command)
        return ProcessResult()

    monkeypatch.setattr(manager, "run_cmd", run_cmd)
    return commands


def collect_commands_and_realtime(manager, monkeypatch):
    calls = []

    def run_cmd(command, realtime=True):
        calls.append((command, realtime))
        return ProcessResult()

    monkeypatch.setattr(manager, "run_cmd", run_cmd)
    return calls


def test_os_release_guix_selects_guix_family(monkeypatch):
    monkeypatch.setattr(
        utils,
        "_load_os_release",
        lambda *args, logger=None: {"ID": "guix", "NAME": "Guix System"},
    )

    assert utils.get_os_data()["os_family"] == "Guix"


def test_entrypoint_selects_guix_backend(tmp_path, monkeypatch):
    manager, _guix, _service_dir = make_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(GUIXCLI, "GUIX_CANDIDATES",
                        (manager.package_manager,))

    selected = entrypoint.get_package_manager(
        {"id": "guix", "name": "Guix System", "os_family": "Guix"},
        logging.getLogger("test"),
        logging.NullHandler(),
        logging.DEBUG,
        AgentType.VM,
        no_progress=False,
    )

    assert isinstance(selected, GUIXCLI)
    assert selected.PROGRESS_REPORTING is False


def test_entrypoint_archlinux_still_reports_no_progress(capsys, monkeypatch):
    monkeypatch.setattr(entrypoint.plugins, "entrypoints", [])

    selected = entrypoint.get_package_manager(
        {"id": "arch", "name": "Arch Linux", "os_family": "ArchLinux"},
        logging.getLogger("test"),
        logging.NullHandler(),
        logging.DEBUG,
        AgentType.VM,
        no_progress=False,
    )

    assert selected.PROGRESS_REPORTING is False
    assert "Progress reporting not supported." in capsys.readouterr().out


def test_entrypoint_unknown_family_error_mentions_guix(monkeypatch):
    monkeypatch.setattr(entrypoint.plugins, "entrypoints", [])

    with pytest.raises(NotImplementedError) as exc_info:
        entrypoint.get_package_manager(
            {"id": "custom", "name": "Custom", "os_family": "Unknown"},
            logging.getLogger("test"),
            logging.NullHandler(),
            logging.DEBUG,
            AgentType.VM,
            no_progress=False,
        )

    assert "Guix" in str(exc_info.value)


def test_find_guix_uses_path_fallback(tmp_path, monkeypatch):
    manager, _guix, _service_dir = make_manager(tmp_path, monkeypatch)
    fallback = make_executable(tmp_path / "path" / "guix")
    monkeypatch.setattr(shutil, "which", lambda name: fallback)

    assert manager._find_guix(()) == fallback


def test_find_guix_fails_without_candidates_or_path(tmp_path, monkeypatch):
    manager, _guix, _service_dir = make_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError) as exc_info:
        manager._find_guix(())

    assert "Package manager not found" in str(exc_info.value)


def test_refresh_runs_time_machine_describe_with_proxy(
        tmp_path, monkeypatch, capsys):
    manager, guix, service_dir = make_manager(tmp_path, monkeypatch)
    service_dir.mkdir()
    (service_dir / "updates-proxy-setup").write_text("", encoding="ascii")
    commands = collect_commands(manager, monkeypatch)

    assert not manager.refresh(hard_fail=True)

    assert commands == [[
        "env",
        *GUIXCLI.TIME_MACHINE_ENVIRONMENT,
        "http_proxy=http://127.0.0.1:8082/",
        "https_proxy=http://127.0.0.1:8082/",
        "HTTP_PROXY=http://127.0.0.1:8082/",
        "HTTPS_PROXY=http://127.0.0.1:8082/",
        "all_proxy=http://127.0.0.1:8082/",
        "ALL_PROXY=http://127.0.0.1:8082/",
        "no_proxy=127.0.0.1,localhost",
        "NO_PROXY=127.0.0.1,localhost",
        guix,
        "time-machine",
        f"--branch={GUIXCLI.TIME_MACHINE_BRANCH}",
        "--",
        "describe",
    ]]
    assert "Refreshing Guix channel metadata from master." in (
        capsys.readouterr().out
    )


def test_refresh_streams_time_machine_output(tmp_path, monkeypatch):
    manager, guix, _service_dir = make_manager(tmp_path, monkeypatch)
    calls = collect_commands_and_realtime(manager, monkeypatch)

    assert not manager.refresh(hard_fail=True)

    assert calls == [([
        "env",
        *GUIXCLI.TIME_MACHINE_ENVIRONMENT,
        guix,
        "time-machine",
        f"--branch={GUIXCLI.TIME_MACHINE_BRANCH}",
        "--",
        "describe",
    ], True)]


def test_refresh_reports_silent_guix_failure(tmp_path, monkeypatch):
    manager, _guix, _service_dir = make_manager(tmp_path, monkeypatch)

    def run_cmd(command, realtime=True):
        result = ProcessResult(EXIT.ERR)
        result.posted = True
        return result

    monkeypatch.setattr(manager, "run_cmd", run_cmd)

    result = manager.refresh(hard_fail=True)

    assert result.code == EXIT.ERR
    assert "Guix command failed with exit code 1:" in result.err
    assert "time-machine --branch=master -- describe" in result.err
    assert result.posted is False


def test_update_proxy_vm_does_not_proxy_its_own_guix(tmp_path, monkeypatch):
    manager, guix, service_dir = make_manager(tmp_path, monkeypatch)
    service_dir.mkdir()
    (service_dir / "updates-proxy-setup").write_text("", encoding="ascii")
    (service_dir / "qubes-updates-proxy").write_text("", encoding="ascii")
    commands = collect_commands(manager, monkeypatch)

    assert not manager.refresh(hard_fail=True)

    assert commands == [[
        "env",
        *GUIXCLI.TIME_MACHINE_ENVIRONMENT,
        guix,
        "time-machine",
        f"--branch={GUIXCLI.TIME_MACHINE_BRANCH}",
        "--",
        "describe",
    ]]


def test_upgrade_reconfigures_existing_system_config(
        tmp_path, monkeypatch, capsys):
    manager, guix, _service_dir = make_manager(tmp_path, monkeypatch)
    Path(GUIXCLI.SYSTEM_CONFIG).write_text("(operating-system)\n",
                                           encoding="ascii")
    commands = collect_commands(manager, monkeypatch)

    assert not manager.upgrade_internal(remove_obsolete=True)

    assert commands == [[
        "env",
        *GUIXCLI.TIME_MACHINE_ENVIRONMENT,
        guix,
        "time-machine",
        f"--branch={GUIXCLI.TIME_MACHINE_BRANCH}",
        "--",
        "system",
        "reconfigure",
        "--no-bootloader",
        GUIXCLI.SYSTEM_CONFIG,
    ]]
    out = capsys.readouterr().out
    assert "Reconfiguring Guix System" in out
    assert "Reconfigured Guix System." in out


def test_upgrade_streams_reconfigure_output(tmp_path, monkeypatch):
    manager, guix, _service_dir = make_manager(tmp_path, monkeypatch)
    Path(GUIXCLI.SYSTEM_CONFIG).write_text("(operating-system)\n",
                                           encoding="ascii")
    calls = collect_commands_and_realtime(manager, monkeypatch)

    assert not manager.upgrade_internal(remove_obsolete=True)

    assert calls == [([
        "env",
        *GUIXCLI.TIME_MACHINE_ENVIRONMENT,
        guix,
        "time-machine",
        f"--branch={GUIXCLI.TIME_MACHINE_BRANCH}",
        "--",
        "system",
        "reconfigure",
        "--no-bootloader",
        GUIXCLI.SYSTEM_CONFIG,
    ], True)]


def test_upgrade_reports_silent_guix_failure(tmp_path, monkeypatch):
    manager, _guix, _service_dir = make_manager(tmp_path, monkeypatch)
    Path(GUIXCLI.SYSTEM_CONFIG).write_text("(operating-system)\n",
                                           encoding="ascii")

    def run_cmd(command, realtime=True):
        return ProcessResult(EXIT.ERR)

    monkeypatch.setattr(manager, "run_cmd", run_cmd)

    result = manager.upgrade_internal(remove_obsolete=False)

    assert result.code == EXIT.ERR
    assert "Guix command failed with exit code 1:" in result.err
    assert "system reconfigure --no-bootloader" in result.err


def test_upgrade_uses_qubes_update_proxy(tmp_path, monkeypatch):
    manager, guix, service_dir = make_manager(tmp_path, monkeypatch)
    Path(GUIXCLI.SYSTEM_CONFIG).write_text("(operating-system)\n",
                                           encoding="ascii")
    service_dir.mkdir()
    (service_dir / "updates-proxy-setup").write_text("", encoding="ascii")
    commands = collect_commands(manager, monkeypatch)

    assert not manager.upgrade_internal(remove_obsolete=False)

    assert commands == [[
        "env",
        *GUIXCLI.TIME_MACHINE_ENVIRONMENT,
        "http_proxy=http://127.0.0.1:8082/",
        "https_proxy=http://127.0.0.1:8082/",
        "HTTP_PROXY=http://127.0.0.1:8082/",
        "HTTPS_PROXY=http://127.0.0.1:8082/",
        "all_proxy=http://127.0.0.1:8082/",
        "ALL_PROXY=http://127.0.0.1:8082/",
        "no_proxy=127.0.0.1,localhost",
        "NO_PROXY=127.0.0.1,localhost",
        guix,
        "time-machine",
        f"--branch={GUIXCLI.TIME_MACHINE_BRANCH}",
        "--",
        "system",
        "reconfigure",
        "--no-bootloader",
        GUIXCLI.SYSTEM_CONFIG,
    ]]


def test_upgrade_fails_without_system_config(tmp_path, monkeypatch):
    manager, _guix, _service_dir = make_manager(tmp_path, monkeypatch)

    result = manager.upgrade_internal(remove_obsolete=False)

    assert result.code == EXIT.ERR_VM_UPDATE
    assert "missing Guix system configuration" in result.err


def test_upgrade_logs_reconfiguration_failure(
        tmp_path, monkeypatch, capsys):
    manager, _guix, _service_dir = make_manager(tmp_path, monkeypatch)
    Path(GUIXCLI.SYSTEM_CONFIG).write_text("(operating-system)\n",
                                           encoding="ascii")

    def run_cmd(command, realtime=True):
        return ProcessResult(EXIT.ERR_VM_UPDATE, err="failed")

    monkeypatch.setattr(manager, "run_cmd", run_cmd)

    result = manager.upgrade_internal(remove_obsolete=False)

    assert result.code == EXIT.ERR_VM_UPDATE
    captured = capsys.readouterr()
    assert "Reconfiguring Guix System" in captured.out
    assert "Guix System reconfiguration failed." in captured.err


def test_get_action_reports_reconfigure_command(tmp_path, monkeypatch):
    manager, _guix, _service_dir = make_manager(tmp_path, monkeypatch)

    assert manager.get_action(remove_obsolete=True) == [
        "time-machine",
        f"--branch={GUIXCLI.TIME_MACHINE_BRANCH}",
        "--",
        "system",
        "reconfigure",
        "--no-bootloader",
        GUIXCLI.SYSTEM_CONFIG,
    ]


def test_get_packages_reports_system_profile_metadata(tmp_path, monkeypatch):
    manager, _guix, _service_dir = make_manager(tmp_path, monkeypatch)
    system_target = tmp_path / "store" / "system"
    system_target.mkdir(parents=True)
    Path(GUIXCLI.STATE_PATHS["guix-system"]).parent.mkdir(parents=True)
    Path(GUIXCLI.STATE_PATHS["guix-system"]).symlink_to(system_target)
    manifest = "\n".join([
        "bash               5.2.15         out/gnu/store/hash-bash-5.2.15",
        "guix               1.5.0-1.deedd48out/gnu/store/hash-guix-1.5.0-1.deedd48",
        "glibc 2.39 debug /gnu/store/hash-glibc-debug-2.39",
        "glibc 2.39 out /gnu/store/hash-glibc-2.39",
        "qubes-vm-gui-common4.3.1          out/gnu/store/hash-qubes-vm-gui-common-4.3.1",
    ]) + "\n"
    monkeypatch.setattr(
        manager,
        "_list_installed_packages",
        lambda: ProcessResult(out=manifest),
    )

    assert manager.get_packages() == {
        "guix-system": [str(system_target)],
        "bash:out": ["5.2.15 /gnu/store/hash-bash-5.2.15"],
        "guix:out": [
            "1.5.0-1.deedd48 /gnu/store/hash-guix-1.5.0-1.deedd48"
        ],
        "glibc:debug": ["2.39 /gnu/store/hash-glibc-debug-2.39"],
        "glibc:out": ["2.39 /gnu/store/hash-glibc-2.39"],
        "qubes-vm-gui-common:out": [
            "4.3.1 /gnu/store/hash-qubes-vm-gui-common-4.3.1"
        ],
    }


def test_get_packages_reports_tab_separated_manifest_metadata(
        tmp_path, monkeypatch):
    manager, _guix, _service_dir = make_manager(tmp_path, monkeypatch)
    manifest = "\n".join([
        "bash|5.2.15|out|/gnu/store/hash-bash-5.2.15",
        "glibc|2.39|debug|/gnu/store/hash-glibc-debug-2.39",
        "qubes-vm-gui-common|4.3.1|out|"
        "/gnu/store/hash-qubes-vm-gui-common-4.3.1",
    ]) + "\n"
    monkeypatch.setattr(
        manager,
        "_list_installed_packages",
        lambda: ProcessResult(out=manifest),
    )

    assert manager.get_packages() == {
        "bash:out": ["5.2.15 /gnu/store/hash-bash-5.2.15"],
        "glibc:debug": ["2.39 /gnu/store/hash-glibc-debug-2.39"],
        "qubes-vm-gui-common:out": [
            "4.3.1 /gnu/store/hash-qubes-vm-gui-common-4.3.1"
        ],
    }


def test_get_packages_ignores_empty_and_malformed_manifest_lines(
        tmp_path, monkeypatch):
    manager, _guix, _service_dir = make_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(
        manager,
        "_list_installed_packages",
        lambda: ProcessResult(out="\nnot-enough-fields\n"),
    )

    assert manager.get_packages() == {}


def test_manifest_parser_accepts_generic_four_column_output():
    assert GUIXCLI._parse_manifest_entry(
        "hello 2.12 out store-path"
    ) == ("hello", "2.12", "out", "store-path")


def test_manifest_parser_rejects_unrecoverable_sanitized_output():
    assert GUIXCLI._parse_manifest_entry(
        "hello 2.12out /gnu/store/hashonly"
    ) is None
    assert GUIXCLI._parse_sanitized_manifest_entry(
        ["hello"], "/gnu/store/hash-hello-2.12"
    ) is None


def test_list_installed_packages_preserves_manifest_columns(
        tmp_path, monkeypatch):
    guix = tmp_path / "guix"
    guix.write_text(
        "#!/bin/sh\n"
        "printf 'bash\\t5.2.15\\tout\\t/gnu/store/hash-bash-5.2.15\\n'\n",
        encoding="ascii",
    )
    guix.chmod(0o755)
    monkeypatch.setattr(GUIXCLI, "GUIX_CANDIDATES", (str(guix),))

    manager = GUIXCLI(logging.NullHandler(), logging.DEBUG, AgentType.VM)
    result = manager._list_installed_packages()

    assert result.out == "bash|5.2.15|out|/gnu/store/hash-bash-5.2.15\n"


def test_get_packages_falls_back_to_system_generation_on_manifest_error(
        tmp_path, monkeypatch):
    manager, _guix, _service_dir = make_manager(tmp_path, monkeypatch)
    system_target = tmp_path / "store" / "system"
    system_target.mkdir(parents=True)
    Path(GUIXCLI.STATE_PATHS["guix-system"]).parent.mkdir(parents=True)
    Path(GUIXCLI.STATE_PATHS["guix-system"]).symlink_to(system_target)
    monkeypatch.setattr(
        manager,
        "_list_installed_packages",
        lambda: ProcessResult(EXIT.ERR, err="profile missing"),
    )

    assert manager.get_packages() == {
        "guix-system": [str(system_target)],
    }


def test_upgrade_prints_per_package_change_summary(
        tmp_path, monkeypatch, capsys):
    manager, guix, _service_dir = make_manager(tmp_path, monkeypatch)
    Path(GUIXCLI.SYSTEM_CONFIG).write_text("(operating-system)\n",
                                           encoding="ascii")
    package_states = iter([
        {
            "guix-system": ["/gnu/store/old-system"],
            "bash:out": ["5.2.15 /gnu/store/old-bash"],
        },
        {
            "guix-system": ["/gnu/store/new-system"],
            "bash:out": ["5.2.21 /gnu/store/new-bash"],
            "hello:out": ["2.12 /gnu/store/hello"],
        },
    ])

    monkeypatch.setattr(manager, "get_packages", lambda: next(package_states))
    commands = collect_commands(manager, monkeypatch)

    code = manager.upgrade(
        refresh=False,
        hard_fail=True,
        remove_obsolete=True,
        print_streams=True,
    )

    assert code == EXIT.OK
    assert commands == [[
        "env",
        *GUIXCLI.TIME_MACHINE_ENVIRONMENT,
        guix,
        "time-machine",
        f"--branch={GUIXCLI.TIME_MACHINE_BRANCH}",
        "--",
        "system",
        "reconfigure",
        "--no-bootloader",
        GUIXCLI.SYSTEM_CONFIG,
    ]]
    out = capsys.readouterr().out
    assert "Installed packages:" in out
    assert "hello:out ['2.12 /gnu/store/hello']" in out
    assert "Updated packages:" in out
    assert (
        "bash:out 5.2.15 /gnu/store/old-bash -> "
        "5.2.21 /gnu/store/new-bash"
    ) in out
    assert "guix-system /gnu/store/old-system -> /gnu/store/new-system" in out


def test_clean_keeps_guix_generations(tmp_path, monkeypatch):
    manager, _guix, _service_dir = make_manager(tmp_path, monkeypatch)

    assert manager.clean() == EXIT.OK


def test_requirements_are_not_installed_into_root_profile(
        tmp_path, monkeypatch):
    manager, _guix, _service_dir = make_manager(tmp_path, monkeypatch)

    result = manager.install_requirements({"foo": "1"}, {})

    assert result.code == EXIT.ERR_VM_PRE
    assert "unsupported" in result.err


def test_empty_requirements_are_accepted(tmp_path, monkeypatch):
    manager, _guix, _service_dir = make_manager(tmp_path, monkeypatch)

    assert not manager.install_requirements({}, {})
