# f5_bigip / F5 BIG-IP VE

vrnetlab image for F5 BIG-IP VE.

## Building the docker image

1. Download BIG-IP VE in KVM/qcow2 format from F5 (requires login/access).
2. Place the qcow2 in this directory (e.g. `BIGIP-17.5.1.3-0.0.19.qcow2`).
3. Run `make`. The image will be tagged `vrnetlab/f5_bigip-ve:<version>` (or `DOCKER_REGISTRY/…` if set).


## Tested image

- BIGIP-17.5.1.3-0.0.19.qcow2

## System requirements

- CPU: 4 vCPU
- RAM: 8 GB
- Disk: space for the qcow2 + overlay (<10 GB typical)
