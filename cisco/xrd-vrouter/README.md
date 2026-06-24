# Cisco IOS XRd vRouter

This is the vrnetlab integration for **Cisco IOS XRd vRouter** — the high-performance DPDK-dataplane form factor of Cisco IOS XRd.

Unlike the XRd Control Plane (which containerlab runs directly as `cisco_xrd`), XRd vRouter's dataplane interfaces are **PCI-only**; it cannot bind a veth. This integration solves that by running the XRd vRouter container inside a small KVM guest (a micro-VM) that presents emulated PCI NICs, so it can be wired with ordinary containerlab links like any other VM-based node.

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

Per running node, XRd vRouter needs:

- **vCPU** — at least 4 cores; the launcher defaults to and floors at 4.
- **RAM** — ~5 GiB minimum; the launcher defaults to **10 GiB**, with a hard floor of **8 GiB**.
- **Hugepages** — 3 GiB of 1 GiB hugepages.

Tune vCPU/RAM with the `VCPU` / `RAM` node env. The 10 GiB default is comfortable; drop toward the 8 GiB floor only to fit more nodes on a host — at 8 GiB an idle node booted and forwarded with ~800 MB to spare, which leaves little headroom for feature-heavy configs.

Unlike the control-plane `cisco_xrd`, the elevated inotify limits XR needs are tuned inside the micro-VM, so no host inotify tuning is required.

## Usage with containerlab

Use the dedicated [`cisco_xrd_vrouter`](https://containerlab.srlinux.dev/manual/kinds/cisco_xrd_vrouter/) kind — it ships XRd vRouter's tuned defaults (4 vCPU / 10 GiB). The stock [`cisco_xrv9k`](https://containerlab.srlinux.dev/manual/kinds/vr-xrv9k/) kind is **also** compatible (XRd vRouter is IOS XR), but it defaults to XRv9k's heavier 2 vCPU / 16 GiB, so set `RAM` explicitly there.

```yaml
name: xrd
topology:
  nodes:
    r1:
      kind: cisco_xrd_vrouter
      image: vrnetlab/cisco_xrd-vrouter:25.4.2
      startup-config: r1.cfg
    r2:
      kind: cisco_xrd_vrouter
      image: vrnetlab/cisco_xrd-vrouter:25.4.2
      startup-config: r2.cfg
  links:
    - endpoints: ["r1:eth1", "r2:eth1"]
```

Management (SSH, gNMI on `:9339`, NETCONF on `:830`) uses the default credentials `clab` / `clab@123`. The router's `MgmtEth0/RP0/CPU0/0` carries the containerlab node IP. Dataplane interface config comes from each node's `startup-config`; `eth1`, `eth2`, … map to the data interfaces in order — `TenGigE0/0/0/0`, `…/1`, … by default (see [Dataplane NIC](#dataplane-nic) for the interface naming).

> Emulated NIC throughput is suitable for feature, protocol and dataplane-behaviour labs, not line-rate performance testing.

## Dataplane NIC

`XRD_NIC_TYPE` selects the emulated data-NIC model — both are on XRd vRouter's supported-PCI allowlist:

| `XRD_NIC_TYPE`        | XR interface              | Speed  | Notes                          |
| --------------------- | ------------------------- | ------ | ------------------------------ |
| `vmxnet3` *(default)* | `TenGigE0/0/0/X`          | 10 GbE | ~1.5× faster bulk forwarding   |
| `igb`                 | `GigabitEthernet0/0/0/X`  | 1 GbE  | alternative                    |

Either way `eth1`, `eth2`, … map to the data interfaces in order — just make sure your `startup-config` uses the matching interface names. `XRD_NIC_TYPE` affects only the data interfaces; the management NIC is always a lightweight paravirtual virtio-net.

```yaml
    r1:
      kind: cisco_xrd_vrouter
      image: vrnetlab/cisco_xrd-vrouter:25.4.2
      env:
        XRD_NIC_TYPE: igb   # optional; default is vmxnet3
      startup-config: r1.cfg   # with igb, interface names must be GigabitEthernet0/0/0/X
```

## Troubleshooting

**Bulk TCP between a Linux host and the node stalls, while ping and UDP work.** The emulated host↔node datapath can silently drop full-size (1500-byte) frames in a sustained TCP flow even though equally large single packets pass — a path-MTU black hole in the emulated datapath, not an XRd forwarding issue. Lower the MTU on the Linux endpoints (`ip link set eth1 mtu 1400`) or clamp TCP MSS; the node itself needs no change.
