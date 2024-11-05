#!/usr/bin/python3
import os
import sys
import argparse

from source import plugins
from source.args import AgentArgs
from source.utils import get_os_data
from source.log_congfig import init_logs
from source.common.exit_codes import EXIT


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
    pkg_mng = get_package_manager(
        os_data, log, log_handler, log_level, args.no_progress)

    log.debug("Running upgrades.")
    return_code = pkg_mng.upgrade(refresh=not args.no_refresh,
                                  hard_fail=not args.force_upgrade,
                                  remove_obsolete=not args.leave_obsolete,
                                  print_streams=args.show_output
                                  )

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


def get_package_manager(os_data, log, log_handler, log_level, no_progress):
    """
    Returns instance of `PackageManager`.

    If appropriate python package is not installed or `no_progress` is `True`
    cli based version is returned.
    """
    if os_data["os_family"] == "Debian":
        try:
            from source.apt.apt_api import APT as PackageManager
        except ImportError:
            log.warning("Failed to load apt with progress bar. Use apt cli.")
            # no progress reporting
            no_progress = True

        if no_progress:
            from source.apt.apt_cli import APTCLI as PackageManager
    elif os_data["os_family"] == "RedHat":
        try:
            from source.dnf.dnf_api import DNF as PackageManager
        except ImportError:
            log.warning("Failed to load dnf with progress bar. Use dnf cli.")
            # no progress reporting
            no_progress = True

        if no_progress:
            from source.dnf.dnf_cli import DNFCLI as PackageManager
    elif os_data["os_family"] == "ArchLinux":
        from source.pacman.pacman_cli import PACMANCLI as PackageManager
    else:
        raise NotImplementedError(
            "Only Debian, RedHat and ArchLinux based OS is supported.")

    requirements = {}
    for plugin in plugins.entrypoints:
        plugin(os_data, log, requirements=requirements)

    pkg_mng = PackageManager(log_handler, log_level)
    pkg_mng.requirements = requirements
    return pkg_mng


if __name__ == '__main__':
    try:
        sys.exit(main())
    except RuntimeError as ex:
        print(ex)
        sys.exit(EXIT.ERR_VM_UNHANDLED)
