# f5_bigip / F5 BIG-IP VE

vrnetlab image for F5 BIG-IP VE.

## Building the docker image

1. Download BIG-IP VE in KVM/qcow2 format from F5 (requires login/access).
2. Place the qcow2 in this directory (e.g. `BIGIP-17.5.1.3-0.0.19.qcow2`).
3. Run `make`. The image will be tagged `vrnetlab/f5_bigip-ve:<version>` (or `DOCKER_REGISTRY/…` if set).

## Runtime/Initial Boot details

- Management IP/route automation:
  - Primary: cloud-init seeds `cidata.iso` with the container’s `eth0` IP/gateway in passthrough mode and disables mgmt DHCP.
  - Fallback: if a forced password change blocks cloud-init, console automation waits for `mcpd` to be running, applies mgmt IP + default route, and saves config.
- Password automation:
  - Defaults: `PASSWORD` (admin, default `admin`), `ROOT_PASSWORD` (root, default `default`).
  - If a forced password change is required, console automation sets both root and admin to `F5_NEW_PASSWORD` (default `Labl@b!234`) and saves config.
  - Admin is explicitly aligned to the chosen password so GUI login does not prompt for a change.
  - Default interfaces (1 MGMT/3 Dataplane)


## Tested image

- BIGIP-17.5.1.3-0.0.19.qcow2

## System requirements

- CPU: 4 vCPU
- RAM: 8 GB
- Disk: space for the qcow2 + overlay (<10 GB typical)
