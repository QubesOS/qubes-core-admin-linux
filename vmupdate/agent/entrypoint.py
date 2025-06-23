#!/usr/bin/python3
import os
import sys
import argparse

from source import plugins
from source.args import AgentArgs
from source.utils import get_os_data
from source.log_congfig import init_logs
from source.common.exit_codes import EXIT
from source.common.package_manager import AgentType


def main(args=None):
    """
    Run the appropriate package manager.
    """
    args = parse_args(args)
    log, log_handler, log_level, _log_path, _log_formatter = init_logs(
        level=args.log, truncate_file=True)
    log.debug("Run entrypoint with args: %s", str(args))
    os_data = get_os_data()

    log.debug("Selecting package manager.")
    agent_type = AgentType.VM
    if os_data["id"] == "qubes":
        agent_type = AgentType.DOM0
    if args.download_only:
        agent_type = AgentType.UPDATE_VM
    pkg_mng = get_package_manager(
        os_data, log, log_handler, log_level, agent_type, args.no_progress)

    log.debug("Running upgrades.")
    return_code = pkg_mng.upgrade(refresh=not args.no_refresh,
                                  hard_fail=not args.force_upgrade,
                                  remove_obsolete=not args.leave_obsolete,
                                  print_streams=args.show_output,
                                  )

    if not pkg_mng.PROGRESS_REPORTING and not args.no_progress:
        # even if progress reporting is unavailable we want info that update finished
        if agent_type is AgentType.UPDATE_VM:
            print(f"{55:.2f}", flush=True, file=sys.stderr)
        else:
            print(f"{100:.2f}", flush=True, file=sys.stderr)

    if agent_type is AgentType.VM:
        log.debug("Notify dom0 about upgrades.")
        os.system("/usr/lib/qubes/upgrades-status-notify")

    if not args.no_cleanup:
        return_code = max(pkg_mng.clean(), return_code)

    if return_code not in EXIT.VM_HANDLED:
        return_code = EXIT.ERR_VM_UNHANDLED
    return return_code


def parse_args(args):
    parser = argparse.ArgumentParser()
    AgentArgs.add_arguments(parser)
    args = parser.parse_args(args)
    return args


def get_package_manager(os_data, log, log_handler, log_level, agent_type, no_progress):
    """
    Returns instance of `PackageManager`.

    If appropriate python package is not installed or `no_progress` is `True`
    cli based version is returned.
    """
    requirements = {}
    # plugins MUST be applied before import anything from package managers.
    # in case of apt configuration is loaded on `import apt`.
    if agent_type is not AgentType.UPDATE_VM:
        for plugin in plugins.entrypoints:
            plugin(os_data, log, requirements=requirements)

    if os_data["os_family"] == "RedHat" or agent_type is AgentType.UPDATE_VM:
        PackageManager = import_rhel_package_manager(os_data, log, no_progress)
    elif os_data["os_family"] == "Debian":
        PackageManager = import_debian_package_manager(log, no_progress)
    elif os_data["os_family"] == "ArchLinux":
        from source.pacman.pacman_cli import PACMANCLI as PackageManager
        print(f"Progress reporting not supported.", flush=True)
    elif os_data["os_family"] == "Qubes":
        PackageManager = import_dom0_package_manager(os_data, log, no_progress)
    else:
        raise NotImplementedError(
            "Only Debian, RedHat and ArchLinux based OS is supported.")

    pkg_mng = PackageManager(log_handler, log_level, agent_type)
    pkg_mng.requirements = requirements
    return pkg_mng


def import_rhel_package_manager(os_data, log, no_progress):
    """
    Import dnf package manager.
    """
    dnf5_fedora_version = 41
    if os_data["os_family"] == "RedHat":
        try:
            version = int(os_data["release"].split(".")[0])
        except ValueError:
            version = 99  # fedora changed its version
    else:
        version = dnf5_fedora_version  # try to use whatever is available, starting from dnf5

    loaded = False
    if version >= dnf5_fedora_version:
        try:
            from source.dnf.dnf5_api import DNF5 as PackageManager
            loaded = True
        except ImportError:
            log.warning("Failed to load dnf5.")

    if not loaded:
        try:
            from source.dnf.dnf_api import DNF as PackageManager
            loaded = True
            log.debug("Using dnf python API for progress reporting.")
        except ImportError:
            print(f"Progress reporting not supported.", flush=True)

    if no_progress or not loaded:
        log.warning(
            "Failed to load dnf with progress bar. Using dnf cli.")
        from source.dnf.dnf_cli import DNFCLI as PackageManager

    return PackageManager


def import_debian_package_manager(log, no_progress):
    """
    Import apt package manager.
    """
    loaded = False
    try:
        from source.apt.apt_api import APT as PackageManager
        loaded = True
    except ImportError:
        log.warning("Failed to load apt with progress bar. Using apt cli.")
        print(f"Progress reporting not supported.", flush=True)

    if no_progress or not loaded:
        from source.apt.apt_cli import APTCLI as PackageManager

    return PackageManager


def import_dom0_package_manager(os_data, log, no_progress):
    """
    Import dnf package manager for dom0.
    """
    major, minor = os_data["release"].split(".")
    major, minor = int(major), int(minor)
    loaded = False
    if major >= 5 or (major == 4 and minor >= 3):
        try:
            from source.dnf.dnf5_api import DNF5 as PackageManager
            loaded = True
        except ImportError:
            log.warning("Failed to load dnf5.")

    if not loaded:
        try:
            from source.dnf.dnf_api import DNF as PackageManager
            loaded = True
            log.debug("Using dnf python API for progress reporting.")
        except ImportError:
            print(f"Progress reporting not supported.", flush=True)

    if no_progress or not loaded:
        log.warning(
            "Failed to load dnf with progress bar. Using dnf cli.")
        from source.dnf.dnf_cli import DNFCLI as PackageManager

    return PackageManager


if __name__ == '__main__':
    try:
        sys.exit(main())
    except RuntimeError as ex:
        print(ex)
        sys.exit(EXIT.ERR_VM_UNHANDLED)
