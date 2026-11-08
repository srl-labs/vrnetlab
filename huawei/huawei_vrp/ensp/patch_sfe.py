#!/usr/bin/env python3
"""Let ARP broadcasts be bridged in bridge domains that have a VBDIF.

The software forwarding engine of the eNSP VRP8 images (libsfe.so) hands a
broadcast frame received in a bridge domain to the layer 3 path only when the
bridge domain has a VBDIF interface, the frame is never flooded. ARP requests
between hosts in such a bridge domain (and over VXLAN) never reach the other
host.

For ARP broadcasts the patch does what libsfe already does in VLAN mode
(SFE_EthL2McMacAccess): a copy goes to the control plane with
SFE_ParallelToCp() and the frame itself is bridged. Other broadcast and
multicast frames are left alone.

usage: patch_sfe.py libsfe.so output
"""

import struct
import sys

# SFE_EthAccess, after SFE_EthBdEnableGet():
#   movzbl 0x26(%esp),%eax ; cmp $0x1,%al ; mov %al,0x1f(%esp) ; je <l3 path>
SITE = bytes.fromhex("0fb64424263c018844241f0f84")
# edx/ecx are loaded from the stack right after SFE_EthBdEnableGet() returns,
# both paths take them as arguments.
REGS = bytes.fromhex("8b54242c") + b"\x81\x38\x00\x00\x5e\x00" + bytes.fromhex("884c24108b4c2428")

# see the comment at the top, assembled at offset 0
CAVE = bytes.fromhex(
    "8b5608"              # mov    0x8(%esi),%edx       frame data
    "833aff"              # cmpl   $0xffffffff,(%edx)
    "754d"                # jne    l3
    "66837a04ff"          # cmpw   $0xffff,0x4(%edx)
    "7546"                # jne    l3
    "8d4a0c"              # lea    0xc(%edx),%ecx       skip up to two tags
    "6681398100"          # cmpw   $0x81,(%ecx)
    "750d"                # jne    1f
    "83c104"              # add    $0x4,%ecx
    "6681398100"          # cmpw   $0x81,(%ecx)
    "7503"                # jne    1f
    "83c104"              # add    $0x4,%ecx
    "6681390806"          # 1: cmpw $0x608,(%ecx)       ARP
    "7528"                # jne    l3
    "ff74240c"            # push   0xc(%esp)
    "6a0a"                # push   $0xa
    "6a0e"                # push   $0xe
    "56"                  # push   %esi
    "e800000000"          # call   SFE_ParallelToCp@plt
    "83c410"              # add    $0x10,%esp
    "c644242600"          # movb   $0x0,0x26(%esp)
    "c644241f00"          # movb   $0x0,0x1f(%esp)
    "8b54242c"            # mov    0x2c(%esp),%edx
    "8b4c2428"            # mov    0x28(%esp),%ecx
    "e900000000"          # jmp    <bridge path>
    "8b54242c"            # l3: mov 0x2c(%esp),%edx
    "8b4c2428"            # mov    0x28(%esp),%ecx
    "e900000000"          # jmp    <l3 path>
)
CALL_PTC, JMP_BRIDGE, JMP_L3 = 0x36, 0x50, 0x5d


def die(msg):
    sys.exit(f"patch_sfe: {msg}")


def find_once(data, pattern, what):
    pos = data.find(pattern)
    if pos < 0 or data.find(pattern, pos + 1) >= 0:
        die(f"{what} not found")
    return pos


def sections(data):
    shoff, = struct.unpack_from("<I", data, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x2E)
    hdrs = [struct.unpack_from("<10I", data, shoff + i * shentsize) for i in range(shnum)]
    strtab = hdrs[shstrndx][4]
    names = {}
    for h in hdrs:
        end = data.index(b"\0", strtab + h[0])
        names[data[strtab + h[0]:end].decode()] = h
    return names


def plt_entry(data, symbol):
    """Address of the PLT stub that jumps through the GOT slot of symbol."""
    sec = sections(data)
    dynsym, dynstr, relplt = sec[".dynsym"], sec[".dynstr"], sec[".rel.plt"]
    gotplt, plt = sec[".got.plt"], sec[".plt"]
    for off in range(relplt[4], relplt[4] + relplt[5], 8):
        r_offset, r_info = struct.unpack_from("<II", data, off)
        st_name, = struct.unpack_from("<I", data, dynsym[4] + (r_info >> 8) * 16)
        end = data.index(b"\0", dynstr[4] + st_name)
        if data[dynstr[4] + st_name:end] != symbol:
            continue
        # jmp *disp(%ebx), ebx points to .got.plt
        stub = b"\xff\xa3" + struct.pack("<i", r_offset - gotplt[3])
        pos = data.find(stub, plt[4], plt[4] + plt[5])
        if pos < 0:
            die("PLT stub not found")
        return pos - plt[4] + plt[3]
    die(f"{symbol.decode()} not imported")


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__.strip().splitlines()[-1])
    data = bytearray(open(sys.argv[1], "rb").read())
    if data[:4] != b"\x7fELF" or data[4] != 1:
        die("not a 32-bit ELF file")

    site = find_once(data, SITE, "patch site")
    find_once(data, REGS, "register setup")
    je = site + len(SITE) - 2
    l3 = je + 6 + struct.unpack_from("<i", data, je + 2)[0]
    bridge = je + 6

    # The cave goes into the padding after the text segment, the page it
    # is in is mapped executable anyway. File offsets equal addresses there.
    phoff, = struct.unpack_from("<I", data, 0x1C)
    phentsize, phnum = struct.unpack_from("<HH", data, 0x2A)
    loads = []
    for i in range(phnum):
        ph = phoff + i * phentsize
        p_type, p_offset, p_vaddr, _, p_filesz, _, p_flags = struct.unpack_from("<7I", data, ph)
        if p_type == 1:
            loads.append((ph, p_offset, p_vaddr, p_filesz, p_flags))
    ph, offset, vaddr, filesz, flags = loads[0]
    if offset != 0 or vaddr != 0 or not flags & 1 or len(loads) < 2:
        die("unexpected segment layout")
    if data[l3:l3 + CALL_PTC] == CAVE[:CALL_PTC]:
        die("already patched")
    cave = (filesz + 15) & ~15
    if cave + len(CAVE) > loads[1][1] or any(data[filesz:cave + len(CAVE)]):
        die("no room after the text segment")

    code = bytearray(CAVE)
    ptc = plt_entry(data, b"SFE_ParallelToCp")
    for at, target in ((CALL_PTC, ptc), (JMP_BRIDGE, bridge), (JMP_L3, l3)):
        struct.pack_into("<i", code, at + 1, target - (cave + at + 5))
    data[cave:cave + len(code)] = code
    struct.pack_into("<i", data, je + 2, cave - (je + 6))
    struct.pack_into("<II", data, ph + 16, cave + len(code), cave + len(code))

    open(sys.argv[2], "wb").write(data)


if __name__ == "__main__":
    main()
