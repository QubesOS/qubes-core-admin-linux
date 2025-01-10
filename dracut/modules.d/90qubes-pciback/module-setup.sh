#!/bin/bash
# This is a Qubes-specific dracut module that takes care of detecting and
# hiding all networking devices (and optionally USB devices) at boot time so
# that Dom0 doesn't load drivers for them...

check() {
    return 0
}

install () {
    inst_hook cmdline 02 "$moddir/qubes-pciback.sh"
    inst lspci
    inst grep
    inst awk
    mkdir -p -m 0700 -- "$initdir/etc/usbguard"
    mkdir -p -m 0755 -- "$systemdsystemunitdir/usbguard.service.d"
    inst_multiple /etc/nsswitch.conf
    inst_multiple /etc/usbguard/{qubes-usbguard.conf,rules.d,IPCAccessControl.d}
    inst_multiple /etc/usbguard/rules.d/*
    inst -l /usr/bin/usbguard
    inst -l /usr/sbin/usbguard-daemon
    inst /usr/lib/systemd/system/usbguard.service.d/30_qubes.conf
    inst /usr/lib/systemd/system/usbguard.service
}

installkernel() {
    local mod=

    for mod in pciback xen-pciback; do
        if modinfo -k "${kernel}" "${mod}" >/dev/null 2>&1; then
            hostonly='' instmods "${mod}"
        fi
    done
}
