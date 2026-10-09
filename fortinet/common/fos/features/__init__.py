"""FortiOS bootstrap features."""

from .credentials import CredentialsFeature
from .base import Feature, StaticFeature
from .mgmt_dns import ConfigureMgmtDns
from .capture_config import ConfigSaveFeature
from .disks import FormatDisks
from ..file_watcher import FeatureFileWatcher
from .product import ValidateProduct, parse_version_constraint
from .default_config import DefaultConfig
from .fortitoken import WaitForFortiTokens
from .license import SetLicense, WaitForLicenseValidation
from .mgmt_net import ConfigureMgmtNetwork, ReconfigureMgmtNetwork, MoveMgmtToVrf1
from .pki import InstallPkiCertificates
from .image_info import ImageInfo, FortiOSVersion, product_from_system_status
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
    "ImageInfo",
    "FortiOSVersion",
    "product_from_system_status",
    "ReconfigureMgmtNetwork",
    "MoveMgmtToVrf1",
    "StaticFeature",
    "ApplyStartupConfig",
    "parse_startup_config",
    "fortiguard_hooks_enabled",
    "ValidateProduct",
    "parse_version_constraint",
]
