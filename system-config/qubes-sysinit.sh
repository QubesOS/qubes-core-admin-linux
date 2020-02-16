#!/bin/bash

# Implementation of /var/run/qubes/"SERVICE NAME" for dom0
# Currently needed for qvm-start-daemon in dom0 (GuiVM and AudioVM)
# This part if for the boot. Then, it's managed by qubes admin API

rm -rf /var/run/qubes-service
mkdir -p /var/run/qubes-service

while IFS= read -r service
do
    svc_name="$(echo "$service" | awk '{print $1}')"
    svc_status="$(echo "$service" | awk '{print $2}')"
    if [ "$svc_status" == "on" ]; then
        touch "/var/run/qubes-service/$svc_name"
    fi
done < <(qvm-service dom0)
