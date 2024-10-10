===============
qubes-vm-update
===============

NAME
====
qubes-vm-update - update software in virtual machines (qubes)

SYNOPSIS
========
| qubes-vm-update [options]

OPTIONS
=======

Package Manager
---------------
--no-refresh
    Do not refresh available packages before upgrading vm
--force-upgrade, -f
    Try upgrade even if errors are encountered (like a refresh error)
--leave-obsolete
    Do not remove obsolete packages during upgrading

Targeting
---------
--skip SKIP
    Comma separated list of VMs to be skipped, works with all other options.
--targets TARGETS
    Comma separated list of VMs to target. Ignores conditions.
--templates, -T
    Target all updatable TemplateVMs.
--standalones, -S
    Target all updatable StandaloneVMs.
--apps, -A
    Target running updatable AppVMs to update in place. Updates will be lost after vm restart.
--all
    DEFAULT. Target all updatable VMs except AdminVM. Use explicitly with "--targets" to include both.

Selecting
---------
--update-if-available
    Update targeted VMs with known updates available
--update-if-stale UPDATE_IF_STALE
    DEFAULT. Attempt to update targeted VMs with known updates available or for which last update check was more than N days ago. (default: dom0 feature `qubes-vm-update-update-if-stale` if set or 7)
--force-update
    Attempt to update all targeted VMs even if no updates are available

Propagation
-----------
--apply-to-sys, --restart, -r
    Restart not updated ServiceVMs whose template has been updated.
--apply-to-all, -R
    Restart not updated ServiceVMs and shutdown not updated AppVMs whose template has been updated.
--no-apply
    DEFAULT. Do not restart/shutdown any AppVMs.

Auxiliary
---------
--max-concurrency MAX_CONCURRENCY, -x MAX_CONCURRENCY
    Maximum number of VMs configured simultaneously (default: number of cpus)
--log LOG
    Provide logging level. Values: DEBUG, INFO (default), WARNING, ERROR, CRITICAL
--signal-no-updates
    Return exit code 100 instead of 0 if there is no updates available.

--no-progress
    Do not show upgrading progress
--dry-run
    Just print what happens
--no-cleanup
    Do not remove updater and cache files from target qube

--help, -h
    Show this help message and exit
--quiet, -q
    Do not print anything to stdout
--show-output, --verbose, -v
    Show output of management commands


How to correctly use targeting and selection?

Targeting is used to choose the VMs that will be checked for available updates, and the three-level selection is used to check if the previously chosen VMs qualify for updates (i.e., there are, for example, updates available for them).

Additionally, not all VMs in the system can be updated directly (such as AppVMs), and to update them, you must use one of the "propagation" options. This means, after updating the template, restarting the VM and applying the installed updates to it. Using at least the `--apply-to-sys` flag is recommended, which restarts all service VMs. Keep in mind that during this process, unsaved data may be lost.

VMs with `skip-update` feature set to True will be excluded from update, unless directly targeted with `--targets` option.

RETURN CODES
============

0:   ok

100: ok, returned if `--signal-no-updates` and no updates available

1:   general error

2:   usage error, unrecognized argument

11:  error of TemplateVM shutdown

12:  error of AppVM shutdown

13:  error of AppVM startup

21:  general error inside updated vm

22:  error inside updated vm during updating/installing prerequisites/patches

23:  repo-refresh error inside updated vm, check if vm is connected to network

24:  error inside updated vm during installing updates

25:  error inside updated vm during cleanup

26:  unhandled error inside updated vm

40:  qrexec error, communication across domains was interrupted

64:  usage error, wrong parameter value

130: user interruption

AUTHORS
=======
| Piotr Bartman-Szwarc <prbartman at invisiblethingslab dot com>
