# Nvidia Cumulus VX

Containerizes the Nvidia Cumulus VX KVM appliance using the
[srl-labs/vrnetlab](https://github.com/srl-labs/vrnetlab) framework.
Cumulus VX is a virtual network switch running Cumulus Linux — a Debian-based
NOS with full L2/L3 switching, routing, and NVUE declarative management.

## Requirements

| Resource   | Minimum | Recommended  |
|------------|---------|--------------|
| RAM        | 4 GB | 4 GB        |
| vCPU       | 2      | 2            |
| Disk       | ~200 MB overlay, grows with config | —            |

## How to obtain the image

NVIDIA does not provide the Cumulus VX qcow2 image anymore. You will have to find it...

The expected filename pattern is:
`cumulus-linux-<version>-vx-amd64-qemu.qcow2`

## Build instructions

```bash
# 1. Place the qcow2 in the vrnetlab/nvidia/cumulus-vx/ directory
cp /path/to/cumulus-linux-5.16.1-vx-amd64-qemu.qcow2 vrnetlab/nvidia/cumulus-vx/

# 2. Build
cd vrnetlab/nvidia/cumulus-vx/
make

# Resulting image tag: vrnetlab/nvidia_cumulus-vx:5.16.1

# 3. (Optional) push to a private registry
DOCKER_REGISTRY=myregistry.example.com:5000/vrnetlab make docker-push
```

## Test version extraction before building

```bash
make version-test IMAGE=cumulus-linux-5.16.1-vx-amd64-qemu.qcow2
# Expected output: 5.16.1
```

## Debugging

**Container logs**:

```bash
docker logs -f clab-cumulus-lab-cumulus1
```

**Health status**:

```bash
docker inspect --format='{{.State.Health.Status}}' clab-cumulus-lab-cumulus1
# Expected: starting → (2–4 min) → healthy
```

**Verify persistent overlay is being used**:

```bash
docker exec clab-cumulus-lab-cumulus1 \
    cat /proc/$(docker exec clab-cumulus-lab-cumulus1 pgrep qemu)/cmdline \
    | tr '\0' '\n' | grep overlay
# Expected: if=ide,file=/config/cumulus_overlay.qcow2
```

## Known issues and limitations

- **KVM acceleration recommended**: Cumulus VX can run without KVM but switchd
  performance degrades significantly under software emulation.
- **Nested virtualisation**: Cumulus VX requires `/dev/kvm` for hardware
  acceleration. It cannot achieve line-rate switching inside a VM that does
  not expose KVM to guest workloads.
- **NVUE is the default CLI** in Cumulus Linux 5.x+. The legacy NCLU commands
  (`net add`, `net commit`) are still available but deprecated.

## Contact

The author of this code is Wei Luo (<olaf.luo@foxmail.com>), feel free to reach him in case of problems.
