# vrnetlab / OPNsense

This is the vrnetlab docker image for [OPNsense](https://opnsense.org/), the
FreeBSD-based firewall/router.

## Building the docker image

OPNsense is distributed as a live installer (`dvd`/`vga`/`serial`) and as a
**pre-installed `nano`** disk image. Use the **nano** image — it boots straight
to a persistent system on a serial console, which is exactly what vrnetlab
needs (no install step).

1. Download `OPNsense-<version>-nano-amd64.img.bz2` from an OPNsense mirror.
2. Decompress and convert it to qcow2 in this directory:

   ```
   bunzip2 -k OPNsense-26.1.6-nano-amd64.img.bz2
   qemu-img convert -f raw -O qcow2 \
       OPNsense-26.1.6-nano-amd64.img OPNsense-26.1.6.qcow2
   ```

3. Run `make`. It builds `vrnetlab/opnsense_opnsense:<version>` and also tags it
   as `vrnetlab/opnsense:<version>`.

Tested with `OPNsense-26.1.6-nano-amd64.img`.

The image is used unmodified — no manual preparation is required. The stock nano
image puts a static `192.168.1.1` on the LAN and ships with SSH disabled, so on
the first boot `launch.py` logs in over the console, configures the LAN interface
(`vtnet0`) as the management interface, enables sshd (root login + password
auth), and reboots once to apply.

### Management modes

The LAN is configured to match vrnetlab's management datapath:

* **host-forwarded** (default): `vtnet0` is set to DHCP and picks up the
  address qemu's user-mode networking hands out (`10.0.0.15`).
* **transparent / passthrough** (`CLAB_MGMT_PASSTHROUGH=true`): `vtnet0` is
  given the static address containerlab assigned to the container's `eth0`,
  plus a default gateway, so the node shows its real management IP. Combine
  with `CLAB_MGMT_DHCP=true` to leave the LAN on DHCP for an external server.

## Usage

The first interface (`vtnet0`) is the LAN/management interface; data interfaces
start at `vtnet1` (WAN), `vtnet2` (OPT1), ...

Default credentials: **root / opnsense** (fixed by the appliance image). The web
GUI is on HTTPS (port 443).

First boot takes a little longer than other images because of the configure +
reboot cycle (~90s).

### With containerlab

There is no native `opnsense` kind, so use `generic_vm`:

```yaml
  nodes:
    fw:
      kind: generic_vm
      image: vrnetlab/opnsense:26.1.6
```

## System requirements

CPU: 1 core
RAM: 2048 MB
DISK: ~8 GB (the nano image is resized at build time)
