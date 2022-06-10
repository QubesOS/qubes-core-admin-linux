#!/bin/bash
# Install some missing modules

installkernel() {
    # ehci-hcd split off
    hostonly='' instmods ehci-pci ehci-platform || :
    # xhci-hcd split off
    hostonly='' instmods xhci-pci xhci-plat-hcd || :
    # ohci-hcd split off
    hostonly='' instmods ohci-pci || :
    # workaround for https://github.com/dracutdevs/dracut/issues/712
    if [[ $hostonly ]]; then
        if [ "$(cat /sys/bus/pci/devices/0000:00:02.0/vendor 2>/dev/null || :)" = "0x1013" ]; then
            hostonly='' instmods cirrus
        fi
        if [ "$(cat /sys/bus/pci/devices/0000:00:02.0/vendor 2>/dev/null || :)" = "0x1234" ]; then
            hostonly='' instmods bochs_drm bochs
        fi
    else
        instmods cirrus bochs_drm bochs
    fi
}
