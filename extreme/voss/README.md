# vrnetlab / Extreme-VOSS (voss)

This is the vrnetlab docker image for Extreme VOSS.

## Building the docker image

Select and download the QCOW2 image from [Extreme Networks github page](https://github.com/extremenetworks/Virtual_VOSS#voss-image-files), or if you know the version you want you can directly use this:

```bash
curl -O https://akamai-ep.extremenetworks.com/Extreme_P/github-en/Virtual_VOSS/FEGNS3.9.3.1.0.qcow2
```

Place the QCOW2 image into this folder, then run:

```bash
make
```

The image will be tagged based on the version in the filename (e.g., `vrnetlab/extreme_voss:9.3.1.0`).

## Tested versions

- `VOSS-VM_v9.4.0.0.qcow2`
- `VOSS-VM_v9.3.1.0.qcow2`
- `VOSS-VM_v8.10.1.0.qcow2`
