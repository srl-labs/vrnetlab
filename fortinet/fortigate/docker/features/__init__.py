"""FortiOS bootstrap features."""

from .credentials import CredentialsFeature
from .base import Feature, StaticFeature
from .mgmt_dns import ConfigureMgmtDns
from .capture_config import ConfigSaveFeature
from .disks import FormatDisks
from file_watcher import FeatureFileWatcher
from .default_config import DefaultConfig
from .license import SetLicense, WaitForLicenseValidation
from .mgmt_net import ConfigureMgmtNetwork, ReconfigureMgmtNetwork, MoveMgmtToVrf1
from .startup_config import ApplyStartupConfig, parse_startup_config
from .tst_license import ConfigureTestLicenseFortiGuard, tst_license_enabled

__all__ = [
    "CredentialsFeature",
    "ConfigureMgmtDns",
    "ConfigSaveFeature",
    "FormatDisks",
    "Feature",
    "FeatureFileWatcher",
    "DefaultConfig",
    "SetLicense",
    "WaitForLicenseValidation",
    "ConfigureMgmtNetwork",
    "ConfigureTestLicenseFortiGuard",
    "ReconfigureMgmtNetwork",
    "MoveMgmtToVrf1",
    "StaticFeature",
    "ApplyStartupConfig",
    "parse_startup_config",
    "tst_license_enabled",
]

from .tst_license import ConfigureTestLicenseFortiGuard
