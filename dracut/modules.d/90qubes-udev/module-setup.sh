#!/bin/bash --
check () { :; }
install () {
    inst /usr/lib/udev/rules.d/00-qubes-ignore-devices.rules
    inst /usr/lib/udev/rules.d/12-qubes-ignore-lvm-devices.rules
    inst /usr/lib/udev/rules.d/99z-qubes-mark-ready.rules
}
