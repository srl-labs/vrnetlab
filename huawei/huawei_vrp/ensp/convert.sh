#!/usr/bin/env bash
#
# Convert a Huawei eNSP VRP8 disk image into a qcow2 that boots VRP without
# the eNSP control service.
#
# usage: ensp/convert.sh <image.zip|.vdi|.img|.vmdk|.qcow2> [output-dir]
#
# The root filesystem is modified offline with debugfs, root is not required.
# The result is named huawei_<platform>-<version>.qcow2 so the regular build
# picks it up.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
RC_LOCAL="$SCRIPT_DIR/rc.local"

die() {
	echo "ERROR: $*" >&2
	exit 1
}

[ $# -ge 1 ] && [ -f "$1" ] || die "usage: $0 <image> [output-dir]"
for tool in qemu-img debugfs sfdisk jq python3; do
	command -v "$tool" >/dev/null || die "$tool is required"
done

src=$(readlink -f "$1")
out_dir=$(readlink -f "${2:-.}")

work=$(mktemp -d "$out_dir/.ensp-XXXXXX")
trap 'rm -rf "$work"' EXIT

disk="$src"
if [[ "$src" == *.zip ]]; then
	python3 -I -m zipfile -e "$src" "$work/zip"
	disk=$(find "$work/zip" -type f \
		\( -iname '*.img' -o -iname '*.vdi' -o -iname '*.vmdk' -o -iname '*.qcow2' \) \
		-printf '%s %p\n' | sort -rn | head -1 | cut -d' ' -f2-)
	[ -n "$disk" ] || die "no disk image found in $src"
fi

echo "Converting $(basename "$disk") to raw"
format=$(qemu-img info --output=json "$disk" | jq -r .format)
qemu-img convert -f "$format" -O raw "$disk" "$work/disk.raw"
rm -rf "$work/zip"

# eNSP images have a single Linux root partition followed by swap.
read -r start size < <(sfdisk -J "$work/disk.raw" |
	jq -r '[.partitiontable.partitions[] | select(.type == "83")][0] | "\(.start) \(.size)"')
[[ "$start" =~ ^[0-9]+$ ]] || die "no Linux partition found"

dd if="$work/disk.raw" of="$work/root.raw" bs=1M status=none conv=sparse \
	iflag=skip_bytes,count_bytes skip=$((start * 512)) count=$((size * 512))

fs_cat() { debugfs -R "cat $1" "$work/root.raw" 2>/dev/null; }
fs_ls() { debugfs -R "ls $1" "$work/root.raw" 2>/dev/null | tr -s ' ' '\n'; }

fs_ls /opt | grep -qx svrp_agent.sh || die "not an eNSP VRP8 image (no /opt/svrp_agent.sh)"

product=$(fs_ls /opt/v8pkgpool | sed -n 's/^v8_current_pkg\.//p' | head -1)
package=$(fs_ls /opt/v8pkgpool | grep '^VRPV' | head -1)
version=$(grep -oE 'V[0-9]{3}R[0-9]{3}C[0-9]{2}(SPC[0-9]{3})?' <<<"$package" | head -1)
[ -n "$product" ] && [ -n "$version" ] || die "VRP package not found in /opt/v8pkgpool"

# svrp product name -> platform name used by this build
case "$product" in
ne40e) platform=ne40e ;;
ne5ke | ne5ke_new) platform=ne5000e ;;
ne9000) platform=ne9000 ;;
cx | cx_new) platform=cx ;;
s6800 | s12800) platform=ce12800 ;;
*) die "unsupported eNSP product: $product" ;;
esac
echo "Found $platform $version"

# The original rc.local verifies md5 sums of a few system files, including
# itself, and halts the VM on mismatch. Update the sum for our rc.local.
fs_cat /etc/data/cfgfile.list >"$work/cfgfile.list"
fs_cat /etc/data/rc.local >"$work/rc.local.ensp"
[ -s "$work/cfgfile.list" ] || die "/etc/data/cfgfile.list not found"
sum=$(md5sum <"$RC_LOCAL" | cut -d' ' -f1)
sed -i "s|^[0-9a-f]\{32\}  /etc/rc.local$|$sum  /etc/rc.local|" "$work/cfgfile.list"

debugfs -w "$work/root.raw" -f - >/dev/null 2>&1 <<EOF
write $work/rc.local.ensp /etc/data/rc.local.ensp
rm /etc/data/rc.local
write $RC_LOCAL /etc/data/rc.local
sif /etc/data/rc.local mode 0100755
rm /etc/data/cfgfile.list
write $work/cfgfile.list /etc/data/cfgfile.list
sif /etc/data/cfgfile.list mode 0100644
EOF
fs_cat /etc/data/rc.local | cmp -s - "$RC_LOCAL" || die "failed to install rc.local"

dd if="$work/root.raw" of="$work/disk.raw" bs=1M status=none conv=notrunc,sparse \
	oflag=seek_bytes seek=$((start * 512))

target="$out_dir/huawei_${platform}-${version}.qcow2"
qemu-img convert -c -O qcow2 "$work/disk.raw" "$target"
echo "Created $(basename "$target")"
