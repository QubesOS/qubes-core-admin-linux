/*
 * Move PCI devices to Xen's quarantine domain (DOMID_IO), like
 * `xl pci-assignable-add`, but without libxl and xenstored, so it can run
 * from the initramfs. The device should already be unbound from its dom0
 * driver (e.g. bound to pciback).
 *
 * Usage: qubes-pci-quarantine SSSS:BB:DD.F...
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <xenctrl.h>

static int parse_sbdf(const char *str, uint32_t *sbdf)
{
    unsigned int seg, bus, dev, func;
    char end;

    if (sscanf(str, "%4x:%2x:%2x.%1x%c", &seg, &bus, &dev, &func, &end) != 4 ||
            dev > 0x1f || func > 7)
        return -1;
    *sbdf = (seg << 16) | (bus << 8) | (dev << 3) | func;
    return 0;
}

int main(int argc, char **argv)
{
    xc_interface *xch;
    uint32_t sbdf;
    int i, ret = 0;

    if (argc < 2) {
        fprintf(stderr, "Usage: %s SSSS:BB:DD.F...\n", argv[0]);
        return 2;
    }

    xch = xc_interface_open(NULL, NULL, 0);
    if (!xch) {
        perror("xc_interface_open");
        return 1;
    }

    for (i = 1; i < argc; i++) {
        if (parse_sbdf(argv[i], &sbdf) < 0) {
            fprintf(stderr, "Invalid PCI address: %s\n", argv[i]);
            ret = 1;
            continue;
        }
        /*
         * Same call as libxl__device_pci_assignable_add(): DOMID_IO has no
         * mappings, so RDM_RELAXED avoids a needless refusal.
         */
        if (xc_assign_device(xch, DOMID_IO, sbdf,
                             XEN_DOMCTL_DEV_RDM_RELAXED) < 0) {
            fprintf(stderr, "Failed to quarantine %s: %s\n", argv[i],
                    strerror(errno));
            ret = 1;
        }
    }

    xc_interface_close(xch);
    return ret;
}
