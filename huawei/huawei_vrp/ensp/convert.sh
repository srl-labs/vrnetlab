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

# The svrp driver is a 32-bit program linked against libpcap 0.9.3, which
# does not restore the 802.1Q tags the kernel strips from received frames,
# so VLAN tagged traffic never reaches VRP. It is replaced with libpcap from
# Debian 8, the release the image is based on.
LIBPCAP_DEB=libpcap0.8_1.6.2-2_i386.deb
LIBPCAP_URL=https://archive.debian.org/debian/pool/main/libp/libpcap/$LIBPCAP_DEB
LIBPCAP_SHA256=ce227489fa4322a275dc189a857e450c75a1debbb39b57c1de7d3823bfffda6c
LIBPCAP_SO=libpcap.so.1.6.2

die() {
	echo "ERROR: $*" >&2
	exit 1
}

[ $# -ge 1 ] && [ -f "$1" ] || die "usage: $0 <image> [output-dir]"
for tool in qemu-img debugfs sfdisk jq python3 curl ar xz unsquashfs; do
	command -v "$tool" >/dev/null || die "$tool is required"
done

src=$(readlink -f "$1")
out_dir=$(readlink -f "${2:-.}")

work=$(mktemp -d "$out_dir/.ensp-XXXXXX")
trap 'rm -rf "$work"' EXIT

deb="$out_dir/$LIBPCAP_DEB"
if [ ! -f "$deb" ]; then
	echo "Downloading $LIBPCAP_DEB"
	curl -fsSL -o "$deb.part" "$LIBPCAP_URL" || die "failed to download $LIBPCAP_URL"
	mv "$deb.part" "$deb"
fi
echo "$LIBPCAP_SHA256  $deb" | sha256sum -c --quiet - || die "checksum mismatch: $deb"
ar p "$deb" data.tar.xz | tar -xJ -C "$work" "./usr/lib/i386-linux-gnu/$LIBPCAP_SO"

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

# Patch the forwarding engine so ARP broadcasts are bridged in bridge
# domains with a VBDIF interface, see patch_sfe.py. The library lives in the
# VRP package (squashfs), the patched copy is bind mounted over it on boot.
sfe_cmds=
for rpg in $(debugfs -R "ls -p /opt/v8pkgpool/$package/$product" "$work/root.raw" 2>/dev/null |
	awk -F/ '$6 ~ /\.rpg$/ { print $6 }'); do
	debugfs -R "dump /opt/v8pkgpool/$package/$product/$rpg $work/vrp.rpg" "$work/root.raw" 2>/dev/null
	unsquashfs -q -n -d "$work/rpg" "$work/vrp.rpg" module/libsfe.so >/dev/null 2>&1 || true
	rm -f "$work/vrp.rpg"
done
if [ -f "$work/rpg/module/libsfe.so" ] &&
	python3 -I "$SCRIPT_DIR/patch_sfe.py" "$work/rpg/module/libsfe.so" "$work/libsfe.so"; then
	sfe_cmds="write $work/libsfe.so /etc/data/libsfe.so
sif /etc/data/libsfe.so mode 0100644"
	echo "Patched libsfe.so"
else
	echo "WARNING: libsfe.so not patched, ARP is not flooded in bridge domains with a VBDIF" >&2
fi

# The original rc.local verifies md5 sums of a few system files, including
# itself, and halts the VM on mismatch. Update the sum for our rc.local.
fs_cat /etc/data/cfgfile.list >"$work/cfgfile.list"
fs_cat /etc/data/rc.local >"$work/rc.local.ensp"
[ -s "$work/cfgfile.list" ] || die "/etc/data/cfgfile.list not found"
sum=$(md5sum <"$RC_LOCAL" | cut -d' ' -f1)
sed -i "s|^[0-9a-f]\{32\}  /etc/rc.local$|$sum  /etc/rc.local|" "$work/cfgfile.list"

debugfs -w "$work/root.raw" -f - >/dev/null 2>&1 <<EOF
write $work/usr/lib/i386-linux-gnu/$LIBPCAP_SO /usr/lib/$LIBPCAP_SO
sif /usr/lib/$LIBPCAP_SO mode 0100644
rm /usr/lib/libpcap.so.1
symlink /usr/lib/libpcap.so.1 $LIBPCAP_SO
write $work/rc.local.ensp /etc/data/rc.local.ensp
rm /etc/data/rc.local
write $RC_LOCAL /etc/data/rc.local
sif /etc/data/rc.local mode 0100755
rm /etc/data/cfgfile.list
write $work/cfgfile.list /etc/data/cfgfile.list
sif /etc/data/cfgfile.list mode 0100644
$sfe_cmds
EOF
fs_cat /etc/data/rc.local | cmp -s - "$RC_LOCAL" || die "failed to install rc.local"
[ -z "$sfe_cmds" ] || fs_cat /etc/data/libsfe.so | cmp -s - "$work/libsfe.so" ||
	die "failed to install libsfe.so"
debugfs -R "stat /usr/lib/libpcap.so.1" "$work/root.raw" 2>/dev/null |
	grep -q "Fast link dest: \"$LIBPCAP_SO\"" || die "failed to install $LIBPCAP_SO"

dd if="$work/root.raw" of="$work/disk.raw" bs=1M status=none conv=notrunc,sparse \
	oflag=seek_bytes seek=$((start * 512))

target="$out_dir/huawei_${platform}-${version}.qcow2"
qemu-img convert -c -O qcow2 "$work/disk.raw" "$target"
echo "Created $(basename "$target")"
