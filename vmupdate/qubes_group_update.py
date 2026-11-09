#!/usr/bin/python3
import logging
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from typing import Optional

CONFIG_PATH = "/etc/qubes/vm-update.toml"
COMMAND = "/usr/bin/qubes-vm-update"
DEFAULT_FLAGS = ["--no-progress", "--force-update", "--max-concurrency", "1"]
SUBPROCESS_TIMEOUT = 3600
GLOBAL_OPTIONS_KEY = "global_options"


logger = logging.getLogger("qubes_group_update")


@dataclass
class Options:
    """Global options defined in the [global_options] section."""
    logging_level: str = "info"
    notify_on_failure: bool = True


@dataclass
class Group:
    """A group of VMs defined in the update config."""
    vms: list[str]
    notify_on_failure: bool | None = None

    @classmethod
    def from_config(cls, raw: object) -> Optional["Group"]:
        if not isinstance(raw, dict):
            return None
        vms = raw.get("vms", [])
        if not isinstance(vms, list):
            return None
        if not vms:
            return None
        if not all(isinstance(vm, str) for vm in vms):
            return None
        if not all(re.match(r"\A[a-zA-Z][a-zA-Z0-9_.-]*\Z", vm) for vm in vms):
            logger.warning("invalid input in qube name")
            return None
        notify = raw.get("notify_on_failure", None)
        if notify is not None and not isinstance(notify, bool):
            return None
        return cls(vms=vms, notify_on_failure=notify)


def notify_update_failure(vms: list[str]) -> None:
    """Show a desktop notification listing VMs whose update failed.
    This is a best-effort operation.
    """
    if not vms:
        return
    try:
        subprocess.run(
            [
                "notify-send",
                "-u",
                "critical",
                "Qubes VM Update Failed",
                f"Update failed for {len(vms)} qubes.",
            ],
            check=True,
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.exception(f"failed to send update failure notification: {e!s}")

def run_update(vms: list[str]) -> list[str]:
    """Run qubes-vm-update for each VM individually.
    Returns the list of VMs that failed to update.
    """
    failed: list[str] = []
    for vm in vms:
        cmd = [COMMAND] + DEFAULT_FLAGS + ["--target", vm]
        try:
            result = subprocess.run(cmd, timeout=SUBPROCESS_TIMEOUT, check=False)
        except subprocess.TimeoutExpired:
            logger.exception("qubes-vm-update timed out after %d seconds for %s", SUBPROCESS_TIMEOUT, vm)
            failed.append(vm)
            continue
        except Exception as e:  # pylint: disable=broad-except
            logger.exception("qubes-vm-update failed for %s: %s", vm, e)
            failed.append(vm)
            continue
        if result.returncode != 0:
            logger.error("qubes-vm-update returned %d for %s", result.returncode, vm)
            failed.append(vm)
    return failed

def load_config() -> tuple[Options, dict[str, Group]]:
    """Load the config file and return global options and a dict of groups defined."""
    try:
        with open(CONFIG_PATH, "rb") as f:
            config = tomllib.load(f)
    except FileNotFoundError:
        logger.info("Config %s not found; updating nothing", CONFIG_PATH)
        config = {}
    options, groups = _parse_config(config)
    return options, groups


def _parse_config(config: dict) -> tuple[Options, dict[str, Group]]:
    """Parse the config and return global options and a dict of groups defined."""
    raw_options = config.pop("global_options", {})
    if isinstance(raw_options, dict):
        options = Options(
            logging_level=raw_options.get("logging_level", "info"),
            notify_on_failure=raw_options.get("notify_on_failure", True),
        )
    else:
        options = Options()

    validated_config = {}
    for group, members in config.items():
        g = Group.from_config(members)
        if g is None:
            logger.warning("Group '%s' is invalid; skipping", group)
            continue
        logger.info("Updating group '%s': %s", group, g.vms)
        validated_config[group] = g
    return options, validated_config


LOG_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}


def main() -> int:
    options, config = load_config()
    level_name = options.logging_level.lower() if options.logging_level else "info"
    log_level = LOG_LEVEL_MAP.get(level_name, logging.INFO)
    logging.basicConfig(
        level=log_level,
    )
    exit_code = 0
    if not config:
        logger.info("No groups defined; updating nothing")
        return 0

    for name, group in config.items():
        logger.info("Updating group '%s': %s", name, group.vms)
        failed = run_update(group.vms)
        if failed:
            exit_code = 1
            logger.error("Update failed for '%s' in group '%s'", failed, name)
            notify = (
                group.notify_on_failure
                if group.notify_on_failure is not None
                else options.notify_on_failure
            )
            if notify:
                notify_update_failure(failed)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
