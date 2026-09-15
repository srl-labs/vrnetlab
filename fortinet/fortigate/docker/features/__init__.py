"""FortiOS bootstrap features."""

from .credentials import CredentialsFeature
from .base import Feature, StaticFeature
from .mgmt_dns import ConfigureMgmtDns
from .capture_config import ConfigSaveFeature
from .disks import FormatDisks
from file_watcher import FeatureFileWatcher
from .default_config import DefaultConfig
from .fortitoken import WaitForFortiTokens
from .license import SetLicense, WaitForLicenseValidation
from .mgmt_net import ConfigureMgmtNetwork, ReconfigureMgmtNetwork, MoveMgmtToVrf1
from .pki import InstallPkiCertificates
from .system_version import DetectSystemVersion, FortiOSVersion
from .startup_config import ApplyStartupConfig, parse_startup_config
from .fortiguard_hooks import (
    ConfigureFortiGuardHooks,
    ReapplyFortiGuardHooks,
    fortiguard_hooks_enabled,
)

__all__ = [
    "CredentialsFeature",
    "ConfigureMgmtDns",
    "ConfigSaveFeature",
    "FormatDisks",
    "Feature",
    "FeatureFileWatcher",
    "DefaultConfig",
    "WaitForFortiTokens",
    "SetLicense",
    "WaitForLicenseValidation",
    "ConfigureMgmtNetwork",
    "ConfigureFortiGuardHooks",
    "ReapplyFortiGuardHooks",
    "InstallPkiCertificates",
    "DetectSystemVersion",
    "FortiOSVersion",
    "ReconfigureMgmtNetwork",
    "MoveMgmtToVrf1",
    "StaticFeature",
    "ApplyStartupConfig",
    "parse_startup_config",
    "fortiguard_hooks_enabled",
]
