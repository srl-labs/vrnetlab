# Dell Enterprise SONiC

This is the vrnetlab docker image for SONiC's VM.
The scripts in this directory are based on FreeBSD and VSRX kinds.

> Available with [containerlab](https://containerlab.dev) as [`dell_sonic`](https://containerlab.dev/manual/kinds/dell_sonic/) kind.

## Building the docker image

Download the latest Dell SONiC qcow2 disk image from Dell support website. Rename the file to `dell-sonic-[version].qcow2` and run `make`.

After typing `make`, a new image will appear named `vrnetlab/vr-dell_sonic:<version>`. Run `docker images` to confirm this.

## System requirements

- CPU: 2 cores
- RAM: 4GB
- DISK: ~3.2GB

## Configuration

SONiC nodes boot with a basic configuration by default, enabling SSH and basic management connectivity. All factory default configuration is retained.
Full startup configuration can be passed by mounting it under `/config/config_db.json`, this is done automatically by Containerlab. Only SONiC json config format is accepted. This fill will replace existing default config.

The startup config is copied into the VM over SSH. Before that, the launcher brings up the management interface over the serial console (address, link, default route) and, if `sshd` does not already listen on IPv4, adds `ListenAddress 0.0.0.0` in `/etc/ssh/sshd_config.d/vrnetlab-ipv4.conf`. It then waits until `eth0` is up, SSH listens on IPv4 and CONFIG_DB answers. These steps are idempotent and do not write to CONFIG_DB, so management settings in a supplied startup config are kept.

## Running tests

```
python3 -m unittest discover -s dell/dell_sonic/tests
```
