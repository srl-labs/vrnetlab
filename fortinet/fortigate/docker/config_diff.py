"""Capture, normalize, and structurally diff FortiOS CLI configuration."""

import os
import re
from dataclasses import dataclass, field


@dataclass
class ConfigNode:
    original: str
    normalized: str
    kind: str
    children: list = field(default_factory=list)

    @property
    def is_block(self):
        return self.kind in ("config", "edit")

    @property
    def is_encrypted(self):
        return "ENC <encrypted>" in self.normalized

    def render(self):
        lines = [self.original]
        for child in self.children:
            lines.extend(child.render())
        indent = self.original[: len(self.original) - len(self.original.lstrip())]
        if self.kind == "edit":
            lines.append(indent + "next")
        elif self.kind == "config":
            lines.append(indent + "end")
        return lines


def normalize_line(line):
    normalized = re.sub(r"[ \t]+", " ", line).strip()
    # FortiOS emits a fresh ENC representation for protected fields on each
    # show. Compare those fields structurally but preserve the original value
    # whenever a new or changed config object is emitted.
    return re.sub(r"\bENC\s+\S+$", "ENC <encrypted>", normalized)


def literal_line(line):
    return re.sub(r"[ \t]+", " ", line).strip()


def _prune_empty_config_blocks(node):
    children = []
    for child in node.children:
        _prune_empty_config_blocks(child)
        if child.kind == "config" and not child.children:
            continue
        children.append(child)
    node.children = children


def parse_config(config):
    root = ConfigNode("", "", "root")
    stack = [root]
    for raw_line in config.splitlines():
        original = raw_line.rstrip()
        normalized = normalize_line(original)
        if not normalized or normalized.startswith("#"):
            continue
        if normalized in ("next", "end"):
            if len(stack) > 1:
                stack.pop()
            continue

        if normalized.startswith("config "):
            kind = "config"
        elif normalized.startswith("edit "):
            kind = "edit"
        else:
            kind = "leaf"

        node = ConfigNode(original, normalized, kind)
        stack[-1].children.append(node)
        if node.is_block:
            stack.append(node)
    _prune_empty_config_blocks(root)
    return root


def _find_match(nodes, target, used):
    for index, node in enumerate(nodes):
        if index not in used and node.kind == target.kind and node.normalized == target.normalized:
            return index, node
    return None, None


def _delta_nodes(baseline, current, track_encrypted_changes):
    result = []
    used = set()
    for node in current:
        index, match = _find_match(baseline, node, used)
        if index is None:
            result.append(node)
            continue
        used.add(index)
        if not node.is_block:
            if (
                track_encrypted_changes
                and node.is_encrypted
                and literal_line(node.original) != literal_line(match.original)
            ):
                result.append(node)
            continue

        children = _delta_nodes(
            match.children,
            node.children,
            track_encrypted_changes,
        )
        if children:
            # An edit is the replayable entry boundary. Once any part of its
            # body changes, emit the complete current subtree, including ENC.
            if node.kind == "edit":
                result.append(node)
            else:
                result.append(
                    ConfigNode(node.original, node.normalized, node.kind, children)
                )
    return result


def diff_config(baseline, current, track_encrypted_changes=True):
    baseline_tree = parse_config(baseline)
    current_tree = parse_config(current)
    delta = _delta_nodes(
        baseline_tree.children,
        current_tree.children,
        track_encrypted_changes,
    )
    lines = []
    for node in delta:
        lines.extend(node.render())
    return "\n".join(lines)
