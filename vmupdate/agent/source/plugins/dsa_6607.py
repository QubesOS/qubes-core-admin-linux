import subprocess

from logging import Logger
from typing import Any

# Based on previous script for DSA-4371.
SCRIPT = b"""
set -eu -o pipefail

tmp=

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
    local pkg="$1"
    local fixed_version="$2"
    if [ -z "$fixed_version" ] || [ -z "$pkg" ]; then
        error "Bug: Invalid argument!"
    fi

    installed_version="$(dpkg -s $pkg | grep '^Version: ' | cut -d ' ' -f 2)"
    if [ -z "$installed_version" ]; then
        error "Failed to get apt version."
    fi

    rc=0
    dpkg --compare-versions "$installed_version" ge "$fixed_version" || rc=$?

    if [ "$rc" -gt 1 ]; then
        error "Bug: Failed to compare versions!"
    fi

    return $rc
}

main() {
    trap cleanup EXIT
    tmp="$(mktemp -d --tmpdir)"

    codename="$(cat /etc/debian_version)"
    pkg="apt"
    case "$codename" in
        */sid|14.*)
            # We will treat testing as sid here. This hopefully won't break
            # anything ...
            codename="sid"
            fixed_version="3.3.6"
            ;;
        13.*)
            codename="trixie"
            fixed_version="3.0.4"
            ;;
        12.*)
            codename="bookworm"
            fixed_version="2.6.2"
            ;;
        *)
            error 'Error: Could not determine Debian release!'
    esac

    if check_apt_version "$pkg" "$fixed_version"; then
        exit_ok 'Nothing to do, apt already fixed.'
    fi

    echo "apt not fixed, updating it ..."

    : > "$tmp/sources.list"
    mkdir "$tmp/sources.list.d"

    # Make sure that any old (maybe bogus) list is removed.
    apt-get \
        -o "Acquire::http::AllowRedirect=false" \
        -o "Acquire::Retries=0" \
        -o "Dir::Etc::SourceList=$tmp/sources.list" \
        -o "Dir::Etc::SourceParts=$tmp/sources.list.d" \
        --list-cleanup \
        update

    printf 'deb https://deb.debian.org/debian %s main\n' "$codename" > "$tmp/sources.list"
    if [ "$codename" != "sid" ]; then
        printf 'deb https://deb.debian.org/debian-security %s-security main\n' "$codename" >> "$tmp/sources.list"
    fi

    # Don't fetch Translation and Contents file. We don't need them and we will
    # throw them away later anyway.
    apt-get \
        -o "Acquire::Retries=0" \
        -o "Acquire::http::AllowRedirect=false" \
        -o "Acquire::Languages=none" \
        -o "Acquire::IndexTargets::deb::Contents-deb::DefaultEnabled=false" \
        -o "Dir::Etc::SourceList=$tmp/sources.list" \
        -o "Dir::Etc::SourceParts=$tmp/sources.list.d" \
        update

    apt-get \
        -o "Acquire::Retries=0" \
        -o "Acquire::http::AllowRedirect=false" \
        -o "Dir::Etc::SourceList=$tmp/sources.list" \
        -o "Dir::Etc::SourceParts=$tmp/sources.list.d" \
        --no-remove \
        --only-upgrade \
        -y \
        install "$pkg"

    if ! check_apt_version "$pkg" "$fixed_version"; then
        error 'apt version is still not fixed!'
    fi

    # Run update again to restore normal package sources.
    apt-get update

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
