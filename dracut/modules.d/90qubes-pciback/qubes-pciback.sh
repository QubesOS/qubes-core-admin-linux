#!/bin/sh

type getarg >/dev/null 2>&1 || . /lib/dracut-lib.sh

# Find all networking devices currenly installed...
HIDE_PCI="`lspci -mm -n | grep '^[^ ]* "02'|awk '{print $1}'`"

# ... and optionally all USB controllers...
if getargbool 0 rd.qubes.hide_all_usb; then
    HIDE_PCI="$HIDE_PCI `lspci -mm -n | grep '^[^ ]* "0c03'|awk '{print $1}'`"
fi

HIDE_PCI="$HIDE_PCI `getarg rd.qubes.hide_pci | tr ',' ' '`"

modprobe xen-pciback 2>/dev/null || :

# ... and hide them so that Dom0 doesn't load drivers for them
for dev in $HIDE_PCI; do
    BDF=0000:$dev
    if [ -e /sys/bus/pci/devices/$BDF/driver ]; then
        echo -n $BDF > /sys/bus/pci/devices/$BDF/driver/unbind
    fi
    echo -n $BDF > /sys/bus/pci/drivers/pciback/new_slot
    echo -n $BDF > /sys/bus/pci/drivers/pciback/bind
done
