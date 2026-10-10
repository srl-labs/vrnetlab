# TrueNAS

This is the vrnetlab docker image for [TrueNAS](https://www.truenas.com/) (Community Edition, formerly TrueNAS SCALE):
a ZFS storage appliance serving NFS, SMB, iSCSI, NVMe over TCP and S3.

## Building the docker image

Download the TrueNAS ISO from <https://www.truenas.com/download/> and place it in this directory, then run `make`.

```
TrueNAS-27.0.0-RC.1.iso  ->  vrnetlab/truenas_truenas:27.0.0-RC.1
```

TrueNAS ships as an installer ISO, so the build installs it: `make` builds a base image with an empty boot disk, then
runs that image once with `--install` (privileged, the ISO bind-mounted read-only). The launcher boots the ISO's
kernel and initrd directly (no GRUB menu, serial console on ttyS0) and drives the installer's own JSON-RPC service
(port 8080): boot disk, `truenas_admin` password, DHCP on the management NIC. The installed disk is committed into the
image; the ISO is not part of it. The build needs KVM and takes about 4 minutes; the image is about 3.8 GB.

Tested with TrueNAS 27.0.0-RC.1.

## System requirements

|              | default | variable          |
|--------------|---------|-------------------|
| vCPU         | 4       | `TRUENAS_SMP`     |
| RAM          | 8192 MB | `TRUENAS_RAM`     |
| boot disk    | 16 GB (thin) | -            |
| data disks   | 4 x 50 GB (thin) | `TRUENAS_DATA_DISKS`, `TRUENAS_DATA_DISK_SIZE` |

TrueNAS needs at least 8 GB of RAM. ZFS uses free memory as its cache (ARC); `TRUENAS_ARC_MAX_MB` caps it, which
keeps the VM's resident memory predictable on a shared host.

## Usage

Use the `generic_vm` kind in containerlab:

```yaml
topology:
  nodes:
    truenas1:
      kind: generic_vm
      image: vrnetlab/truenas_truenas:27.0.0-RC.1
      binds:
        - truenas1:/persist          # optional: keep the disks across redeploys
      env:
        USERNAME: admin
        PASSWORD: Truenas123
        TRUENAS_RAM: "12288"
        TRUENAS_ARC_MAX_MB: "4096"
```

The node is healthy once the TrueNAS middleware is ready and the first-boot configuration has run (about 1.5 to
2 minutes on first boot, under a minute afterwards).

### First boot

The launcher configures the system through the TrueNAS API (JSON-RPC 2.0 over the websocket `/api/current`):

* hostname = the node name
* `truenas_admin`'s password = `PASSWORD`, SSH password login enabled, the SSH service started
* the ZFS ARC cap (`TRUENAS_ARC_MAX_MB`, optional)
* pool `tank` (`TRUENAS_POOL`, empty = no pool) on the data disks, RAIDZ1 with three or more disks, else a stripe
  (`TRUENAS_POOL_LAYOUT`)
* an administrator `USERNAME` (unless it is `truenas_admin`) with the same password, home on the pool
* the startup config, if any (below)

A marker next to the disks records that the first boot ran, so with `/persist` mounted a redeploy keeps the whole
system (configuration, pool, shares, data) and skips this step.

### Startup configuration

`startup-config` takes a JSON list of TrueNAS API calls, applied in order on first boot; add `"job": true` for methods
that run as jobs:

```json
[
  {"method": "pool.dataset.create", "params": [{"name": "tank/nfs"}]},
  {"method": "sharing.nfs.create", "params": [{"path": "/mnt/tank/nfs", "networks": ["10.0.1.0/24"]}]},
  {"method": "service.update", "params": ["nfs", {"enable": true}]},
  {"method": "service.control", "params": ["START", "nfs"], "job": true}
]
```

### Interfaces

| container | TrueNAS | notes |
|-----------|---------|-------|
| eth0 | `ens1` | management (DHCP, 10.0.0.15 behind the container's address) |
| eth1 | `ens2` | data |
| ethN | `ens(N+1)` | data |

The NICs are virtio-net with a reported link speed of 10 Gb/s (`TRUENAS_NIC_SPEED`, Mb/s): Linux 802.3ad bonding
refuses members that report no speed, so LACP link aggregations work as on hardware.

### Access

* web UI / API: `https://<node>` (and `http://`), user `truenas_admin` or `USERNAME`
* SSH: `ssh admin@<node>`
* serial console (the TrueNAS console menu): `telnet <node> 5000`. The launcher never types on it; note that the
  menu acts on a number and Enter.

### Stopping

On SIGTERM (`docker stop`, `containerlab destroy`) the launcher sends the guest an ACPI power-off and waits up to
`TRUENAS_STOP_TIMEOUT` seconds (default 90) for it to halt; give the container that long to stop
(`docker stop -t 120`). An abrupt stop risks the TrueNAS configuration database.

## Notes

* TrueNAS 25.04+ removed the REST API (`/api/v2.0`); tooling must use the websocket JSON-RPC API.
* TrueNAS rejects two interfaces in the same IPv4 subnet.
* The management NIC (`ens1`) uses QEMU user networking, which keeps a remote client's real source address on the forwarded ports (only
  loopback clients appear as 10.0.0.2). If you move TrueNAS's default route to a data interface, add a static route for the containerlab
  management network via 10.0.0.2 first (`staticroute.create {"destination": "172.20.20.0/24", "gateway": "10.0.0.2"}`), or the web UI,
  SSH and the launcher's API session on the management address stop answering. Make `ens1` static (10.0.0.15/24, no gateway) so only
  one default route remains.
* Community Edition features that need a license (HA, NVMe-oF ANA/SPDK/RDMA, S3 versioning and audit, ...) are not
  available.
