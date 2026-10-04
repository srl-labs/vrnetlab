# vrnetlab / Genua genuscreen

This is the vrnetlab docker image for Genua genuscreen firewall appliances.

## Building the docker image

Download the genuscreen ISO image and place it in this directory. The expected naming format is:
- `genuscreen-8.0.iso`
- `genuscreen-1.0.0.iso`
- `genuscreen-2.1.3.iso`

After placing the ISO file, run `make` to build the docker image. The resulting image will be called `vrnetlab/genua_genuscreen:X.Y.Z` where X.Y.Z matches the version from the ISO filename.

## Installation Process

Genuscreen requires an initial installation from ISO to create the qcow2 disk image. The build process automatically handles this:

1. The ISO is mounted as a CD-ROM device
2. An empty 20GB qcow2 disk is created for installation
3. The automated installation configures:
   - Hostname (from `--hostname` parameter)
   - Network interface (eth0 with IP 10.0.0.15/24)
   - Default gateway (10.0.0.2)
   - Administrative password (from `--password` parameter)
   - SSH daemon (enabled)
   - Web-GUI access restrictions
   - Admin ACL network settings

## Usage

### With containerlab

```yaml
name: genuscreen-lab
topology:
  nodes:
    gw1:
      kind: vr-genuscreen
      image: vrnetlab/genua_genuscreen:8.0
```

### Manual docker run

```bash
docker run -d --privileged --name my-genuscreen vrnetlab/genua_genuscreen:8.0
```

## Configuration

The genuscreen image supports the following parameters:

- `--hostname`: Router hostname (default: `vr-genuscreen`)
- `--username`: Login username (default: `root`)
- `--password`: Login password (default: `VR-netlab9`)
- `--connection-mode`: Datapath connection mode (default: `tc`)
- `--install`: Run installation mode (used during build process)
- `--trace`: Enable trace level logging

## Interface mapping

The genuscreen VM exposes 8 network interfaces using virtio-net-pci:
- eth0: Management interface (configured during installation)
- eth1-eth7: Additional data interfaces

## Network Configuration

During installation, the system is configured with:
- **Management IP**: 10.0.0.15/24
- **Default Gateway**: 10.0.0.2
- **DNS**: Default system DNS
- **SSH**: Enabled on port 22
- **Admin ACL**: 192.168.1.0/24 (Only when web-gui restrictions is set true)

## Tested versions

The image has been developed and tested with:
- genuscreen-8.0.iso

## Troubleshooting

### Installation Issues

If installation fails:
1. Check that the ISO file is properly named and in the correct directory
2. Ensure sufficient disk space (>25GB)
3. Verify the ISO image is not corrupted
4. Enable trace logging with `--trace` for detailed output

### SSH Access

Default SSH credentials:
- Username: `root`
- Password: `VR-netlab9`
- Port: 22

## License

This vrnetlab image is provided under the same license terms as the main vrnetlab project. The actual genuscreen software requires appropriate licensing from [Genua GmbH](https://www.genua.de/).
