# Cisco SD-WAN Components

vrnetlab Docker images for Cisco SD-WAN controller components: vManage, vSmart, and vBond.

## Building

Download qcow2 images from Cisco and place them in this directory. Component type is auto-detected from filename:
- `*vmanage*.qcow2` → manager
- `*vsmart*.qcow2` → controller
- `*vbond*.qcow2` → validator

Build all components:
```bash
make
```

Or build individually:
```bash
make docker-image IMAGE=viptela_vmanage_20_16_1.qcow2
```

Generates images:
- `vrnetlab/cisco_sdwan-manager:20.16.1`
- `vrnetlab/cisco_sdwan-controller:20.16.1`
- `vrnetlab/cisco_sdwan-validator:20.16.1`

## Requirements

| Component | RAM | Disk |
|-----------|-----|------|
| vManage | 16 GB | 30 GB + 50 GB data disk |
| vSmart | 4 GB | 30 GB |
| vBond | 2 GB | 30 GB |

Default: 5 NICs (eth0-eth4)
- **eth0**: VPN 512 (management) - configured with vrnetlab management IP
- **eth1+**: VPN 0 (transport)

## Configuration

### Default
- Username: `admin`
- Password: `admin`
- Hostname: `sdwan-manager`, `sdwan-controller`, or `sdwan-validator`

### Custom Config

**Full cloud-init** at `/config/cloud-init.yaml`:
```bash
docker run -d --privileged \
  -v /path/to/cloud-init.yaml:/config/cloud-init.yaml \
  vrnetlab/cisco_sdwan-manager:20.16.1
```

**zCloud XML only** at `/config/zcloud.xml`:
```bash
docker run -d --privileged \
  -v /path/to/zcloud.xml:/config/zcloud.xml \
  vrnetlab/cisco_sdwan-manager:20.16.1
```

### Runtime Parameters

```bash
docker run -d --privileged vrnetlab/cisco_sdwan-manager:20.16.1 \
  --hostname my-vmanage \
  --username admin \
  --password MyPass123 \
  --nics 8
```

## Troubleshooting

View logs:
```bash
docker logs <container-name>
```

Serial console:
```bash
docker exec -it <container-name> telnet localhost 5000
```

SSH access:
```bash
docker exec -it <container-name> ssh admin@localhost
```

## Tested Versions

- Cisco SD-WAN 20.16.1
