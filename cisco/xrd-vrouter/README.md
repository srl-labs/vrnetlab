# Cisco XRd vRouter

This is the vrnetlab integration for the **vRouter** form factor of Cisco XRd — the variant with the high-performance DPDK dataplane.

Unlike the XRd Control Plane (which containerlab runs directly as `cisco_xrd`), XRd vRouter's dataplane interfaces are **PCI-only**; it cannot bind a veth. This integration solves that by running the XRd vRouter container inside a small KVM guest that presents emulated PCI NICs, so it can be wired with ordinary containerlab links like any other VM-based node.

## Building the image

XRd vRouter is distributed as a container image. Export it to a tarball named `xrd-vrouter-<version>.tar`, place it in this directory, and run `make`:

```bash
docker pull <your-registry>/xrd-vrouter:25.4.2
docker save <your-registry>/xrd-vrouter:25.4.2 -o xrd-vrouter-25.4.2.tar
make
```

`make` builds a fully self-contained image: it provisions a fresh Ubuntu cloud image (cgroups v1, 1 GiB hugepages, IOMMU + vfio-pci, Docker, the AppArmor profile and the guest-init service) and preloads the XRd vRouter image into it, producing `vrnetlab/cisco_xrd-vrouter:<version>`.

The build runs a short throwaway VM, so the build host needs nested virtualization (`/dev/kvm`).

## System requirements

XRd vRouter requires, per running node: 2 vCPUs (one dedicated to the dataplane), ~5 GiB RAM and 3 GiB of 1 GiB hugepages. The launcher defaults to 4 vCPU / 10 GiB and enforces a hard floor of 8 GiB; tune with the `VCPU` / `RAM` node env in your topology. 8 GiB is feasible — in our testing an idle node booted and forwarded with ~800 MB RAM to spare — but that's tight, so give it more for feature-heavy labs.

## Usage with containerlab

XRd vRouter is IOS-XR, so it runs under the stock [`cisco_xrv9k`](https://containerlab.srlinux.dev/manual/kinds/vr-xrv9k/) kind. A dedicated [`cisco_xrd_vrouter`](https://containerlab.srlinux.dev/manual/kinds/cisco_xrd_vrouter/) kind is also available.

```yaml
name: xrd
topology:
  nodes:
    r1:
      kind: cisco_xrv9k
      image: vrnetlab/cisco_xrd-vrouter:25.4.2
      startup-config: r1.cfg
    r2:
      kind: cisco_xrv9k
      image: vrnetlab/cisco_xrd-vrouter:25.4.2
      startup-config: r2.cfg
  links:
    - endpoints: ["r1:eth1", "r2:eth1"]
```

Management (SSH, gNMI on `:9339`, NETCONF on `:830`) uses the default credentials `clab` / `clab@123`. The router's `MgmtEth0/RP0/CPU0/0` carries the containerlab node IP. Data-plane interface config comes from each node's `startup-config`; `eth1`, `eth2`, … map to `GigabitEthernet0/0/0/0`, `…/1`, … in order.

> Emulated NIC throughput is suitable for feature, protocol and dataplane-behaviour labs, not line-rate performance testing.
