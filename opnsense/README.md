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

3. **Pre-configure the base image once** (see *Image preparation* below). This
   bakes management networking + SSH into `/conf/config.xml`.
4. Run `make`. It builds `vrnetlab/opnsense_opnsense:<version>` and also tags it
   as `vrnetlab/opnsense:<version>`.

Tested with `OPNsense-26.1.6-nano-amd64.img`.

## Image preparation (one-time, baked into the qcow2)

The stock nano image puts a static `192.168.1.1` on the LAN and ships with SSH
disabled, so it is unreachable through vrnetlab's management plane. Boot the
qcow2 once and apply two changes to `/conf/config.xml`:

* set the **LAN interface (vtnet0) to DHCP** so it picks up vrnetlab's
  management address (`10.0.0.15`);
* **enable sshd** with root login and password auth.

```sh
# in the OPNsense shell (console menu -> 8):
sed -i '' -e 's|<ipaddr>192.168.1.1</ipaddr>|<ipaddr>dhcp</ipaddr>|' /conf/config.xml
sed -i '' -e 's|<subnet>24</subnet>|<subnet></subnet>|' /conf/config.xml
sed -i '' -e 's|<group>admins</group>|<group>admins</group><enabled>enabled</enabled><permitrootlogin>1</permitrootlogin><passwordauth>1</passwordauth>|' /conf/config.xml
reboot
```

On first boot OPNsense runs the interface-assignment wizard once
(WAN -> vtnet1, LAN -> vtnet0); answer it before applying the edits so the
assignment is persisted too.

## Usage

The first interface (`vtnet0`) is the LAN/management interface; data interfaces
start at `vtnet1` (WAN), `vtnet2` (OPT1), ...

Default credentials: **root / opnsense**. The web GUI is on HTTPS (port 443).

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
