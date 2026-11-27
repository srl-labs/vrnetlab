# vrnetlab / Extreme-EXOS (exos)

This is the vrnetlab docker image for Extreme EXOS.

## Building the docker image

Select and download the QCOW2 image from [Extreme Networks github page](https://github.com/extremenetworks/Virtual_EXOS?tab=readme-ov-file#qcow2-files-for-gns3), or if you know the version you want you can directly use this:

```bash
curl -O https://akamai-ep.extremenetworks.com/Extreme_P/github-en/Virtual_EXOS/EXOS-VM_32.7.2.19.qcow2
```

Place the QCOW2 image into this folder, then run:

```bash
make
```

The image will be tagged based on the version in the filename (e.g., `vrnetlab/extreme_exos:32.7.2.19`).

## Running on an AMD CPU

EXOS does not boot on a host with an AMD CPU with the default `-cpu host`. The console shows `Warning. Could not determine the CPU Family.` and the VM stops at the EXOS developer menu, so the bootstrap never completes.

Workaround: set the `QEMU_CPU` environment variable to `core2duo`, e.g. in a containerlab topology:

```yaml
    env:
      QEMU_CPU: core2duo
```

## SSH host key algorithm

The bootstrap sets `configure ssh2 key algorithm rsa-sha2-512` before enabling SSH, so the host key is signed with SHA-2 instead of `ssh-rsa` (SHA-1), which recent SSH clients reject. This command was added in EXOS 32.5; all images on the Extreme Networks github page are 32.6 or later.

## Tested versions

On an AMD EPYC host, 32.6.3.126, 32.7.2.19 and 33.6.1.14 boot with `QEMU_CPU=core2duo`, and both management passthrough and the startup-config bind work.
33.1.1.31 doesn't boot on AMD with any CPU setting tried.

| Image                       | Intel CPU | AMD CPU                          |
| --------------------------- | --------- | -------------------------------- |
| `EXOS-VM_v32.6.3.126.qcow2` | OK        | OK with `QEMU_CPU=core2duo`      |
| `EXOS-VM_32.7.2.19.qcow2`   | OK        | OK with `QEMU_CPU=core2duo`      |
| `EXOS-VM_33.1.1.31.qcow2`   | OK        | Not working, no setting found    |
| `EXOS-VM_33.6.1.14.qcow2`   | OK        | OK with `QEMU_CPU=core2duo`      |
