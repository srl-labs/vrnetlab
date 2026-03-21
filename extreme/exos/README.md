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

## Tested versions

- `EXOS-VM_v32.6.3.126.qcow2`
- `EXOS-VM_32.7.2.19.qcow2`
- `EXOS-VM_33.1.1.31.qcow2`
