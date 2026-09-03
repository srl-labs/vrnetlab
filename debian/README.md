# Debian VM

To download a compatible image of the Debian VM execute the [download.sh](download.sh) script that will download a cloud-init image of Debian from <https://cloud.debian.org/images/cloud>. The version is set in the script and can be changed manually.

Once the qcow2 image is downloaded, build the container with the following command:

```bash
make
```

The resulting container will be tagged as `vrnetlab/vr-debian:<version>`, e.g. `vrnetlab/vr-debian:bookworm`.

## Host requirements

* 1 vCPU, 512 MB RAM

## Configuration

Initial config is carried out via cloud-init.

* `9.9.9.9` configured as the DNS resolver. Change it with `resolvectl` if required.
