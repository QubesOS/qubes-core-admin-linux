#!/usr/bin/python3
import os
import sys
import argparse
import logging
from pathlib import Path

from source.args import AgentArgs
from source.apt.configuration import get_configured_apt
from source.dnf.configuration import get_configured_dnf
from source.utils import get_os_data
from source.log_congfig import LOGPATH, LOG_FILE, FORMAT_LOG

Path(LOGPATH).mkdir(parents=True, exist_ok=True)
formatter_log = logging.Formatter(FORMAT_LOG)


def parse_args(args):
    parser = argparse.ArgumentParser()
    AgentArgs.add_arguments(parser)
    args = parser.parse_args(args)
    return args


def init_logs(arg_log_level):
    log_path = os.path.join(LOGPATH, LOG_FILE)
    with open(log_path, "w"):
        # We want temporary logs here, so we truncate log file.
        # Persistent logs are at dom0.
        pass
    log_handler = logging.FileHandler(log_path, encoding='utf-8')
    log_handler.setFormatter(formatter_log)

    log = logging.getLogger('vm-update.agent.PackageManager')
    log.addHandler(log_handler)
    log.propagate = False
    try:
        # if loglevel is unknown just use `DEBUG`
        log.setLevel(arg_log_level)
        log_level = arg_log_level
    except ValueError:
        log_level = "DEBUG"
        log.setLevel(log_level)

    return log, log_handler, log_level


def main(args=None):
    """
    Run the appropriate package manager.
    """
    args = parse_args(args)
    log, log_handler, log_level = init_logs(args.log)
    log.debug("Run entrypoint with args: %s", str(args))
    os_data = get_os_data()
    requirements = {}

    if os_data["os_family"] == "Debian":
        pkg_mng = get_configured_apt(
            os_data, requirements, log_handler, log_level, args.no_progress)
    elif os_data["os_family"] == "RedHat":
        pkg_mng = get_configured_dnf(
            os_data, requirements, log_handler, log_level, args.no_progress)
    else:
        raise NotImplementedError(
            "Only Debian and RedHat based OS is supported.")

    return_code = pkg_mng.upgrade(refresh=not args.no_refresh,
                                  hard_fail=not args.force_upgrade,
                                  remove_obsolete=not args.leave_obsolete,
                                  requirements=requirements,
                                  print_streams=args.show_output
                                  )

    os.system("/usr/lib/qubes/upgrades-status-notify")

    return return_code


if __name__ == '__main__':
    sys.exit(main())
