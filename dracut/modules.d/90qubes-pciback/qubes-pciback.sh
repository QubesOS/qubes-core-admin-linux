#!/bin/bash --

type getarg >/dev/null 2>&1 || . /lib/dracut-lib.sh
unset re HIDE_PCI usb_in_dom0 dev skip exposed PCI_POLICY_FILE PCI_POLICY_RE ignore_re devs invert

usb_in_dom0=false

# PCI_POLICY_FILE syntax:
# - one POSIX regex on `lspci -mm -n` per line (matching device = allowed)
# - empty lines & lines starting with # are ignored
# - lines starting with ! will cause a block
# - processing stops as soon as a match is found
# - WARNING: If you block devices required by dom0, Qubes may not boot anymore.
#            You'll have to chroot and re-create the initramfs.
PCI_POLICY_FILE="/etc/qubes-pci-policy.conf"

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

if getargbool 0 rd.qubes.pci_policy; then
    PCI_POLICY_RE="$(cat "$PCI_POLICY_FILE")" || die "Failed to read ${PCI_POLICY_FILE}."
    info "Manual PCI policy mode based on ${PCI_POLICY_FILE} in initramfs."
    getargbool 0 "rd.qubes.hide_all_usb" && warn "rd.qubes.hide_all_usb has no effect with rd.qubes.pci_policy."
    ignore_re='^[[:blank:]]*(#.*)?$'
    devs="$(lspci -mm -n)" || die "Cannot obtain the list of PCI devices."

    while IFS= read -r dev ; do
        skip=1
        while IFS= read -r re ; do
            invert=1
            [[ "$re" =~ $ignore_re ]] && continue
            [[ "$re" == '!'* ]] && invert=0 && re="${re:1}"
            if [[ "$dev" =~ $re ]] ; then
                [ $invert -eq 0 ] && skip=1 || skip=0
                break
            fi
        done <<< "$PCI_POLICY_RE"
        [ $skip -eq 0 ] && info "Allowed PCI device: $dev" || HIDE_PCI="$HIDE_PCI ${dev%% *}"
    done <<< "$devs"
else
    HIDE_PCI=$(set -o pipefail; { lspci -mm -n | awk "/^[^ ]* \"$re/ {print \$1}";}) ||
        die 'Cannot obtain the list of PCI devices to unbind.'
fi

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
) || {
    hypervisor_type=
    if [ -r /sys/hypervisor/type ]; then
        read -r hypervisor_type < /sys/hypervisor/type || \
            die 'Cannot determine hypervisor type'
    fi
    if [ "$hypervisor_type" = "xen" ]; then
        die 'Cannot unbind PCI devices.'
    else
        warn 'Cannot unbind PCI devices - not running under Xen'
    fi
}
if [ "$usb_in_dom0" = true ]; then
    info "Restricting USB in dom0 via usbguard."
    systemctl --quiet -- enable usbguard.service
    systemctl --no-block start usbguard.service
fi
