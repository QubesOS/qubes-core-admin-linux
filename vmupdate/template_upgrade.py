#!/usr/bin/python3
"""
qvm-template-upgrade — upgrade a qube to the next distro release.

Workflow:
    1. Validate that --template names an existing TemplateVM or StandaloneVM.
    2. Compute the target as os-version + 1 (only consecutive upgrades).
    3. Clone the qube to a new name derived from the target version.
    4. Run the in-VM version-upgrade agent inside the clone.
    5. On success, update template metadata on the clone.
    6. On failure, remove the clone unless --keep-new-on-failure.

The original qube is never touched, so it stays available as the fallback.
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional, Sequence, Tuple

import qubesadmin
import qubesadmin.app
import qubesadmin.exc
import qubesadmin.tools
import qubesadmin.vm

from vmupdate.agent.source.args import AgentArgs
from vmupdate.agent.source.common.exit_codes import EXIT
from vmupdate.agent.source.status import FormatedLine
from vmupdate.update_manager import update_qube

LOG_PATH = "/var/log/qubes/qvm-template-upgrade.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"

SUPPORTED_DISTROS = {"fedora", "debian"}
SUPPORTED_CLASSES = {"TemplateVM", "StandaloneVM"}

DATE_FMT = "%Y-%m-%d %H:%M:%S"


class UpgradeError(Exception):
    """Failure during the upgrade run itself."""


class ValidationError(Exception):
    """Invalid user input or unsupported source qube."""


class _AgentOutput:
    """Minimal status sink that logs the agent's output for a single qube.

    Replaces the bulk updater's multiprocessing progress queue, which we
    don't need when upgrading exactly one qube synchronously.
    """

    def __init__(self, log: logging.Logger) -> None:
        self.log = log

    def put(self, item) -> None:
        # The transport streams agent output as FormatedLine objects; forward
        # those to the log and drop the StatusInfo progress ticks, which need
        # the bulk updater's progress bar to mean anything.
        if isinstance(item, (str, FormatedLine)):
            self.log.info("%s", item)


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
    """
    if override:
        return override
    if current_version not in source_name:
        return f"{source_name}-{target_version}"
    # Replace only the last occurrence (e.g. fedora-41-extras-41 stays sane).
    head, _, tail = source_name.rpartition(current_version)
    return f"{head}{target_version}{tail}"


# Argument parsing / logging


def get_parser() -> qubesadmin.tools.QubesArgumentParser:
    parser = qubesadmin.tools.QubesArgumentParser(
        prog="qvm-template-upgrade",
        description="Upgrade a TemplateVM or StandaloneVM to the next distro "
        "version.",
        # Disable --version: this tool has no standalone version, and the
        # default path reads qubesadmin's package metadata, which is absent
        # when qubesadmin is only on PYTHONPATH (e.g. in tests/CI).
        version="",
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
        "--log", default="INFO", help="Log level (default: INFO)."
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
    # Don't let our messages also flow through the root logger.
    log.propagate = False
    # Idempotent: if main() is called more than once in the same process
    # (embedded use, repeated CLI invocations in tests), skip re-adding
    # handlers so output isn't duplicated.
    if log.handlers:
        return log
    # Always log to stderr: so user sees progress even when the log file
    # is unavailable (dev machine without /var/log/qubes, perms issues, etc.).
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(stderr)
    try:
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        log.addHandler(handler)
    except OSError as err:
        log.warning("Could not open log file %s: %s", LOG_PATH, err)
    return log


# Orchestrator


class TemplateUpgrader:
    """Stateful orchestrator for one source qube upgrade."""

    def __init__(
        self,
        app: qubesadmin.app.QubesBase,
        args: argparse.Namespace,
        log: logging.Logger,
    ) -> None:
        self.app = app
        self.args = args
        self.log = log
        # Populated by validate(). qubesadmin ships no py.typed, so QubesVM
        # resolves to Any for the type checker; the None default is fine and
        # these are always set before any method that reads them runs.
        self.source_vm: qubesadmin.vm.QubesVM = None
        self.distro = ""
        self.current_version = ""
        self.target_version = ""
        self.new_name = ""
        # Populated by clone(); stays None until then so rollback() can tell
        # whether there is anything to remove.
        self.cloned_qube: qubesadmin.vm.QubesVM = None

    # validation

    def validate(self) -> None:
        """Run all pre-flight checks. Populates planning attributes.

        Raises ValidationError on any input/setup problem. After this call,
        self.source_vm / distro / current_version / target_version / new_name
        are all set and the upgrade can proceed (or be reported via
        describe_plan() for --dry-run).
        """
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
        distro = self.source_vm.features.get("os-distribution")
        distro_like = self.source_vm.features.get("os-distribution-like", "")
        version = self.source_vm.features.get("os-version")
        if not distro or not version:
            raise ValidationError(
                f"{self.source_vm.name} is missing os-distribution / "
                f"os-version features. Start the qube once so the in-VM "
                f"agent can report them, then retry."
            )
        # os-distribution takes priority over os-distribution-like, whose
        # entries are already ordered by closeness (cf. ID_LIKE in os-release)
        candidates = [distro.lower(), *distro_like.lower().split()]
        supported = next(
            (c for c in candidates if c in SUPPORTED_DISTROS), None
        )
        if supported is None:
            raise ValidationError(
                f"Unsupported distro {distro!r}; supported distro families "
                f"are: {', '.join(d.capitalize() for d in sorted(SUPPORTED_DISTROS))}."
            )
        return supported, version

    def describe_plan(self) -> str:
        return (
            f"upgrade {self.source_vm.name} "
            f"({self.distro} {self.current_version}) -> "
            f"clone {self.new_name} "
            f"({self.distro} {self.target_version})"
        )

    # execution

    def clone(self) -> None:
        """Clone the source qube. Populates self.cloned_qube."""
        self.log.info("Cloning %s -> %s", self.source_vm.name, self.new_name)
        self.cloned_qube = self.app.clone_vm(self.source_vm, self.new_name)

    def run_agent(self) -> None:
        """Run the in-VM version-upgrade agent inside the clone.

        Reuses the vmupdate qrexec transport (``update_qube``) with a minimal
        status sink instead of the bulk updater's progress bar. A non-zero
        agent result becomes an UpgradeError so main() rolls the clone back.
        """
        agent_args = self._build_agent_args()
        status_notifier = _AgentOutput(self.log)
        termination = SimpleNamespace(value=False)

        self.log.info(
            "Running version-upgrade agent in %s (-> %s)",
            self.cloned_qube.name,
            self.target_version,
        )
        _name, result = update_qube(
            self.cloned_qube,
            agent_args,
            show_progress=True,
            status_notifier=status_notifier,
            termination=termination,
            dom0=False,
        )
        if result.code != EXIT.OK:
            raise UpgradeError(
                f"in-VM version-upgrade agent failed for "
                f"{self.cloned_qube.name} (exit code {result.code}); "
                f"see /var/log/qubes/update-{self.cloned_qube.name}.log"
            )

    def _build_agent_args(self) -> argparse.Namespace:
        """Build the entrypoint args for a version-upgrade agent run.

        Reuse the agent's parser for proper defaults and set only the target
        version; display_name is unused with our private sink.
        """
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
        """Write post-upgrade qvm-features on the clone.

        Only TemplateVMs are touched: template-name marks the clone as
        managed by qvm-template and template-installtime is refreshed.
        StandaloneVMs are outside qvm-template's management model, so their
        template-* features are left as inherited.
        """
        if self.cloned_qube.klass != "TemplateVM":
            return
        self.log.info("Updating metadata on %s", self.cloned_qube.name)
        self.cloned_qube.features["template-name"] = self.cloned_qube.name
        self.cloned_qube.features["template-installtime"] = datetime.now(
            tz=timezone.utc
        ).strftime(DATE_FMT)

    def rollback(self) -> None:
        """Remove the half-upgraded clone, if any. Safe to call repeatedly."""
        if self.cloned_qube is None:
            return
        self.log.warning("Removing failed clone %s", self.cloned_qube.name)
        try:
            # The clone is discarded after a failed upgrade, so kill it
            # immediately instead of waiting for a graceful shutdown. kill()
            # already no-ops with QubesVMNotStartedError when the clone has
            # halted, so swallow that and delete it regardless.
            try:
                self.cloned_qube.kill()
            except qubesadmin.exc.QubesVMNotStartedError:
                pass
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

    if args.dry_run:
        print(
            f"[dry-run] would clone {upgrader.source_vm.name} -> "
            f"{upgrader.new_name} and upgrade {upgrader.distro} "
            f"{upgrader.current_version} -> {upgrader.target_version}"
        )
        return EXIT.OK

    try:
        upgrader.clone()
    except qubesadmin.exc.QubesException as err:
        print(f"error: clone failed: {err}", file=sys.stderr)
        return EXIT.ERR

    try:
        upgrader.run_agent()
    except (
        UpgradeError,
        NotImplementedError,
        qubesadmin.exc.QubesException,
    ) as err:
        log.error("Upgrade failed: %s", err)
        if not args.keep_new_on_failure:
            upgrader.rollback()
        else:
            log.info(
                "Leaving clone %s in place (--keep-new-on-failure).",
                upgrader.cloned_qube.name,
            )
        print(f"error: {err}", file=sys.stderr)
        return EXIT.ERR

    # the OS upgrade succeeded: a metadata-write hiccup must not roll the
    # upgraded clone back, only tell the user what to stamp manually
    try:
        upgrader.finalize()
    except qubesadmin.exc.QubesException as err:
        log.warning("Could not write post-upgrade features: %s", err)
        print(
            f"warning: {upgrader.cloned_qube.name} was upgraded, but "
            f"writing its template-* features failed: {err}. Set them "
            f"manually with qvm-features.",
            file=sys.stderr,
        )

    label = upgrader.cloned_qube.klass.lower().removesuffix("vm")
    print(f"Upgrade complete. New {label}: {upgrader.cloned_qube.name}")
    print(f"Original qube {upgrader.source_vm.name} is untouched.")
    return EXIT.OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
