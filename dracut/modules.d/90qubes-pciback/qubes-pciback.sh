#!/bin/bash --

type getarg >/dev/null 2>&1 || . /lib/dracut-lib.sh
unset re HIDE_PCI usb_in_dom0 dev skip exposed

usb_in_dom0=false

if getargbool 0 rd.qubes.hide_all_usb; then
    # Select all networking and USB devices
    re='0(2|c03)'
elif ! getargbool 1 usbcore.authorized_default; then
    # Select only networking devices, but enable USBguard
    re='02'
    usb_in_dom0=true
else
    re='02'
    warn 'USB in dom0 is not restricted. Consider rd.qubes.hide_all_usb or usbcore.authorized_default=0.'
fi

HIDE_PCI=$(set -o pipefail; { lspci -mm -n | awk "/^[^ ]* \"$re/ {print \$1}";}) ||
    die 'Cannot obtain list of PCI devices to unbind.'

manual_pcidevs=$(getarg rd.qubes.hide_pci)
case $manual_pcidevs in
(*[!0-9a-f.:,]*) warn 'Bogus rd.qubes.hide_pci option - fix your kernel command line!';;
esac
HIDE_PCI="$HIDE_PCI ${manual_pcidevs//,/ }"

# XXX should this be fatal?
ws=$' \n'
[[ $HIDE_PCI =~ ^[0-9a-f.:$ws]+$ ]] ||
    die 'Bogus PCI device list - fix your kernel command line!'
modprobe xen-pciback 2>/dev/null || :
dom0_usb=$(getarg rd.qubes.dom0_usb)
if [[ "$dom0_usb" == *[!0-9a-f.:,]* ]] ; then
    warn 'Bogus rd.qubes.dom0_usb option - fix your kernel command line!'
    dom0_usb=""
elif [ -n "$dom0_usb" ] ; then
    dom0_usb="${dom0_usb//,/ }"
    usb_in_dom0=true
fi

(
set -e
# ... and hide them so that Dom0 doesn't load drivers for them
for dev in $HIDE_PCI; do
    skip=false
    for exposed in $dom0_usb; do
        if [ "$dev" = "$exposed" ]; then skip=true; fi
    done
    if [ "$skip" = true ]; then continue; fi
    BDF=0000:$dev
    if [ -e "/sys/bus/pci/devices/$BDF/driver" ]; then
        echo -n "$BDF" > "/sys/bus/pci/devices/$BDF/driver/unbind"
    fi
    echo -n "$BDF" > /sys/bus/pci/drivers/pciback/new_slot
    echo -n "$BDF" > /sys/bus/pci/drivers/pciback/bind
done
) || die 'Cannot unbind PCI devices.'
if [ "$usb_in_dom0" = true ]; then
    info "Restricting USB in dom0 via usbguard."
    systemctl --quiet "--root=$initdir" -- enable usbguard.service
    systemctl --no-block start usbguard.service
fi
