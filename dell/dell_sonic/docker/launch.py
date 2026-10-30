#!/usr/bin/env python3

import datetime
import json
import logging
import os
import re
import signal
import subprocess
import sys

import vrnetlab

CONFIG_FILE = "/config/config_db.json"
DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "YourPaSsWoRd"
SSHD_DROPIN = "/etc/ssh/sshd_config.d/vrnetlab-ipv4.conf"
MGMT_READY_TIMEOUT = 300
# Bound on each console prompt wait after the readiness check.
PROMPT_TIMEOUT = 120
# Bound on waiting for hostcfgd to apply a CONFIG_DB hostname change.
HOSTCFGD_TIMEOUT = 20
# Bound on the shell prompt or "Login incorrect" after sending a password.
LOGIN_TIMEOUT = 60


def sshd_ipv4_listener_cmd(dropin=SSHD_DROPIN):
    """Add an IPv4 sshd listener via a drop-in, only if sshd lacks one.

    The wrapper forwards IPv4 port 22 to the guest. SONiC 4.6 can boot with
    sshd listening only on [::] (V6ONLY); older images already listen on IPv4
    and are left untouched.
    """
    return (
        "sshd -T 2>/dev/null | grep -q '^listenaddress 0\\.0\\.0\\.0:' || "
        f"{{ echo 'ListenAddress 0.0.0.0' > {dropin} && "
        "systemctl try-reload-or-restart ssh; }"
    )


def mgmt_ready_cmd(timeout=MGMT_READY_TIMEOUT):
    """Poll until eth0 is up, sshd listens on IPv4 and CONFIG_DB is initialized.

    The console login can appear while config-setup is still reloading
    CONFIG_DB; "config load" then fails its PORT table check. Wait for the
    config-setup oneshot to finish and CONFIG_DB_INITIALIZED, the flag SONiC's
    own swss/syncd start scripts wait on. Also wait for the boot-time
    hostname-config oneshot, which otherwise races the hostname step.
    The printf arguments keep the echoed command from matching the marker.
    """
    checks = (
        'ip link show eth0 | grep -q "state UP"'
        ' && ss -Hltn "sport = :22" | grep -q "0.0.0.0:22"'
        " && systemctl is-active --quiet config-setup"
        ' && [ "$(sonic-db-cli CONFIG_DB GET CONFIG_DB_INITIALIZED)" = 1 ]'
        " && ! systemctl list-jobs --no-legend | grep -q hostname-config"
        ' && [ "$(systemctl show -p ActiveState --value hostname-config)" != activating ]'
    )
    return (
        f"timeout {timeout} sh -c 'until {checks}; do sleep 1; done'"
        " && printf '%s_%s\\n' MGMT READY || printf '%s_%s\\n' MGMT TIMEOUT"
    )


def valid_hostname(hostname):
    """SONiC's own hostname rule (config hostname/hostcfgd).

    Also guarantees the name is safe to embed in a shell command.
    """
    if not isinstance(hostname, str) or not hostname or len(hostname) > 253:
        return False
    label = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")
    return all(label.fullmatch(x) for x in hostname.split("."))


def hostname_cmd(
    hostname,
    hostname_file="/etc/hostname",
    hosts_file="/etc/hosts",
    config_db="/etc/sonic/config_db.json",
    timeout=HOSTCFGD_TIMEOUT,
):
    """Set the hostname in CONFIG_DB and apply it to the live system.

    Mirrors SONiC's hostname-config.sh/hostcfgd (/etc/hostname, hostname -F,
    a "127.0.0.1 <name>" hosts entry). hostname-config runs once at boot (the
    readiness wait orders us after it) and hostcfgd does not run on the 4.6
    VM. Where hostcfgd runs, it applies the CONFIG_DB change only if it has
    subscribed and its cached hostname differs; a write that lands while it
    initializes is cached and never applied. So wait (bounded) for hostcfgd
    to apply it, and apply the live steps only if it did not, so its own
    hosts entry is not duplicated. Writing CONFIG_DB first means SONiC agrees
    with the live hostname instead of resetting it later. The saved config is
    only updated if it exists; it is not created here.
    """
    if not valid_hostname(hostname):
        raise ValueError(f"invalid hostname {hostname!r}")
    tmp = f"{hosts_file}.vrnetlab"
    persist = (
        'python3 -c "import json,os,shutil,sys;p,h=sys.argv[1:];'
        "d=json.load(open(p));"
        "d.setdefault('DEVICE_METADATA',{}).setdefault('localhost',{})['hostname']=h;"
        "t=p+'.vrnetlab';json.dump(d,open(t,'w'),indent=4);"
        'shutil.copymode(p,t);os.replace(t,p)"'
    )
    return (
        f'H={hostname}; O="$(hostname)"; A=; '
        "sonic-db-cli CONFIG_DB HSET 'DEVICE_METADATA|localhost' hostname \"$H\" >/dev/null; "
        "if systemctl is-active --quiet hostcfgd; then i=0; "
        f'until [ "$(hostname)" = "$H" ] || [ $i -ge {timeout} ]; do '
        "sleep 1; i=$((i+1)); done; "
        '[ "$(hostname)" = "$H" ] && A=1; fi; '
        "if [ -n \"$A\" ]; then echo 'hostcfgd applied the hostname'; else "
        f'echo "$H" > {hostname_file} && hostname -F {hostname_file}; '
        'awk -v o="$O" -v h="$H" \'!($1 == "127.0.0.1" && NF == 2 && '
        '($2 == h || ($2 == o && o != "localhost")))\' '
        f'{hosts_file} > {tmp} && echo "127.0.0.1 $H" >> {tmp} && '
        f"cat {tmp} > {hosts_file}; rm -f {tmp}; fi; "
        f'if [ -f {config_db} ]; then {persist} {config_db} "$H"; '
        f"else echo '{config_db} absent, hostname not persisted to it'; fi"
    )


def handle_SIGCHLD(_signal, _frame):
    os.waitpid(-1, os.WNOHANG)


def handle_SIGTERM(_signal, _frame):
    sys.exit(0)


signal.signal(signal.SIGINT, handle_SIGTERM)
signal.signal(signal.SIGTERM, handle_SIGTERM)
signal.signal(signal.SIGCHLD, handle_SIGCHLD)

TRACE_LEVEL_NUM = 9
logging.addLevelName(TRACE_LEVEL_NUM, "TRACE")


def trace(self, message, *args, **kws):
    # Yes, logger takes its '*args' as 'args'.
    if self.isEnabledFor(TRACE_LEVEL_NUM):
        self._log(TRACE_LEVEL_NUM, message, args, **kws)


logging.Logger.trace = trace


class Dell_Sonic_VM(vrnetlab.VM):
    def __init__(self, hostname, username, password, conn_mode):
        disk_image = vrnetlab.find_disk_image()
        super(Dell_Sonic_VM, self).__init__(
            username, password, disk_image=disk_image, ram=4096, smp="2"
        )
        self.nic_type = "virtio-net-pci"
        self.conn_mode = conn_mode
        self.num_nics = 10
        self.hostname = hostname

    def bootstrap_spin(self):
        """This function should be called periodically to do work."""

        if self.spins > 300:
            # too many spins with no result ->  give up
            self.stop()
            self.start()
            return

        ridx, match, res = self.tn.expect([b"login:"], 1)
        if match and ridx == 0:  # login
            self.logger.info("VM started")

            self.console_login()

            # run main config!
            self.bootstrap_config()
            self.startup_config()
            # close telnet connection
            self.tn.close()
            # startup time?
            startup_time = datetime.datetime.now() - self.start_time
            self.logger.info(f"Startup complete in: {startup_time}")
            # mark as running
            self.running = True
            return

        # no match, if we saw some output from the router it's probably
        # booting, so let's give it some more time
        if res != b"":
            self.logger.trace("OUTPUT: %s" % res.decode())
            # reset spins if we saw some output
            self.spins = 0

        self.spins += 1

        return

    def console_login(self):
        """Log in on the serial console.

        A fresh disk has the factory password. A disk reused across a
        container restart already has the configured password, so retry
        with it after "Login incorrect". Exit if neither works.
        """
        passwords = [DEFAULT_PASSWORD]
        if self.password != DEFAULT_PASSWORD:
            passwords.append(self.password)
        prompt = f"{DEFAULT_USER}@".encode()
        self.wait_write("\r", None)
        self.wait_write(DEFAULT_USER, wait="login:")
        for i, password in enumerate(passwords):
            if i:
                self.logger.info("Console login incorrect, trying the next password")
                # Type nothing ahead: wait for the guest's own login prompt.
                if b"login:" not in rest:
                    rest = self.tn.read_until(b"login:", LOGIN_TIMEOUT)
                    if b"login:" not in rest:
                        break
                self.wait_write(DEFAULT_USER, wait=None)
            self.wait_write(password, wait="Password:")
            ridx, match, buf = self.tn.expect(
                [prompt, b"Login incorrect"], LOGIN_TIMEOUT
            )
            if match and ridx == 0:
                self.wait_write("", None)
                self.logger.info("Login completed")
                return
            if not match:
                break
            rest = buf.decode(errors="ignore")[match.end() :].encode()
        self.logger.error("Console login failed, aborting startup")
        sys.exit(1)

    def bootstrap_config(self):
        """Do the actual bootstrap config"""
        self.logger.info("applying bootstrap configuration")
        self.wait_write("sudo -i", "$")

        self.bootstrap_mgmt()
        self.wait_write("passwd -q %s" % (self.username))
        self.wait_write(self.password, "New password:")
        self.wait_write(self.password, "password:")
        self.wait_write("sleep 1", "#")
        self.wait_mgmt_ready()
        self.bootstrap_hostname()
        self.prompt_write("logout")
        self.logger.info("completed bootstrap configuration")

    def bootstrap_mgmt(self):
        """Bring up management (address, link, default route, IPv4 sshd).

        SONiC's own path (interfaces-config/hostcfgd) may not have configured
        eth0 by the time the console login appears, so apply the wrapper's
        management settings directly. Nothing is written to CONFIG_DB, so a
        startup config loaded later keeps its own MGMT_* settings.
        """
        if self.mgmt_address_ipv4 != "dhcp":
            self.wait_write(
                f"ip address replace {self.mgmt_address_ipv4} dev eth0", "#"
            )
        if self.mgmt_address_ipv6 and self.mgmt_address_ipv6 != "dhcp":
            # note, v6 address is not being applied for whatever reason
            self.wait_write(
                f"ip -6 address replace {self.mgmt_address_ipv6} dev eth0", "#"
            )
        self.wait_write("ip link set eth0 up", "#")
        if self.mgmt_gw_ipv4 and self.mgmt_gw_ipv4 != "dhcp":
            self.wait_write(
                f"ip route replace default via {self.mgmt_gw_ipv4} dev eth0", "#"
            )
        if self.mgmt_gw_ipv6 and self.mgmt_gw_ipv6 != "dhcp":
            self.wait_write(
                f"ip -6 route replace default via {self.mgmt_gw_ipv6} dev eth0", "#"
            )
        self.wait_write(sshd_ipv4_listener_cmd(), "#")

    def wait_mgmt_ready(self):
        """Wait for the guest state the SSH-based startup loader depends on."""
        self.logger.info(
            "waiting for management interface, IPv4 SSH and CONFIG_DB initialization"
        )
        self.wait_write(mgmt_ready_cmd(), "#")
        ridx, match, buf = self.tn.expect(
            [b"MGMT_READY", b"MGMT_TIMEOUT"], MGMT_READY_TIMEOUT + 30
        )
        # expect() drops the rest of the chunk it matched in. If the shell
        # prompt arrived in that chunk it is gone; ask for a fresh one so the
        # next prompt wait sees exactly one prompt either way.
        if match and "#" in buf.decode(errors="ignore")[match.end() :]:
            self.wait_write("", wait=None)
        if match and ridx == 0:
            self.logger.info("management interface, IPv4 SSH and CONFIG_DB are ready")
        else:
            self.logger.warning(
                "management interface, IPv4 SSH or CONFIG_DB not ready after "
                f"{MGMT_READY_TIMEOUT}s; SSH-based startup config loading may fail"
            )

    def intended_hostname(self):
        """Hostname from the startup config if it sets one, else the node name.

        Returns None if the chosen name is not a valid SONiC hostname, so an
        intentional startup-config hostname is never silently replaced.
        """
        hostname, source = self.hostname, "node name"
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE) as f:
                    meta = json.load(f).get("DEVICE_METADATA", {}).get("localhost", {})
                configured = meta.get("hostname")
            except (OSError, ValueError, AttributeError) as e:
                self.logger.warning(
                    f"Cannot read hostname from {CONFIG_FILE} ({type(e).__name__}), "
                    "using the node name"
                )
                configured = None
            if configured is not None:
                hostname, source = configured, f"startup config {CONFIG_FILE}"
        if not valid_hostname(hostname):
            self.logger.warning(
                f"Invalid hostname {hostname!r} from {source}, not setting hostname"
            )
            return None
        self.logger.info(f"Using hostname {hostname} from {source}")
        return hostname

    def bootstrap_hostname(self):
        """Apply the intended hostname to CONFIG_DB and the live system.

        Runs after CONFIG_DB initialization, so config-setup's reload cannot
        reset it; a startup config loaded later sets the same hostname.
        """
        hostname = self.intended_hostname()
        if hostname:
            self.prompt_write(hostname_cmd(hostname))

    def prompt_write(self, cmd, timeout=PROMPT_TIMEOUT):
        """Wait (bounded) for the root shell prompt, then send cmd.

        A lost prompt fails startup instead of leaving it hanging forever.
        """
        res = self.tn.read_until(b"#", timeout)
        if b"#" not in res:
            self.logger.error(
                f"No shell prompt on the serial console within {timeout}s, "
                "aborting startup"
            )
            sys.exit(1)
        self.wait_write(cmd, wait=None)

    def startup_config(self):
        """Load additional config provided by user."""

        if not os.path.exists(CONFIG_FILE):
            self.logger.trace(
                f"Startup config file {CONFIG_FILE} is not provided, nothing to do"
            )
            return

        self.logger.trace(
            f"Startup config file {CONFIG_FILE} found, copying it to the VM"
        )

        # handle_SIGCHLD would reap the child before subprocess collects its
        # exit status, which makes a failed restore look successful
        previous_sigchld = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        try:
            subprocess.run(
                f"/backup.sh -u {self.username} -p {self.password} restore",
                check=True,
                shell=True,
            )
        except subprocess.CalledProcessError as e:
            self.logger.error(
                f"Failed to apply startup config {CONFIG_FILE}: {e} Aborting startup."
            )
            sys.exit(1)
        finally:
            signal.signal(signal.SIGCHLD, previous_sigchld)


class Dell_SONiC(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode):
        super().__init__(username, password)
        self.vms = [Dell_Sonic_VM(hostname, username, password, conn_mode)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--hostname", default="sonic", help="SONiC hostname")
    parser.add_argument("--username", default="admin", help="Username")
    parser.add_argument("--password", default="admin", help="Password")
    parser.add_argument(
        "--connection-mode", default="tc", help="Connection mode to use in the datapath"
    )
    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()

    logger.setLevel(logging.DEBUG)
    if args.trace:
        logger.setLevel(1)

    vr = Dell_SONiC(
        args.hostname,
        args.username,
        args.password,
        conn_mode=args.connection_mode,
    )
    vr.start()
