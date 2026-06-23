#!/bin/bash
# make-golden.sh — build the "golden" micro-VM image from scratch.
#
# Produces a qcow2 of a minimal Ubuntu VM prepared to host the XRd vRouter
# container: cgroups v1, 1 GiB hugepages, IOMMU + vfio-pci, Docker (overlay2),
# the AppArmor profile, the guest-init service, and the XRd vRouter image
# preloaded. The only inputs are a stock Ubuntu cloud image (downloaded) and the
# XRd vRouter container image, so the result is fully reproducible.
#
# Provisioning runs inside a throwaway boot of the cloud image driven by
# cloud-init; the VM powers itself off when done and the disk is the golden.
# Depends only on qemu, cloud-image-utils and curl (no libguestfs).
#
# Required env:
#   XRD_IMAGE_TAR   path to a `docker save` tarball of the XRd vRouter image
# Optional env:
#   XRD_IMAGE_REF   image reference guest-init runs (default: read from the tarball)
#   OUT             output qcow2 path (default /golden.qcow2)
#   UBUNTU_IMG_URL  cloud image URL (default: Ubuntu 24.04 amd64)
#   PROV_RAM        provisioning VM memory in MiB (default 4096)
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
: "${XRD_IMAGE_TAR:?set XRD_IMAGE_TAR to the XRd vRouter docker-save tarball}"
out=${OUT:-/golden.qcow2}
ubuntu_url=${UBUNTU_IMG_URL:-https://cloud-images.ubuntu.com/releases/24.04/release/ubuntu-24.04-server-cloudimg-amd64.img}
prov_ram=${PROV_RAM:-4096}
marker="GOLDEN_PROVISIONED_OK"

# Default the image reference to the tag recorded in the tarball.
xrd_ref=${XRD_IMAGE_REF:-$(tar -xOf "$XRD_IMAGE_TAR" manifest.json \
    | sed -E 's/.*"RepoTags":\["([^"]+)".*/\1/')}
[ -n "$xrd_ref" ] || { echo "ERROR: could not determine XRd image reference" >&2; exit 1; }
echo "XRd image reference: $xrd_ref"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

echo "== fetching Ubuntu cloud image =="
curl -fSL "$ubuntu_url" -o "$work/base.img"
cp --sparse=always "$work/base.img" "$out"
qemu-img resize "$out" 20G

echo "== staging XRd image for the provisioning VM (9p share) =="
share="$work/share"
mkdir -p "$share"
cp "$XRD_IMAGE_TAR" "$share/xrd.tar"

echo "== rendering cloud-init =="
b64() { base64 -w0 "$1"; }
cat > "$work/meta-data" <<EOF
instance-id: xrd-golden
local-hostname: xrd-golden
EOF
cat > "$work/user-data" <<EOF
#cloud-config
write_files:
  - path: /usr/local/bin/guest-init.sh
    permissions: '0755'
    encoding: b64
    content: $(b64 "$here/guest-init.sh")
  - path: /etc/systemd/system/xrd-guest-init.service
    encoding: b64
    content: $(b64 "$here/xrd-guest-init.service")
  - path: /etc/netplan/90-mgmt.yaml
    permissions: '0600'
    encoding: b64
    content: $(b64 "$here/netplan-mgmt.yaml")
  - path: /etc/cloud/cloud.cfg.d/99-disable-network.cfg
    encoding: b64
    content: $(b64 "$here/cloud-init-disable-net.cfg")
  - path: /etc/apparmor.d/xrd-unconfined
    encoding: b64
    content: $(b64 "$here/xrd-unconfined")
  - path: /etc/docker/daemon.json
    content: '{ "storage-driver": "overlay2", "features": { "containerd-snapshotter": false } }'
  - path: /etc/sysctl.d/99-xrd.conf
    content: |
      fs.inotify.max_user_instances=64000
      fs.inotify.max_user_watches=64000
      net.core.netdev_max_backlog=300000
      net.core.optmem_max=67108864
      net.core.rmem_default=67108864
      net.core.rmem_max=67108864
      net.core.wmem_default=67108864
      net.core.wmem_max=67108864
      net.ipv4.udp_mem=1124736 10000000 67108864
  - path: /etc/modules-load.d/xrd.conf
    content: |
      dummy
      nf_tables
      vfio-pci
  - path: /etc/xrd/image
    content: "$xrd_ref\n"
  - path: /usr/local/bin/provision.sh
    permissions: '0755'
    content: |
      #!/bin/bash
      set -euo pipefail
      curl -fsSL https://get.docker.com | sh
      systemctl restart docker
      mount -t 9p -o trans=virtio,version=9p2000.L,ro xrdshare /mnt
      docker load -i /mnt/xrd.tar
      umount /mnt
      rm -f /etc/netplan/50-cloud-init.yaml
      sed -i 's|^GRUB_CMDLINE_LINUX=.*|GRUB_CMDLINE_LINUX="systemd.unified_cgroup_hierarchy=0 systemd.legacy_systemd_cgroup_controller=1 default_hugepagesz=1G hugepagesz=1G hugepages=3 intel_iommu=on iommu=pt transparent_hugepage=never"|' /etc/default/grub
      update-grub
      apparmor_parser -r /etc/apparmor.d/xrd-unconfined
      systemctl enable xrd-guest-init.service
      systemctl mask serial-getty@ttyS0.service
      echo "$marker" > /dev/console
      sync
      poweroff
runcmd:
  - [ bash, /usr/local/bin/provision.sh ]
EOF
cloud-localds "$work/seed.iso" "$work/user-data" "$work/meta-data"

echo "== running provisioning VM (cloud-init prepares the image, then powers off) =="
timeout 1800 qemu-system-x86_64 \
  -enable-kvm -machine q35 -cpu host -smp 4 -m "$prov_ram" \
  -drive file="$out",if=virtio,format=qcow2 \
  -drive file="$work/seed.iso",if=virtio,format=raw \
  -netdev user,id=net0 -device virtio-net-pci,netdev=net0 \
  -fsdev local,id=xrdfs,path="$share",security_model=none,readonly=on \
  -device virtio-9p-pci,fsdev=xrdfs,mount_tag=xrdshare \
  -display none -serial file:"$work/console.log" -no-reboot || true

if ! grep -q "$marker" "$work/console.log"; then
    echo "ERROR: provisioning did not complete successfully. Console tail:" >&2
    tail -n 40 "$work/console.log" >&2 || true
    exit 1
fi

echo "== compacting =="
qemu-img convert -O qcow2 "$out" "$out.compact"
mv -f "$out.compact" "$out"
ls -lh "$out"
echo "== golden image ready: $out =="
