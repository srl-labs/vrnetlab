# vrnetlab / Extreme-EXOS (exos)

This is the vrnetlab docker image for Extreme EXOS.

## Building the docker image

Download the QCOW2 image from Extreme Networks:

```bash
curl -O https://akamai-ep.extremenetworks.com/Extreme_P/github-en/Virtual_EXOS/EXOS-VM_v32.7.3.126.qcow2
```

Place the QCOW2 image into this folder, then run:

```bash
make
```

The image will be tagged based on the version in the filename (e.g., `vrnetlab/extreme_exos:v32.6.3.126`).

## Tested versions

- `EXOS-VM_v32.6.3.126.qcow2`
- `EXOS-VM_32.7.2.19.qcow2`
- `EXOS-VM_v33.1.1.31.qcow2` - this image seems to take a long time to boot.
