# In pass-through mode, we also spin up a tftp server, but in this case we create a new namespace
# inside the container that simulates the IP addressing of the host.
# we redirect traffic to this ns by using tc flower filters
import logging
import typing
from abc import abstractmethod, ABCMeta

import vrnetlab


class _TFTPLauncher(metaclass=ABCMeta):

    def __init__(self, addr, srv_port, directory, tid_range):
        super().__init__()
        self._directory = directory
        self._srv_port = srv_port
        self._tid_range = tid_range
        self._addr = addr

    @abstractmethod
    def launch(self): ...


class _HostForwardedLauncher(_TFTPLauncher):
    def launch(self):
        logger = logging.getLogger()
        logger.info(f"Launching TFTP Server in Host-Forwarded mode. at={self._addr}:{self._srv_port}")
        cmd = [
            "in.tftpd",
            "--listen",
            "--user",
            "root",
            "-a",
            f"{self._addr}:{self._srv_port}",
            "-s",
            "-c",
            "-v",
            "-p",
        ]
        if self._tid_range is not None:
            cmd.append("-R")
            cmd.append(":".join(map(str, self._tid_range)))
        cmd.append(self._directory)
        vrnetlab.run_command(cmd)


class _PassthroughLauncher(_TFTPLauncher):

    def launch(self, ):
        logger = logging.getLogger()
        logger.info(f"Launching TFTP Server in Passthrough mode. at={self._addr}:{self._srv_port}")

        # start tftp in ns, assign ports to server so it's easier to track it with flower filters
        vrnetlab.run_command(
            [
                "ip",
                "netns",
                "exec",
                "fakehost",
                "in.tftpd",
                "--listen",
                "--user",
                "root",
                "-a",
                f"{self._addr}:{self._srv_port}",
                "-R",
                ":".join(map(str, self._tid_range)),
                "-s",
                "-c",
                "-v",
                "-p",
                self._directory,
            ]
        )


class TFTPServer(_TFTPLauncher):
    def __init__(self, addr="0.0.0.0", directory="/tftpboot", mgmt_net=None, srv_port=69,
                 tid_range: typing.Iterable[int] = None):
        super().__init__(addr, srv_port, directory, tid_range)
        if mgmt_net and mgmt_net.mgmt_passthrough:
            self.launcher = _PassthroughLauncher(addr, srv_port, directory, tid_range)
        else:
            self.launcher = _HostForwardedLauncher(addr, srv_port, directory, tid_range)

    def launch(self):
        self.launcher.launch()
        vrnetlab.run_command(["chmod", "-R", "777", self._directory])
