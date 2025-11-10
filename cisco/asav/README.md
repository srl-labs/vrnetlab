vrnetlab / Cisco ASAv
===========================
This is the vrnetlab docker image for Cisco ASAv.

Building the docker image
-------------------------
Put the .qcow2 file in this directory and run `make docker-image` and
you should be good to go. The resulting image is called `vr-asav`. You can tag
it with something else if you want, like `my-repo.example.com/vr-asav` and then
push it to your repo. The tag is the same as the version of the ASAv image, so
if you have asav9-23-1.qcow2 your final docker image will be called
vr-asav:9-23-1.

Please note that you will always need to specify version when starting your
router as the "latest" tag is not added to any images since it has no meaning
in this context.

It's been tested to boot and respond to SSH/telnet with:

 * 9.23.1 (asav9-23-1.qcow2)

Usage
-----
```
# Start a container with the ASAv image. This can take 5-10 minutes to boot
docker run -d --privileged --name my-asav-firewall vrnetlab/cisco_asav:9-23-1

# Get the docker container's IP address
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' my-asav-firewall

# Follow the boot process, including SSH configuration, this may take a while
docker logs -f my-asav-firewall

# After the ASAv has booted, SSH to it using the configured credentials
ssh admin@<docker-ip> # password: CiscoAsa1!

# Alternatively, you can connect to the console with telnet
telnet <docker-ip> 5000
```

Interface mapping
-----------------
Management0/0 is always configured as a management interface.

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

System requirements
-------------------
CPU: 1 core

RAM: 2GB

Disk: <500MB

FUAQ - Frequently or Unfrequently Asked Questions
-------------------------------------------------
##### Q: Has this been extensively tested?
A: Nope.
