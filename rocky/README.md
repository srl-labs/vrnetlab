# Rocky Linux VM

# Introduction

This was heavily inspired and copied from vrnetlab/ubuntu. Hence launch.py still contains ubuntu "references".
The nice thing with that is that the behaviour is somewhat the same between Rocky Linux and Ubuntu.

To download a compatible image of the Rocky Linux VM execute the [download.sh](download.sh) script that will download a cloud-init image of Rocky Linux from a mirror. The version is set in the script and can be changed manually.

Once the qcow2 image is downloaded, build the container with the following command:

```bash
make
```

The resulting container will be tagged as `vrnetlab/rocky_rocky:<version>`, e.g. `vrnetlab/rocky_rocky:9`.

## Host requirements

* 1 vCPU, 2048 MB RAM

## Configuration

Initial config is carried out via cloud-init.

* `9.9.9.9` configured as the DNS resolver. Change it with `resolvectl` if required.
