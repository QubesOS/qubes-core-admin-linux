#!/usr/bin/python3
"""Upgrade a clone to the next distro release."""

import argparse
import contextlib
import logging
import re
import sys
import threading
import time
from datetime import datetime, timezone
from importlib import metadata
from types import SimpleNamespace
from typing import Iterator, Optional, Sequence, Tuple

import qubesadmin
import qubesadmin.app
import qubesadmin.exc
import qubesadmin.tools
import qubesadmin.vm

from tqdm import tqdm

from vmupdate.agent.source.args import AgentArgs
from vmupdate.agent.source.common.exit_codes import EXIT
from vmupdate.agent.source.status import FormatedLine, StatusInfo
from vmupdate.update_manager import update_qube

LOG_PATH = "/var/log/qubes/qvm-template-upgrade.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
# Separate logger so agent output reaches the log file but not stderr;
# _AgentOutput streams it to the terminal itself.
AGENT_LOGGER = "vm-template-upgrade.agent"

SUPPORTED_DISTROS = {"fedora", "debian"}
SUPPORTED_CLASSES = {"TemplateVM", "StandaloneVM"}

DATE_FMT = "%Y-%m-%d %H:%M:%S"
# Mark templates created by this tool.
REPONAME = "@qvm-template-upgrade"

# qui-disk-space's per-qube "running out of storage space" warning.
DISK_SPACE_NOTIFY_FEATURE = "disk-space-not-notify"


class UpgradeError(Exception):
    """Failure during the upgrade run itself."""


class ValidationError(Exception):
    """Invalid user input or unsupported source qube."""


def make_progress_bar(description: str) -> Optional[tqdm]:
    """Build a terminal progress bar when stderr is interactive."""
    if not sys.stderr.isatty():
        return None
    return tqdm(
        total=100.0,
        desc=description,
        file=sys.stderr,
        # Resize with the terminal during long upgrades.
        dynamic_ncols=True,
        bar_format="{desc} {percentage:5.1f}% |{bar}| [{elapsed}]{postfix}",
    )


def format_quiet_time(seconds: float) -> str:
    """Format elapsed quiet time for the progress bar."""
    minutes, secs = divmod(int(seconds), 60)
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


class _AgentOutput:
    """Stream agent output above one progress bar."""

    #: Heartbeat refresh interval in seconds.
    HEARTBEAT_INTERVAL = 1.0
    #: Inactivity threshold before showing wait duration.
    QUIET_AFTER = 30.0

    def __init__(
        self,
        log: logging.Logger,
        progress_bar: Optional[tqdm] = None,
    ) -> None:
        self.log = log
        self.progress_bar = progress_bar
        # The heartbeat thread and put() both draw on the bar.
        self._bar_lock = threading.Lock()
        self._last_advance = time.monotonic()
        self._stop_heartbeat = threading.Event()
        self._heartbeat: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the progress heartbeat when a bar is enabled."""
        if self.progress_bar is None or self._heartbeat is not None:
            return
        self._heartbeat = threading.Thread(
            target=self._beat, name="upgrade-progress-heartbeat", daemon=True
        )
        self._heartbeat.start()

    def _beat(self) -> None:
        while not self._stop_heartbeat.wait(self.HEARTBEAT_INTERVAL):
            with self._bar_lock:
                if self.progress_bar is None:
                    return
                try:
                    self.progress_bar.set_postfix_str(
                        self._quiet_note(), refresh=False
                    )
                    self.progress_bar.refresh()
                except Exception:  # pylint: disable=broad-except
                    # Progress rendering must not interrupt the upgrade.
                    self.log.debug("progress heartbeat stopped", exc_info=True)
                    return

    def _quiet_note(self) -> str:
        """Return the waiting time after progress stalls."""
        quiet = time.monotonic() - self._last_advance
        if quiet < self.QUIET_AFTER:
            return ""
        return f"waiting {format_quiet_time(quiet)}"

    def put(self, item) -> None:
        # The transport streams agent output as FormatedLine objects.
        if isinstance(item, (str, FormatedLine)):
            self.log.info("%s", item)
            with self._bar_lock:
                if self.progress_bar is not None:
                    # tqdm reprints the bar below the written line.
                    self.progress_bar.write(str(item))
                else:
                    print(item, flush=True)
        elif isinstance(item, StatusInfo):
            # Keep progress history in the debug log.
            self.log.debug("%s progress: %s", item.qname, item.info)
            # done/pending carry a FinalStatus or None, not a percent
            if self.progress_bar is not None and isinstance(
                item.info, (int, float)
            ):
                with self._bar_lock:
                    if self.progress_bar is None:
                        return
                    advance = float(item.info) - self.progress_bar.n
                    if advance > 0:
                        # Clear the stale waiting note.
                        self._last_advance = time.monotonic()
                        self.progress_bar.set_postfix_str("", refresh=False)
                        self.progress_bar.update(advance)

    def close(self) -> None:
        """Stop the heartbeat and finish the progress line."""
        self._stop_heartbeat.set()
        heartbeat, self._heartbeat = self._heartbeat, None
        if heartbeat is not None:
            heartbeat.join(timeout=self.HEARTBEAT_INTERVAL * 2)
        if not self._bar_lock.acquire(timeout=self.HEARTBEAT_INTERVAL * 2):
            self.log.debug("progress bar busy; leaving its line unfinished")
            return
        try:
            if self.progress_bar is not None:
                self.progress_bar.set_postfix_str("", refresh=False)
                self.progress_bar.close()
                self.progress_bar = None
        finally:
            self._bar_lock.release()


def compute_target_version(current: str) -> str:
    """Return current + 1 as the target distro version.

    Non-integer versions are rejected here.
    """
    try:
        current_n = int(current)
    except ValueError as exc:
        raise ValidationError(
            f"Non-numeric distro version {current!r}; multi-component "
            f"versions (e.g. Debian point releases) are not yet supported "
            f"by this tool."
        ) from exc
    return str(current_n + 1)


def derive_clone_name(
    source_name: str,
    current_version: str,
    target_version: str,
    override: Optional[str],
) -> str:
    """Replace the version in the source name with the target version.

    Examples:
        fedora-41, 41 -> 42  =>  fedora-42

        fedora-41-minimal, 41 -> 42  =>  fedora-42-minimal

        custom, 41 -> 42  =>  custom-42

        debian-121, 12 -> 13  =>  debian-121-13 (no match inside digit runs)
    """
    if override:
        return override
    # Replace only the last occurrence (e.g. fedora-41-extras-41 stays
    # sane), and only at digit boundaries (debian-121 has no "12").
    matches = list(
        re.finditer(rf"(?<!\d){re.escape(current_version)}(?!\d)", source_name)
    )
    if not matches:
        return f"{source_name}-{target_version}"
    last = matches[-1]
    return (
        source_name[: last.start()] + target_version + source_name[last.end() :]
    )


# Argument parsing / logging


def _vmupdate_version() -> str:
    """Version of the installed qubes-vmupdate package.

    The RPM ships the package metadata, but a source checkout run from
    PYTHONPATH (tests, CI, development in dom0) has none; a missing
    version must not stop the parser from being built.
    """
    try:
        return metadata.version("qubes-vmupdate")
    except metadata.PackageNotFoundError:
        return "unknown"


def get_parser() -> qubesadmin.tools.QubesArgumentParser:
    parser = qubesadmin.tools.QubesArgumentParser(
        prog="qvm-template-upgrade",
        description="Upgrade a TemplateVM or StandaloneVM to the next distro "
        "version.",
        version="%(prog)s " + _vmupdate_version(),
    )
    parser.add_argument(
        "--template",
        required=True,
        help="Name of the source TemplateVM or StandaloneVM to upgrade.",
    )
    parser.add_argument(
        "--new-name",
        help="Name for the upgraded clone. Defaults to replacing the version "
        "suffix in the source name (e.g. fedora-41 -> fedora-42).",
    )
    parser.add_argument(
        "--keep-new-on-failure",
        action="store_true",
        help="Preserve the half-upgraded clone if the upgrade fails. "
        "By default the clone is removed and the original remains.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print the planned actions; do not clone "
        "or upgrade anything.",
    )
    parser.add_argument(
        "--log",
        default="INFO",
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log level (default: INFO).",
    )
    return parser


def parse_args(
    argv: Optional[Sequence[str]] = None,
    app: Optional[qubesadmin.app.QubesBase] = None,
) -> Tuple[qubesadmin.tools.QubesArgumentParser, argparse.Namespace]:
    parser = get_parser()
    return parser, parser.parse_args(argv, app=app)


def setup_logging(level: str) -> logging.Logger:
    log = logging.getLogger("vm-template-upgrade")
    log.setLevel(level)
    # Keep agent output out of the terminal.
    agent_log = logging.getLogger(AGENT_LOGGER)
    agent_log.setLevel(level)
    # Do not propagate agent output to the main logger.
    log.propagate = False
    agent_log.propagate = False
    # Avoid duplicate handlers when main() runs again.
    if log.handlers:
        return log
    # Keep milestones visible if the log file is unavailable.
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(stderr)
    try:
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        log.addHandler(handler)
        agent_log.addHandler(handler)
    except OSError as err:
        log.warning("Could not open log file %s: %s", LOG_PATH, err)
    return log


# Upgrade manager


class TemplateUpgrader:
    """Manages the upgrade workflow for a single source qube."""

    def __init__(
        self,
        app: qubesadmin.app.QubesBase,
        args: argparse.Namespace,
        log: logging.Logger,
    ) -> None:
        self.app = app
        self.args = args
        self.log = log
        # Set by validate() before use.
        self.source_vm: Optional[qubesadmin.vm.QubesVM] = None
        self.distro = ""
        self.current_version = ""
        self.target_version = ""
        self.new_name = ""
        # Set by clone() for rollback.
        self.cloned_qube: Optional[qubesadmin.vm.QubesVM] = None

    # validation

    def validate(self) -> None:
        """Validate inputs and prepare the upgrade plan."""
        self.source_vm = self._resolve_source_qube()
        self.distro, self.current_version = self._detect_distro()
        self.target_version = compute_target_version(self.current_version)
        self.new_name = derive_clone_name(
            self.source_vm.name,
            self.current_version,
            self.target_version,
            self.args.new_name,
        )
        if self.new_name in self.app.domains:
            raise ValidationError(
                f"Target name {self.new_name!r} already exists. Remove it "
                "first or pass a different --new-name."
            )

    def _resolve_source_qube(self) -> qubesadmin.vm.QubesVM:
        try:
            vm = self.app.domains[self.args.template]
        except KeyError as exc:
            raise ValidationError(
                f"No such qube: {self.args.template}"
            ) from exc
        if vm.klass not in SUPPORTED_CLASSES:
            raise ValidationError(
                f"{vm.name} is a {vm.klass}; only TemplateVMs and "
                f"StandaloneVMs can be upgraded with this tool."
            )
        return vm

    def _detect_distro(self) -> Tuple[str, str]:
        assert self.source_vm is not None  # set by validate()
        distro = self.source_vm.features.get("os-distribution")
        distro_like = self.source_vm.features.get("os-distribution-like", "")
        version = self.source_vm.features.get("os-version")
        if not distro or not version:
            raise ValidationError(
                f"{self.source_vm.name} is missing os-distribution / "
                f"os-version features. Start the qube once so the in-VM "
                f"agent can report them, then retry."
            )
        # Only os-distribution itself counts: a derivative (matched via
        # os-distribution-like) has its own version scheme, so the parent
        # family's target = version + 1 would be meaningless for it.
        distro_lower = distro.lower()
        if distro_lower in SUPPORTED_DISTROS:
            return distro_lower, version
        family = next(
            (c for c in distro_like.lower().split() if c in SUPPORTED_DISTROS),
            None,
        )
        if family is not None:
            raise ValidationError(
                f"{self.source_vm.name} is a {distro} derivative; its own "
                f"version numbering does not match {family}'s releases, so "
                f"it cannot be upgraded with this tool."
            )
        raise ValidationError(
            f"Unsupported distro {distro!r}; supported distro families "
            f"are: {', '.join(d.capitalize() for d in sorted(SUPPORTED_DISTROS))}."
        )

    def describe_plan(self) -> str:
        assert self.source_vm is not None  # set by validate()
        return (
            f"upgrade {self.source_vm.name} "
            f"({self.distro} {self.current_version}) -> "
            f"clone {self.new_name} "
            f"({self.distro} {self.target_version})"
        )

    # execution

    def clone(self) -> None:
        """Clone the source qube. Populates self.cloned_qube."""
        assert self.source_vm is not None  # set by validate()
        self.log.info("Cloning %s -> %s", self.source_vm.name, self.new_name)
        self.cloned_qube = self.app.clone_vm(self.source_vm, self.new_name)

    @contextlib.contextmanager
    def _disk_space_warning_suppressed(self) -> Iterator[None]:
        """Temporarily suppress qui-disk-space during upgrade.

        The upgrade briefly holds both releases' packages, which can
        trip the 90% threshold. This may cause unnecessary panic for users.
        """
        assert self.cloned_qube is not None  # set by clone()
        try:
            prior = self.cloned_qube.features.get(DISK_SPACE_NOTIFY_FEATURE)
            self.cloned_qube.features[DISK_SPACE_NOTIFY_FEATURE] = "1"
        except qubesadmin.exc.QubesException as err:
            self.log.warning("could not suppress disk space warning: %s", err)
            yield
            return
        try:
            yield
        finally:
            try:
                if prior is None:
                    del self.cloned_qube.features[DISK_SPACE_NOTIFY_FEATURE]
                else:
                    self.cloned_qube.features[DISK_SPACE_NOTIFY_FEATURE] = prior
            except (KeyError, qubesadmin.exc.QubesException) as err:
                self.log.warning(
                    "could not restore disk space warning: %s", err
                )

    def run_agent(self) -> None:
        """Run the version-upgrade agent inside the clone."""
        assert self.cloned_qube is not None  # set by clone()
        agent_args = self._build_agent_args()
        # Print the milestone before creating the progress bar.
        self.log.info(
            "Running version-upgrade agent in %s (-> %s)",
            self.cloned_qube.name,
            self.target_version,
        )
        status_notifier = _AgentOutput(
            logging.getLogger(AGENT_LOGGER),
            progress_bar=make_progress_bar(
                f"{self.cloned_qube.name} "
                f"({self.distro} {self.current_version} "
                f"-> {self.target_version})"
            ),
        )
        termination = SimpleNamespace(value=False)

        with self._disk_space_warning_suppressed():
            status_notifier.start()
            try:
                _name, result = update_qube(
                    self.cloned_qube,
                    agent_args,
                    show_progress=True,
                    status_notifier=status_notifier,
                    termination=termination,
                    dom0=False,
                )
            finally:
                # Finish the bar before any later output.
                status_notifier.close()
        if result.code != EXIT.OK:
            raise UpgradeError(
                f"in-VM version-upgrade agent failed for "
                f"{self.cloned_qube.name} (exit code {result.code}); "
                f"see /var/log/qubes/update-{self.cloned_qube.name}.log"
            )

    def _build_agent_args(self) -> argparse.Namespace:
        """Build arguments for a version-upgrade agent run."""
        parser = argparse.ArgumentParser()
        AgentArgs.add_arguments(parser)
        agent_args = parser.parse_args(
            [
                "--version-upgrade",
                self.target_version,
                "--log",
                self.args.log,
            ]
        )
        agent_args.display_name = None
        return agent_args

    def finalize(self) -> None:
        """Refresh metadata for an upgraded TemplateVM."""
        assert self.cloned_qube is not None  # set by clone()
        assert self.source_vm is not None  # set by validate()
        # The in-VM agent verified the new release; make sure dom0 metadata
        # reflects it even if qubes.PostInstall's feature refresh failed.
        if self.cloned_qube.features.get("os-version") != self.target_version:
            self.cloned_qube.features["os-version"] = self.target_version
        if self.cloned_qube.klass != "TemplateVM":
            return
        self.log.info("Updating metadata on %s", self.cloned_qube.name)
        self.cloned_qube.features["template-name"] = self.cloned_qube.name
        now = datetime.now(tz=timezone.utc).strftime(DATE_FMT)
        self.cloned_qube.features["template-installtime"] = now
        # The clone filesystem was produced by this upgrade.
        self.cloned_qube.features["template-buildtime"] = now
        # Record the tool: this clone came from neither a repo nor an RPM.
        self.cloned_qube.features["template-reponame"] = REPONAME
        # Preserve inherited EVR metadata. qvm-template uses it for package
        # comparisons, so generating a value would give an invalid ordering.
        # Replace the inherited source name but keep all other user text.
        for feature in ("template-summary", "template-description"):
            value = self.cloned_qube.features.get(feature, "")
            if self.source_vm.name in value:
                self.cloned_qube.features[feature] = value.replace(
                    self.source_vm.name, self.cloned_qube.name
                )

    def rollback(self) -> None:
        """Remove the half-upgraded clone, if any. Safe to call repeatedly."""
        if self.cloned_qube is None:
            return
        self.log.warning("Removing failed clone %s", self.cloned_qube.name)
        try:
            # Discard the failed clone immediately. The clone is usually
            # already halted here: QubeConnection shuts it down as soon as
            # the agent run ends, before the failure reaches rollback().
            # kill() on a halted domain raises QubesVMNotStartedError;
            # ignoring it leaves nothing to stop.
            try:
                self.cloned_qube.kill()
            except qubesadmin.exc.QubesVMNotStartedError:
                pass
            # kill() returns before teardown finishes; deleting a domain
            # that is not yet halted fails, so wait briefly for it.
            deadline = time.monotonic() + 30
            while (
                self.cloned_qube.get_power_state() != "Halted"
                and time.monotonic() < deadline
            ):
                time.sleep(0.5)
            del self.app.domains[self.cloned_qube.name]
        except qubesadmin.exc.QubesException as err:
            self.log.error(
                "Could not remove failed clone %s: %s",
                self.cloned_qube.name,
                err,
            )


# CLI entry point


def main(
    argv: Optional[Sequence[str]] = None,
    app: Optional[qubesadmin.app.QubesBase] = None,
) -> int:
    parser, args = parse_args(argv, app)
    log = setup_logging(args.log)
    upgrader = TemplateUpgrader(args.app, args, log)

    try:
        upgrader.validate()
    except ValidationError as err:
        parser.print_error(str(err))
        return EXIT.ERR_USAGE

    log.info("Plan: %s", upgrader.describe_plan())

    source_vm = upgrader.source_vm
    assert source_vm is not None  # set by validate()

    if args.dry_run:
        print(
            f"[dry-run] would clone {source_vm.name} -> "
            f"{upgrader.new_name} and upgrade {upgrader.distro} "
            f"{upgrader.current_version} -> {upgrader.target_version}"
        )
        return EXIT.OK

    try:
        try:
            upgrader.clone()
        except qubesadmin.exc.QubesException as err:
            print(f"error: clone failed: {err}", file=sys.stderr)
            return EXIT.ERR
        cloned_qube = upgrader.cloned_qube
        assert cloned_qube is not None  # set by clone()

        try:
            upgrader.run_agent()
        except UpgradeError as err:
            log.error("Upgrade failed: %s", err)
            if not args.keep_new_on_failure:
                upgrader.rollback()
            else:
                log.info(
                    "Leaving clone %s in place (--keep-new-on-failure).",
                    cloned_qube.name,
                )
            print(f"error: {err}", file=sys.stderr)
            return EXIT.ERR

        # A metadata failure must not roll back a completed OS upgrade.
        try:
            upgrader.finalize()
        except qubesadmin.exc.QubesException as err:
            log.warning("Could not write post-upgrade features: %s", err)
            print(
                f"warning: {cloned_qube.name} was upgraded, but "
                f"writing its template-* features failed: {err}. Set them "
                f"manually with qvm-features.",
                file=sys.stderr,
            )

        label = cloned_qube.klass.lower().removesuffix("vm")
        print(f"Upgrade complete. New {label}: {cloned_qube.name}")
        print(f"Original qube {source_vm.name} is untouched.")
        return EXIT.OK
    except KeyboardInterrupt:
        log.error("Interrupted; clone %s may remain.", upgrader.new_name)
        print("error: interrupted; the clone may remain.", file=sys.stderr)
        return EXIT.SIGINT


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
