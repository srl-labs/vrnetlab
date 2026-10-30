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

The startup config is copied into the VM over SSH. Before that, the launcher brings up the management interface over the serial console (address, link, default route) and, if `sshd` does not already listen on IPv4, adds `ListenAddress 0.0.0.0` in `/etc/ssh/sshd_config.d/vrnetlab-ipv4.conf`. It then waits until `eth0` is up, SSH listens on IPv4, and `config-setup` has finished with `CONFIG_DB_INITIALIZED` set, so `config load` does not race the boot-time CONFIG_DB reload. These steps are idempotent and do not write to CONFIG_DB, so management settings in a supplied startup config are kept.

### Hostname

The hostname is chosen as follows:

1. If the startup config sets `DEVICE_METADATA.localhost.hostname`, that hostname is used. It is never replaced by the node name.
2. Otherwise the Containerlab node name (`--hostname`) is used. A startup config without a hostname keeps it, because `config load` merges `DEVICE_METADATA` fields.

If the chosen name is not a valid SONiC hostname, or the startup config cannot be read, the launcher logs a warning. An invalid hostname is not applied. An unreadable startup config falls back to the node name, and loading it then fails as usual.

After the readiness wait, which also waits for the boot-time `hostname-config` unit to finish, the launcher writes the hostname to CONFIG_DB (`DEVICE_METADATA|localhost`). If `hostcfgd` is running, it applies the change itself. Otherwise the launcher applies it to `/etc/hostname`, the kernel hostname, and a `127.0.0.1 <hostname>` entry in `/etc/hosts`. These are the same steps SONiC's own `hostname-config` and `hostcfgd` use, and running after them avoids a duplicate `/etc/hosts` entry. Other `/etc/hosts` entries are kept, and repeated runs do not add duplicate entries. If `/etc/sonic/config_db.json` already exists (older images), its hostname is updated too. The file is not created when it is absent; a supplied startup config is saved to it by the loader.

This is needed because on the SONiC 4.6 VM `hostname-config` runs before the startup config is loaded and `hostcfgd` does not run, so loading a new hostname into CONFIG_DB would not change the live hostname. Setting only the live hostname (for example with `hostnamectl`) is not enough either, because SONiC re-applies the CONFIG_DB hostname.

## Running tests

```
python3 -m unittest discover -s dell/dell_sonic/tests
```
