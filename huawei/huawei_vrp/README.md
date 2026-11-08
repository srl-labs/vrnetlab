# Huawei NE40E

Rename your qcow2 disk image to conform to the following pattern:

```
huawei_ne40e-<version>.qcow2
or
huawei_ce12800-<version>.qcow2
```

Build the image with:

```
make
```

The resulting image will be tagged as:

```
vrnetlab/huawei_vrp:<platform>-<version>
```

for example, if the qcow2 image is named `huawei_ne40e-8.180.qcow2`, then the image will be tagged as:

```
vrnetlab/huawei_vrp:ne40e-8.180
```

## eNSP images

eNSP (Enterprise Network Simulation Platform) is Huawei's network simulator
for training and lab use, no longer maintained by Huawei. eNSP
V100R003C00SPC100 distributes its VRP8 devices as separate device packages:
`NE40E.zip`, `NE5000E.zip`, `NE9000.zip`, `CX.zip` and `CE.zip`, each
containing a single VirtualBox disk (`NE40E.img`, `CE.img`, ...).

These disks do not start VRP on their own: in eNSP a control service inside
the VM (`svrp_agent.sh`) creates and starts the router on request from the
GUI. `ensp/convert.sh` replaces it with a boot script (`ensp/rc.local`) and
produces a regular `huawei_<platform>-<version>.qcow2`.

Put the zip files (or the extracted `.vdi`) in this directory and run `make`
as usual. Each image is converted once, a `.<name>.converted` stamp file marks
it as done. The script can also be run by hand:

```
./ensp/convert.sh NE40E.zip
```

The disk is modified offline with `debugfs`, root is not required. The
script needs `qemu-img`, `debugfs` (e2fsprogs), `sfdisk`, `jq`, `python3`,
`curl`, `ar` (binutils) and `xz`.

The libpcap shipped in the images is too old to pass 802.1Q tagged frames to
VRP, so it is replaced with `libpcap0.8_1.6.2-2_i386.deb` from Debian 8
(the release the images are based on). The package is downloaded from
archive.debian.org on the first conversion and its checksum is verified. To
build offline, put the file in this directory beforehand.

Tested images:

| eNSP image   | Platform | VRP version        | Data ports            | Mgmt port            |
|--------------|----------|--------------------|-----------------------|----------------------|
| NE40E.zip    | ne40e    | V800R011C00SPC607  | Ethernet3/0/0-9       | GigabitEthernet0/0/0 |
| NE5000E.zip  | ne5000e  | V800R011C00SPC607  | Ethernet3/0/0-9       | GigabitEthernet0/0/0 |
| NE9000.zip   | ne9000   | V800R011C00SPC607  | Ethernet3/0/0-9       | GigabitEthernet0/0/0 |
| CX.zip       | cx       | V800R011C00SPC607  | Ethernet3/0/0-9       | GigabitEthernet0/0/0 |
| CE.zip       | ce12800  | V200R005C10SPC607  | GE3/0/0-9             | MEth0/0/0            |

Data ports map to `eth1` to `eth10` of the container in order.

Notes:

* The node is healthy after 5 to 7 minutes (CE12800 about 2.5 minutes).
  Right after boot `commit` may fail with `The system is busy in building
  system configurations`, retry after a minute.
* CE12800 ports are layer 2 and shut down by default, use `undo portswitch`
  and `undo shutdown` before assigning an address. On this version a few
  `ssh client` bootstrap commands are rejected, this does not affect access
  to the node.
* NE40E, NE5000E, NE9000 and CX generate the system MAC (LLDP chassis ID)
  from the time VRP starts and pick a new one on every reboot. So that nodes
  of a lab get different ones, each node starts VRP in its own 4 second slot
  of a 2 minute cycle, taken from the container address (modulo 30). Boot
  takes up to 2 minutes longer. Nodes whose addresses differ by a multiple
  of 30 may still share the system MAC.
* The CE12800 system MAC (`3864-0111-1200`) is the same on every node.
  Routed ports (`undo portswitch`) get their own MAC addresses and work
  between CE12800 nodes, VLANIF interfaces between them do not.
* USG6000V and the VRP5 devices (AR, S3700, S5700, USG5500) are not
  supported: the former gets its interfaces from the eNSP control service,
  the latter are Windows programs and are not shipped as disk images.
