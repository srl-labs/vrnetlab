# Nvidia Cumulus VX — vrnetlab / srl-labs container

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

Download the Cumulus VX qcow2 image from the
[Nvidia developer portal](https://developer.nvidia.com/networking).
An Nvidia developer account is required.

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

## Containerlab topology

```yaml
# cumulus-lab.clab.yaml
name: cumulus-lab

mgmt:
  network: cumulus-mgmt
  ipv4-subnet: 172.20.20.0/24
  ipv4-gw: 172.20.20.1

topology:
  nodes:

    cumulus1:
      kind: cumulus_vm
      image: vrnetlab/nvidia_cumulus-vx:5.16.1
      mgmt-ipv4: 172.20.20.10
      env:
        QEMU_MEMORY: "4096"
        QEMU_SMP: "2"

    cumulus2:
      kind: cumulus_vm
      image: vrnetlab/nvidia_cumulus-vx:5.16.1
      mgmt-ipv4: 172.20.20.11
      env:
        QEMU_MEMORY: "4096"
        QEMU_SMP: "2"

  links:
    - endpoints: ["cumulus1:swp1", "cumulus2:swp1"]
    - endpoints: ["cumulus1:swp2", "cumulus2:swp2"]
```

Deploy:
```bash
sudo containerlab deploy -t cumulus-lab.clab.yaml
```

## Default credentials

| Service  | Username | Password       |
|----------|----------|----------------|
| SSH      | cumulus  | Nsn1234!       |
| NVUE API | cumulus  | Nsn1234!       |

## Management interfaces

### NVUE REST API (Cumulus Linux 5.x+)

NVUE is the primary declarative management interface for Cumulus Linux 5.x.
It exposes an OpenAPI-compatible REST API on port 8765 (HTTPS).

```bash
# Check NVUE health
curl -k -u cumulus:CumulusLinux! https://172.20.20.10:8765/nvue_v1/

# Apply configuration
curl -k -u cumulus:CumulusLinux! -X PATCH \
  https://172.20.20.10:8765/nvue_v1/ \
  -H "Content-Type: application/json" \
  -d '{
    "interface": {
      "swp1": {
        "type": "swp",
        "link": {
          "mtu": 9216
        }
      }
    }
  }'
```

### SSH

```bash
ssh cumulus@<mgmt-ip>
```

### Serial console

```bash
# from the container host
telnet <container-name> 5000

# or from inside the container
docker exec -it <container-name> bash
telnet localhost 5000
```

## Interface naming

Inside the VM, Cumulus VX presents data-plane interfaces as `swp1`, `swp2`, etc.
These map to the containerlab links defined in the topology:

| Containerlab link | VM interface inside Cumulus VX |
|-------------------|-------------------------------|
| `eth1`            | `swp1`                        |
| `eth2`            | `swp2`                        |
| `ethN`            | `swpN`                        |

The mapping is 1:1 — containerlab `ethN` maps to Cumulus `swpN`.

## Persistent VM state

The `generic_vm` kind automatically bind-mounts
`clab-<labname>/<nodename>/config/` to `/config` inside the container.
`launch.py` detects this mount and creates the QEMU overlay disk there:

```
clab-cumulus-lab/
└── cumulus1/
    └── config/
        └── cumulus_overlay.qcow2   ← all VM writes go here
```

All Cumulus VX configuration — including NVUE settings, interface config,
and installed packages — survives `clab destroy` and `clab deploy` cycles.

To start completely fresh, delete the overlay before redeploying:

```bash
rm clab-cumulus-lab/cumulus1/config/cumulus_overlay.qcow2
sudo containerlab deploy -t cumulus-lab.clab.yaml
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
