#!/bin/bash
# guest-init.sh — runs inside the micro-VM at boot (vrnetlab/native model).
#
#   1. Read per-node config from the config CD the launcher attaches (/dev/sr0):
#      nodename + first-boot.cfg (XR config: clab user, ssh/grpc/netconf + user cfg).
#   2. Discover the virtio mgmt NIC and the emulated igb data NICs.
#   3. Run the XRd vRouter container with host networking so XR shares the VM
#      mgmt NIC (XR owns the management address explicitly via first-boot.cfg),
#      and map each igb data NIC to Gi0/0/0/N.
#   4. When XR is up and MgmtEth is up, print the readiness marker on the console
#      so the vrnetlab launcher (watching the serial) marks the node up.
set -euo pipefail

LOG=/var/log/guest-init.log
# Mirror everything to the serial console too, so it shows up in the vrnetlab
# container's `docker logs` (the only window into the VM in production).
exec > >(tee -a "$LOG" /dev/console) 2>&1
echo "=== guest-init $(date -u +%FT%TZ) ==="

IMG=$(cat /etc/xrd/image)
READY_MARKER="CLAB_XRD_READY"

# ---------------------------------------------------------------------------
# 1. Per-node config from the launcher's config CD.
# ---------------------------------------------------------------------------
CFGDIR=/run/xrd-config; mkdir -p "$CFGDIR"
NODENAME="xrd"
FIRST_BOOT="$CFGDIR/first-boot.cfg"
if [ -b /dev/sr0 ]; then
    mount -o ro /dev/sr0 /mnt 2>/dev/null || true
    [ -f /mnt/nodename ] && NODENAME=$(cat /mnt/nodename)
    [ -f /mnt/first-boot.cfg ] && cp /mnt/first-boot.cfg "$FIRST_BOOT"
    umount /mnt 2>/dev/null || true
fi
[ -f "$FIRST_BOOT" ] || printf 'hostname %s\n!\nend\n' "$NODENAME" > "$FIRST_BOOT"
echo "node=$NODENAME image=$IMG"

# ---------------------------------------------------------------------------
# 2. Discover NICs. Mgmt = virtio_net; data = igb (8086:10c9), PCI-sorted.
# ---------------------------------------------------------------------------
MGMT_IF=""
for n in /sys/class/net/*; do
    ifc=$(basename "$n")
    [ "$ifc" = "lo" ] && continue
    drv=$(basename "$(readlink -f "$n/device/driver" 2>/dev/null)" 2>/dev/null || true)
    [ "$drv" = "virtio_net" ] && { MGMT_IF=$ifc; break; }
done
echo "mgmt interface: ${MGMT_IF:-NONE}"

mapfile -t PCIS < <(lspci -Dn | awk '$3 ~ /8086:10c9/ {print $1}' | sort)
echo "igb data NIC(s): ${PCIS[*]:-none}"

# NB: xr_name is NOT valid for pci interfaces on vRouter; XR numbers them
# Gi0/0/0/0,1,... in the order listed here. We list in PCI order, which matches
# the eth1,eth2,... order (each ethN -> rpN -> ascending PCI address).
XR_IFS=""
for d in "${PCIS[@]}"; do
    short=${d#0000:}
    XR_IFS+="${XR_IFS:+;}pci:${short}"
done
echo "XR_INTERFACES=$XR_IFS"

# XR owns the mgmt address explicitly (configured in first-boot.cfg); the linux
# mgmt NIC is just the L2 carrier (tc-mirred passthrough to the clab mgmt net).
MGMT_ENV="linux:${MGMT_IF},chksum"

# ---------------------------------------------------------------------------
# 3. Launch XRd vRouter (host networking so XR shares the VM mgmt NIC).
# ---------------------------------------------------------------------------
docker rm -f xrd >/dev/null 2>&1 || true
ARGS=(
  -d --name xrd --restart unless-stopped --network host
  --privileged --security-opt apparmor=xrd-unconfined
  --mount "type=bind,source=$FIRST_BOOT,target=/etc/xrd/first-boot.cfg"
  --env XR_FIRST_BOOT_CONFIG=/etc/xrd/first-boot.cfg
  --env XR_MGMT_INTERFACES="$MGMT_ENV"
)
[ -n "$XR_IFS" ] && ARGS+=(--env XR_INTERFACES="$XR_IFS")
echo "docker run ${ARGS[*]} $IMG"
docker run "${ARGS[@]}" "$IMG"

# ---------------------------------------------------------------------------
# 4. Wait for XR readiness, then signal the launcher via the console.
# ---------------------------------------------------------------------------
xrcli(){ timeout 25 docker exec xrd /pkg/bin/xr_cli "$1" 2>/dev/null; }

# Wait for the XR control plane, then confirm MgmtEth is up. XR takes ~2-3 min
# after the CLI first answers to apply config and bring MgmtEth up — that is the
# real readiness signal. External mgmt reachability is verified separately by the
# containerlab healthcheck, so we do not probe the address from the Linux netns
# (XR owns it, so it does not live there).
echo "waiting for XR control plane + management..."
for i in $(seq 1 120); do
    if xrcli "show version" | grep -qi "cisco IOS XR"; then
        # '|| true' is essential: under `set -e`, a non-matching grep would
        # otherwise abort guest-init before it ever emits the readiness marker.
        mgmtline=$(xrcli "show ipv4 vrf all interface brief" | grep -i Mgmt || true)
        echo "[t=$((i*10))s] XR up; MgmtEth: ${mgmtline:-pending}"
        case "$mgmtline" in
            *Up*Up*) echo "XR management is up"; break ;;
        esac
    else
        echo "[t=$((i*10))s] XR converging..."
    fi
    sleep 10
done

# Emit the readiness marker on the serial console for the vrnetlab launcher.
# Repeat a few times so the launcher's serial poller reliably catches it.
for n in 1 2 3 4 5; do
    for tty in /dev/ttyS0 /dev/console; do
        [ -w "$tty" ] && echo "${READY_MARKER}:${NODENAME}" > "$tty" 2>/dev/null || true
    done
    sleep 2
done
echo "=== guest-init done ==="
