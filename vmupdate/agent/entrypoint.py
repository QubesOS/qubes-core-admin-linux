#!/usr/bin/python3
import os
import sys
import argparse

from source.args import AgentArgs
from source.apt.configuration import get_configured_apt
from source.dnf.configuration import get_configured_dnf
from source.utils import get_os_data
from source.log_congfig import init_logs



def main(args=None):
    """
    Run the appropriate package manager.
    """
    args = parse_args(args)
    log, log_handler, log_level, _log_path, _log_formatter = init_logs(
        level=args.log, truncate_file=True)
    log.debug("Run entrypoint with args: %s", str(args))
    os_data = get_os_data()

    pkg_mng = get_package_manager(
        os_data, log, log_handler, log_level, args.no_progress)

    return_code = pkg_mng.upgrade(refresh=not args.no_refresh,
                                  hard_fail=not args.force_upgrade,
                                  remove_obsolete=not args.leave_obsolete,
                                  print_streams=args.show_output
                                  )

    os.system("/usr/lib/qubes/upgrades-status-notify")

    return return_code


def parse_args(args):
    parser = argparse.ArgumentParser()
    AgentArgs.add_arguments(parser)
    args = parser.parse_args(args)
    return args


def get_package_manager(os_data, log, log_handler, log_level, no_progress):
    if os_data["os_family"] == "Debian":
        return get_configured_apt(
            os_data, log, log_handler, log_level, no_progress)
    elif os_data["os_family"] == "RedHat":
        return get_configured_dnf(
            os_data, log, log_handler, log_level, no_progress)
    else:
        raise NotImplementedError(
            "Only Debian and RedHat based OS is supported.")


if __name__ == '__main__':
    sys.exit(main())
