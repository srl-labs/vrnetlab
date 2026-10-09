"""Validate the product family and optional FortiOS version constraint."""

import os
import re
from dataclasses import dataclass

from .base import Feature
from .image_info import FortiOSVersion


VERSION_CONSTRAINT = re.compile(
    r"^(?P<version>\d+(?:\.\d+){0,2})(?:\.b(?P<build_tag>\d+))?$"
    r"|^(?P<range_min>\d+(?:\.\d+){0,3})-(?P<range_max>\d+(?:\.\d+){0,3})$"
)


@dataclass(frozen=True)
class VersionBound:
    values: tuple[int, ...]
    is_upper: bool = False

    def matches(self, version: FortiOSVersion) -> bool:
        actual = (version.major, version.minor, version.patch, version.build)
        if self.is_upper:
            return actual <= self.as_tuple(fill=999999)
        return actual >= self.as_tuple(fill=0)

    def as_tuple(self, fill):
        return self.values + (fill,) * (4 - len(self.values))


@dataclass(frozen=True)
class VersionConstraint:
    minimum: VersionBound
    maximum: VersionBound | None = None
    build: int | None = None

    def matches(self, version):
        return (
            (self.build is None or version.build == self.build)
            and self.minimum.matches(version)
            and (self.maximum is None or self.maximum.matches(version))
        )


def parse_version_constraint(value):
    if not value:
        return None
    value = value.strip()
    match = VERSION_CONSTRAINT.fullmatch(value)
    if not match:
        raise ValueError(
            "FOS_PRODUCT_VERSION must be a release prefix (such as 8 or 8.0), "
            "a build selector (such as 8.0.b278), or an inclusive range "
            "such as 7.2-8.0.1"
        )
    if match.group("version") is not None:
        parsed = tuple(int(component) for component in match.group("version").split("."))
        minimum = VersionBound(parsed)
        maximum = VersionBound(parsed, is_upper=True)
        build_value = match.group("build_tag")
        build = int(build_value) if build_value else None
    else:
        minimum = VersionBound(
            tuple(int(component) for component in match.group("range_min").split("."))
        )
        maximum = VersionBound(
            tuple(int(component) for component in match.group("range_max").split(".")),
            is_upper=True,
        )
        build = None
    constraint = VersionConstraint(minimum, maximum, build)
    if maximum is not None and minimum.as_tuple(0) > maximum.as_tuple(999999):
        raise ValueError("FOS_PRODUCT_VERSION range minimum must not exceed maximum")
    return constraint


class ValidateProduct(Feature):
    """Reject an image whose reported product or version is not expected."""

    def __init__(self, vm, commander, expected_product):
        super().__init__(vm, commander, "product-validation")
        self.expected_product = expected_product
        self.skip = os.getenv("FOS_SKIP_PRODUCT_CHECK", "").strip().lower() == "true"
        self.constraint = None if self.skip else parse_version_constraint(
            os.getenv("FOS_PRODUCT_VERSION")
        )

    def activate(self):
        if self.skip:
            self.commander.feature_complete(self)
            return
        product = self.vm.fos_product
        version = self.vm.fos_version
        if product != self.expected_product:
            raise RuntimeError(
                f"Image product mismatch: expected {self.expected_product}, got {product}"
            )
        if self.constraint is not None and (version is None or not self.constraint.matches(version)):
            expected = os.getenv("FOS_PRODUCT_VERSION")
            actual = str(version) if version is not None else "unknown"
            raise RuntimeError(f"Image version mismatch: expected {expected}, got {actual}")
        self.commander.logger.info("Validated product %s (%s)", product, version)
        self.commander.feature_complete(self)

    def on_block_complete(self):
        self.commander.feature_complete(self)
