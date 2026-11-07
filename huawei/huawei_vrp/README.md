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

Disk images shipped with eNSP (`NE40E.zip`, `CE.zip`, ... each containing a
VirtualBox disk) can be used as well. They do not start VRP on their own: in
eNSP a control service inside the VM (`svrp_agent.sh`) creates and starts the
router on request from the GUI. `ensp/convert.sh` replaces it with a boot
script (`ensp/rc.local`) and produces a regular
`huawei_<platform>-<version>.qcow2`.

Put the zip files (or the extracted `.vdi`) in this directory and run `make`
as usual. Each image is converted once, a `.<name>.converted` stamp file marks
it as done. The script can also be run by hand:

```
./ensp/convert.sh NE40E.zip
```

The disk is modified offline with `debugfs`, root is not required. The
script needs `qemu-img`, `debugfs` (e2fsprogs), `sfdisk`, `jq` and `python3`.

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

* The node is healthy after about 5 minutes (CE12800 about 2.5 minutes).
  Right after boot `commit` may fail with `The system is busy in building
  system configurations`, retry after a minute.
* CE12800 ports are layer 2 and shut down by default, use `undo portswitch`
  and `undo shutdown` before assigning an address. On this version a few
  `ssh client` bootstrap commands are rejected, this does not affect access
  to the node.
* USG6000V and the VRP5 devices (AR, S3700, S5700, USG5500) are not
  supported: the former gets its interfaces from the eNSP control service,
  the latter are Windows programs and are not shipped as disk images.
