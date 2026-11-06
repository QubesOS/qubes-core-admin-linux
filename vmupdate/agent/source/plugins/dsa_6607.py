import subprocess

from logging import Logger
from typing import Any

# Based on previous script for DSA-4371.
SCRIPT = b"""
set -eu -o pipefail

error() {
    echo "Error: $*"
    exit 1
}

exit_ok() {
    echo "$*"
    exit 0
}

cleanup() {
    if [ -n "$tmp" ]; then
        rm -rf "$tmp"
    fi
}

check_apt_version() {
    local installed_version
    local fixed_version

    installed_version="$(dpkg -s apt | grep '^Version: ' | cut -d ' ' -f 2)"
    if [ -z "$installed_version" ]; then
        error "Failed to get apt version."
    fi

    case "$installed_version" in
        1.2.*)
            fixed_version="1.2.36"
            ;;
        1.4.*)
            fixed_version="1.4.12"
            ;;
        1.6.*)
            fixed_version="1.6.19"
            ;;
        1.8.2.*)
            fixed_version="1.8.2.4"
            ;;
        2.0.*)
            fixed_version="2.0.12"
            ;;
        2.2.*)
            fixed_version="2.2.5"
            ;;
        2.4.*)
            fixed_version="2.4.15"
            ;;
        2.6.*)
            fixed_version="2.6.2"
            ;;
        2.8.*)
            fixed_version="2.8.4"
            ;;
        3.0.*)
            fixed_version="3.0.4"
            ;;
        3.2.*)
            fixed_version="3.2.1"
            ;;
        3.3.3ubuntu*)
            # For 26.10 Ubuntu did backport the fix instead of updating to the
            # fixed point release, so we need to special case that here.
            fixed_version="3.3.3ubuntu0.1"
            ;;
        *)
            # For anything that doesn't match a known stable branch require the
            # first non-backported fixed version.
            fixed_version="3.3.6"
            ;;
    esac

    rc=0
    dpkg --compare-versions "$installed_version" ge "$fixed_version" || rc=$?

    if [ "$rc" -gt 1 ]; then
        error "Bug: Failed to compare versions!"
    fi

    return $rc
}

main() {
    if check_apt_version; then
        exit_ok 'Nothing to do, apt already fixed.'
    fi

    echo "apt not fixed, updating it ..."

    apt-get \
        -o "Acquire::Retries=0" \
        update

    apt-get \
        -o "Acquire::Retries=0" \
        --no-remove \
        --only-upgrade \
        -y \
        install apt

    if ! check_apt_version; then
        error 'apt version is still not fixed!'
    fi

    exit_ok "apt has been successfully updated."
}

main
"""


def dsa_6607(os_data: dict, log: Logger, **kwargs: Any) -> None:
    """
    Fix DSA-6607 [1] without relying on the broken package verification.
    According to [2], disabling retries should mitigate the bug.

    [1]: https://lists.debian.org/debian-security-announce/2026/msg00526.html
    [2]: https://lists.debian.org/deity/2026/11/msg00005.html
    """

    if os_data.get("os_family", "") != "Debian":
        log.debug("Qube not Debian based. Skipping.")
        return

    with subprocess.Popen(["bash"], stdin=subprocess.PIPE) as process:
        try:
            process.communicate(input=SCRIPT)
            process.wait()
        except:
            process.kill()
            raise

    if process.returncode != 0:
        raise IOError("DSA-6607 script failed")
