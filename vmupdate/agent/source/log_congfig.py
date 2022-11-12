import os
import logging
from pathlib import Path

LOGPATH = '/var/log/qubes/qubes-update'
FORMAT_LOG = '%(asctime)s [Agent] %(message)s'
LOG_FILE = 'update-agent.log'


def init_logs(
        directory=LOGPATH,
        file=LOG_FILE,
        format_=FORMAT_LOG,
        level="INFO",
        truncate_file=False
):
    Path(directory).mkdir(parents=True, exist_ok=True)
    log_path = os.path.join(directory, file)

    if truncate_file:
        with open(log_path, "w"):
            # We want temporary logs here, so we truncate log file.
            # Persistent logs are at dom0.
            pass

    log_handler = logging.FileHandler(log_path, encoding='utf-8')
    log_formatter = logging.Formatter(format_)
    log_handler.setFormatter(log_formatter)

    log = logging.getLogger('vm-update.agent.PackageManager')
    log.addHandler(log_handler)
    log.propagate = False
    try:
        # if loglevel is unknown just use `DEBUG`
        log.setLevel(level)
        log_level = level
    except (ValueError, TypeError):
        log_level = "DEBUG"
        log.setLevel(log_level)

    return log, log_handler, log_level, log_path, log_formatter
