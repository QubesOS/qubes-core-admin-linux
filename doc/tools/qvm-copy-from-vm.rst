================
qvm-copy-from-vm
================

NAME
====
qvm-copy-from-vm - copy files from a qube to dom0

SYNOPSIS
========
| qvm-copy-from-vm [-y|--yes] [--] <src-vm-name> <file> [<file> ...]

DESCRIPTION
===========
Copy files or directories from the qube <src-vm-name> into
~/QubesIncoming/<src-vm-name> in dom0. File names are interpreted in the qube,
relative to the home directory of its default user. The qube is started if
needed.

Copying data into dom0 is dangerous: a compromised qube can send files crafted
to compromise dom0 when they are opened, parsed or executed. The tool therefore
shows a warning and asks for confirmation (typing "yes") before copying.

The files are received with the same restrictions as other file copies between
qubes: the copy fails on existing files (they are never overwritten), file
names with unsafe characters, and symlinks pointing outside of the copied tree.
The transfer is limited to 4 GiB (or 90% of the free space, if less) and 4096
files; set the UPDATES_MAX_BYTES and UPDATES_MAX_FILES environment variables to
change these limits. If the copy fails, the files received until then are left
in place.

OPTIONS
=======
-h, --help
    Show usage and exit
-y, --yes
    Do not show the warning and do not ask for confirmation

