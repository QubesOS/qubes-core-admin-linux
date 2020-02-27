#!/usr/bin/bash
# Add roadrunner2/macbook12-spi-driver drivers to initramfs for supporting keyboard, touchpad, touchbar in the MacBooks.
# Pre-requisite: these drivers need to be included in the Linux kernel package.

check() {
  grep -q ^MacBook /sys/devices/virtual/dmi/id/product_name || return 255
}

installkernel() {
  hostonly='' instmods intel_lpss intel_lpss_pci spi_pxa2xx_platform spi_pxa2xx_pci applespi apple_ib_tb
}

install() {
  echo "options apple_ib_tb fnmode=2" >> "${initdir}/etc/modprobe.d/macbook12-spi-driver.conf"
  echo "options applespi fnremap=1" >> "${initdir}/etc/modprobe.d/macbook12-spi-driver.conf"
}
