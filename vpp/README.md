# VPP Debian/Ubuntu VM

To download a compatible image of the Ubuntu VM execute the [download.sh](download.sh) script that will download a cloud-init image of IPng VPP from <https://ipng.ch/media/vpp-proto/vpp-proto-bookworm.qcow2.lrz>. The version is set in the script and can be changed manually.

Note: if the image is not there, please view: https://ipng.ch/media/vpp-proto/ to get the latest one.

Once the qcow2 image is downloaded, build the container with the following command:

```bash
make
```

The resulting container will be tagged as `vrnetlab/vr-ubuntu:<version>`, e.g. `vrnetlab/vr-ubuntu:vpp-proto-bookworm.qcow2`.

## Host requirements

* 1 vCPU, 512 MB RAM

## Configuration

Initial config is carried out via cloud-init.

* `9.9.9.9` configured as the DNS resolver. Change it with `resolvectl` if required.
