#!/bin/bash
# Install some missing modules

installkernel() {
    # ehci-hcd split off
    instmods ehci-pci ehci-platform || :
}
