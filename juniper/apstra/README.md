# Juniper Apstra — vrnetlab / srl-labs container

## Build instructions

```bash
# 1. Place your qcow2 in the vrnetlab/juniper/apstra/ directory
cp /path/to/aos_server_6.1.1-70.qcow2 vrnetlab/juniper/apstra/

# 2. Build
cd vrnetlab/juniper/apstra/
make

# Resulting image tag: vrnetlab/juniper_apstra:6.1.1-70

# 3. (Optional) push to a private registry
DOCKER_REGISTRY=myregistry.example.com:5000/vrnetlab make docker-push
```

## Test version extraction before building

```bash
make version-test IMAGE=aos_server_6.1.1-70
# Expected output: 6.1.1-70
```

## Containerlab topology example

```yaml
# apstra-lab.clab.yaml
name: apstra-lab

mgmt:
  network: apstra-mgmt      # name of the Docker management network
  ipv4-subnet: 172.20.20.0/24
  ipv4-gw: 172.20.20.1

topology:
  nodes:
    apstra:
      kind: generic_vm
      image: vrnetlab/juniper_apstra:6.1.1-70
      mgmt-ipv4: 172.20.20.10
      binds:
        - clab-apstra-lab/apstra:/state   # INFO: node directory under lab folder must be created manually - used for persistent overlay image to preserve VM state across destroys 
      env:
        QEMU_MEMORY: "16384"   # 16 GB minimum; increase to 32768 for production
        QEMU_SMP: "4"          # vCPU count

      ports:
        - "22:22"     # SSH CLI access
        - "80:80"     # HTTP (redirects to HTTPS)
        - "443:443"   # Web UI + REST API
```

Deploy:
```bash
sudo containerlab deploy -t apstra-lab.clab.yaml
```

Access the Web UI at `https://<host-ip>` (or the management IP shown by
`sudo containerlab inspect -t apstra-lab.clab.yaml`).

## Debugging

**Serial console** (while container is running):
```bash
telnet localhost 5000
# or, using the container name:
docker exec -it clab-apstra-lab-apstra telnet localhost 5000
```

**Container logs** (boot progress from launch.py):
```bash
docker logs -f clab-apstra-lab-apstra
```

**Health status**:
```bash
docker inspect --format='{{.State.Health.Status}}' clab-apstra-lab-apstra
```
Expected progression: `starting` → (2-5 min) → `healthy`
