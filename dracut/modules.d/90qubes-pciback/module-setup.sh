#!/bin/bash

install() {
    inst_hook cmdline 02 "$moddir/qubes-pciback.sh"
    inst lspci
    inst grep
    inst awk
}

installkernel() {
    local mod=

    for mod in pciback xen-pciback; do
        if modinfo -k "${kernel}" "${mod}" >/dev/null 2>&1; then
            hostonly='' instmods "${mod}"
        fi
    done
}
