# VeloCloud SD-WAN components

vrnetlab images for the three VeloCloud (VMware/Broadcom SD-WAN) components:

| Component | Image file | Resulting image |
|---|---|---|
| VCE — Edge | `edge-VC_KVM_GUEST-x86_64-<ver>-…-updatable-ext4.qcow2` | `vrnetlab/velocloud_vce:<ver>` |
| VCC — Gateway | `velocloud-vcc-v3-<ver>-…-kvm.qcow2` | `vrnetlab/velocloud_vcc:<ver>` |
| VCO — Orchestrator | `velocloud-orchestrator-kvm-<ver>-….tar` | `vrnetlab/velocloud_vco:<ver>` |

`<ver>` is the first 4-part version in the filename, e.g. `6.4.4.0` or `6.4.2.13`.
The VCO tar (`rootfs.qcow2`, `store.qcow2`, `store2.qcow2`, `store3.qcow2`) is
extracted at build time; the image ships all four disks.

## Building

Put one or more of the files above in this directory and run:

```bash
make                                   # builds every VeloCloud file found here
make docker-image IMAGES=<one file>    # build a single one
```

Set `DOCKER_REGISTRY=registry.example.com/foo` to tag for another registry.

## Cloud-init (required)

Every node boots with **your own** cloud-init. Bind-mount the node's cloud-init
directory to `/config/cloud-init/`:

- `user-data`, `meta-data` and optional `network-config` are packed verbatim into
  a NoCloud ISO (volume `cidata`) and attached as a CD-ROM on every boot. A missing
  `meta-data` (or `user-data`) is replaced by an empty file, with a warning.
- If the directory only holds a ready-made `cdrom.iso`, that ISO is attached as-is.
- With neither, the VM boots without cloud-init (warning in the logs).

**The first VM interface is the management interface.** The cloud-init has to
match that; nothing in the image checks or rewrites it.

## Management / SSH

`ssh <container mgmt IP>` (port 22, and the other vrnetlab-forwarded TCP ports such
as 443) reaches the VM's first interface:

| Component | Management address inside the VM | Login (set by your cloud-init) |
|---|---|---|
| VCE | GE1, factory default `192.168.2.1/24` | `root` |
| VCC | `eth0`: first static IPv4 in `network-config`, otherwise DHCP `10.0.0.15` | `vcadmin` |
| VCO | `eth0`: first static IPv4 in `network-config`, otherwise DHCP `10.0.0.15` | `vcadmin` |

The VM never needs a default route towards the management network: connections
reach it from an on-link address in its own management subnet. To keep `eth0`
management-only with DHCP, ignore the DHCP routes and DNS so the default route
stays on the data interface (as in the example VCC and VCO `network-config`):

```yaml
  eth0:
    dhcp4: true
    dhcp4-overrides: { use-routes: false, use-dns: false }
```

## Health

The container reports healthy (`docker ps`, `containerlab inspect`) once its VM
is ready. What "ready" means depends on the component:

| Component | Ready when |
|---|---|
| VCE, VCC | The VM's SSH server answers. |
| VCO | cloud-init has finished: **1.** the serial console prints `Finished Cloud-init: Final Stage`, then **2.** logging in over SSH, `/var/log/cloud-init.log` contains `SUCCESS: running modules for final`. |

The VCO check makes sure the cloud-init `vco:` settings (system properties and
so on) have been applied before anything uses the Orchestrator. The VCO gets to
this point a few minutes after it boots, and the container log follows the steps:

```
[1/2] Waiting for 'Finished Cloud-init: Final Stage' on the console (0:02:05 elapsed)
[1/2] Console: 'Finished Cloud-init: Final Stage' seen after 0:03:56
[2/2] /var/log/cloud-init.log: final stage succeeded
cloud-init finished. Startup complete in: 0:04:06
```

Step 2 logs in with these environment variables (set them with `env:` on the
node). The `--username`/`--password` launch arguments are not used for this,
because containerlab always passes its own values there.

| Variable | Default | |
|---|---|---|
| `VC_SSH_USERNAME` | `vcadmin` | Login set by your cloud-init. |
| `VC_SSH_PASSWORD` | `Velocloud123` | Your cloud-init has to allow password logins (`ssh_pwauth: true`). |
| `VC_READY_CHECK` | per component | `ssh` or `cloud-init`. Overrides the table above, e.g. `ssh` for a VCO whose cloud-init sets no password. |

- A refused login logs a warning that names these variables, and the node stays
  unhealthy.
- If cloud-init records a failed final stage, the launcher logs an error and the
  node stays unhealthy.
- A VM that isn't ready after about 100 minutes is restarted, except in the two
  cases above: the VM is up there, and a reboot would not fix either.

Docker checks health every 30 s and marks a container `unhealthy` after three
failed checks, so a booting node shows `unhealthy` until it is ready; that means
"not ready yet", not "broken".

### VCC: move the Gateway's WAN to eth1

The Gateway software uses `eth0` as its WAN/VCMP interface by default and
resets `eth0` when it starts, which drops the management address. Since `eth0`
is the management interface here, the VCC cloud-init must move the WAN to
`eth1` in two files:

- `/etc/config/gatewayd`: the `wan` and `vcmp.interfaces` lists.
- `/etc/config/gatewayd-tunnel`: `wan="eth0"`. The Gateway's firewall
  (`VCMP_IN_ACL`) opens SSH, HTTPS, ICMP and DNS/NTP replies only on this
  interface and drops everything else arriving on `eth1`. If you skip it, `eth1`
  answers ARP but not ping, and replies to the Gateway's own outbound traffic
  over `eth1` (DNS, NTP, HTTPS to the VCO) are dropped.

Put the public address and default route on `eth1`, and keep `eth0` as DHCP
management only. Tested `user-data` / `network-config`:

```yaml
#cloud-config
hostname: vcg1
manage_etc_hosts: true
password: Velocloud123
chpasswd: { expire: False }
ssh_pwauth: True

bootcmd:
  # eth0 is the vrnetlab management interface: move the Gateway's WAN/VCMP to eth1
  - [ sed, -i, -e, 's/"wan": \["eth0"\]/"wan": ["eth1"]/', -e, 's/"vcmp.interfaces": \["eth0"\]/"vcmp.interfaces": ["eth1"]/', /etc/config/gatewayd ]
  - [ sed, -i, -e, 's/^wan="eth0"/wan="eth1"/', /etc/config/gatewayd-tunnel ]

runcmd:
  - chage -M -1 vcadmin
```

```yaml
version: 2
ethernets:
  eth0:                     # management only
    dhcp4: true
    dhcp4-overrides: { use-routes: false, use-dns: false }
  eth1:
    dhcp4: false
    addresses:
      - 100.64.65.2/24
    routes:
      - to: 0.0.0.0/0
        via: 100.64.65.1
```

Keep the `sed` command in list form: as a plain string, its `": ` makes the
whole `user-data` invalid YAML and cloud-init ignores all of it.

## Interfaces

Container `eth1`, `eth2`, … map to the VM's 2nd, 3rd, … NIC:

- VCE: `eth1` → GE2, `eth2` → GE3, `eth3` → GE4. GE2 shares the factory LAN
  with GE1 (192.168.2.1); its role after activation comes from the VCO profile,
  which may also change GE1 (and so SSH).
- VCC / VCO: `eth1` → VM `eth1`.

## Resources

| Component | vCPU | RAM | Data NICs |
|---|---|---|---|
| VCE | 3 | 4 GB | 3 |
| VCC | 3 | 8 GB | 1 |
| VCO | 12 | 54 GB | 1 |

Override per node with the `QEMU_SMP` / `QEMU_MEMORY` (MB) environment variables
and the `--nics` launch argument (number of data NICs).

## Example

[`example/`](example/) is a complete lab: VCO + VCC + VCE behind a Linux router.
The router takes a DHCP address on the host bridge `pnet0` (EVE-NG's uplink) and
NATs the lab subnets out of it, so every node can reach the Internet. On a host
without `pnet0`, change the `pnet0` node and its link to your own bridge.

```
example/
  velocloud.clab.yml
  cloud-init/
    vce1/  user-data, meta-data                  # GE1 192.168.2.1 (mgmt), GE3 172.25.0.2, GE4 100.64.66.2
    vcg1/  user-data, meta-data, network-config  # eth0 DHCP 10.0.0.15 (mgmt only), eth1 100.64.65.2, WAN moved to eth1
    vco/   user-data, meta-data, network-config  # eth0 DHCP 10.0.0.15 (mgmt only), eth1 100.64.64.2
```

```bash
cd example
sudo containerlab deploy -t velocloud.clab.yml
ssh root@clab-velocloud-vce1      # password Velocloud123
ssh vcadmin@clab-velocloud-vcg1
ssh vcadmin@clab-velocloud-vco
```

Copy a node's directory and change its addresses, hostname and `instance-id` to
add more nodes.
