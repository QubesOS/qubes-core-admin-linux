#!/bin/bash
# Install some missing modules

installkernel() {
    # ehci-hcd split off
    hostonly='' instmods ehci-pci ehci-platform || :
    # xhci-hcd split off
    hostonly='' instmods xhci-pci xhci-plat-hcd || :
    # ohci-hcd split off
    hostonly='' instmods ohci-pci || :
}
