# vrnetlab / Cisco ASAv

This is the vrnetlab docker image for Cisco ASAv.

## Building the docker image

Put the .qcow2 file in this directory and run `make` or `make docker-image` and
you should be good to go. The resulting image is called `vrnetlab/cisco_asav:9-24-1`.
You can tag it with something else if you want, like `my-repo.example.com/vr-asav` and
then push it to your repo. The tag is the same as the version of the ASAv image, so
if you have asav9-23-1.qcow2 your final docker image will be called
vrnetlab/cisco_asav:9-24-1.

Please note that you will always need to specify version when starting your
router as the "latest" tag is not added to any images since it has no meaning
in this context.

It's been tested to boot and respond to SSH/telnet with:

- 9.24.1 (asav9-24-1.qcow2) — the ASAv in the CML refplat 2.10
- 9.23.1 (asav9-23-1.qcow2) - not tested in the last PR (2026-09)
- 9.18.1 (asav9-18-1.qcow2)
- 9.9.2 (asav9-9-2.qcow2)

Bootstrap prompts vary between releases, so the launcher answers the ones it is
given rather than a fixed sequence.

## Usage

```sh
# Start a container with the ASAv image. This can take 5-10 minutes to boot
docker run -d --privileged --name my-asav-firewall vrnetlab/cisco_asav:9-24-1

# Get the docker container's IP address
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' my-asav-firewall

# Follow the boot process, including SSH configuration, this may take a while
docker logs -f my-asav-firewall

# After the ASAv has booted, SSH to it using the configured credentials
ssh admin@<docker-ip> # password: CiscoAsa1!

# Alternatively, you can connect to the console with telnet
telnet <docker-ip> 5000
```

## Forced password change on first login

Recent releases (9.18.1 and 9.24.1 here, not 9.9.2) mark a user whose password was
set by an administrator as "New-User" and force the first authenticated login to
change it. Nothing in the configuration clears it and it cannot be turned off — see
[this thread](https://community.cisco.com/t5/network-security/asa-9-19-forced-password-changes/td-p/4833624).

Only authenticated logins get the dialog, so the launcher briefly authenticates the
console, answers it with the same password, and removes the console authentication
again. You get a device you can log straight in to.

## Interface mapping

Management0/0 is always configured as a management interface, and the table below maps
the container's data interfaces (`eth1`, `eth2`, ...) to the ASAv ones.

It is the full mapping, not what every node gets: a NIC is only created for a container
interface that exists, so a node with two links has Management0/0, GigabitEthernet0/0 and
GigabitEthernet0/1 and nothing else.

| vr-asav             | vr-xcon |
| :---:               |  :---:  |
| Management0/0       | 0       |
| GigabitEthernet0/0  | 1       |
| GigabitEthernet0/1  | 2       |
| GigabitEthernet0/2  | 3       |
| GigabitEthernet0/3  | 4       |
| GigabitEthernet0/4  | 5       |
| GigabitEthernet0/5  | 6       |
| GigabitEthernet0/6  | 7       |
| GigabitEthernet0/7  | 8       |

## System requirements

CPU: 1 core

RAM: 2GB

Disk: <500MB

## FUAQ - Frequently or Unfrequently Asked Questions

### Q: Has this been extensively tested?

A: Nope. Not really, in September 2026, the version available in CML: 9.24.1 was tested successfully. Same for 9.18.1.
9.9.2 was briefly tested, i.e. the container is healthy, but no further tests were performed.
