#!/usr/bin/env python3
"""
Generate a plugin dependency list for a ComfyUI workflow.

The script inspects the workflow JSON, filters out nodes that ship with
ComfyUI, and matches the remaining nodes against the ComfyUI-Manager
extension catalog. The result is a JSON document describing which
plugins are required and which custom nodes from those plugins are used.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Pattern, Sequence, Set, Tuple


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve ComfyUI workflow custom node dependencies."
    )
    parser.add_argument(
        "--workflow",
        required=True,
        type=Path,
        help="Path to the workflow JSON file exported from ComfyUI.",
    )
    parser.add_argument(
        "--comfy-root",
        type=Path,
        default=None,
        help="Path to the ComfyUI repository (auto-detected or cloned into the current directory if absent).",    )
    parser.add_argument(
        "--manager-root",
        type=Path,
        default=None,
        help="Path to the ComfyUI-Manager repository (auto-detected or cloned into the current directory if absent).",
    )
    parser.add_argument(
        "--node-map",
        type=Path,
        default=None,
        help="Optional path to extension-node-map.json (defaults to node_db/dev/extension-node-map.json if present).",
    )
    parser.add_argument(
        "--special-config",
        type=Path,
        default=None,
        help="Path to a JSON file describing special-case node handling (see configs/special-node-overrides.json.example).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write the resulting dependency JSON. Defaults to stdout.",
    )
    return parser.parse_args()


class NodeClassCollector(ast.NodeVisitor):
    """Collect literal node names from NODE_CLASS_MAPPINGS definitions."""

    def __init__(self) -> None:
        self.node_names: Set[str] = set()

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802 - signature defined by NodeVisitor
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "NODE_CLASS_MAPPINGS":
                self._collect_from_node(node.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "NODE_CLASS_MAPPINGS"
            and func.attr == "update"
        ):
            for arg in node.args:
                self._collect_from_node(arg)
            for kw in node.keywords:
                if kw.arg is not None and isinstance(kw.arg, str):
                    self.node_names.add(kw.arg)
        self.generic_visit(node)

    def _collect_from_node(self, node: ast.AST) -> None:
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    self.node_names.add(key.value)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            self._collect_from_node(node.left)
            self._collect_from_node(node.right)
        elif isinstance(node, ast.Call):
            # Handles dict(...) literals with keyword arguments
            for kw in node.keywords:
                if kw.arg is not None:
                    self.node_names.add(kw.arg)
            for arg in node.args:
                self._collect_from_node(arg)


def gather_builtin_nodes(comfy_root: Path) -> Set[str]:
    collector = NodeClassCollector()

    candidate_files: Set[Path] = set()

    root_nodes = comfy_root / "nodes.py"
    if root_nodes.exists():
        candidate_files.add(root_nodes)

    for directory in ("comfy_extras", "comfy_api_nodes"):
        base = comfy_root / directory
        if base.is_dir():
            candidate_files.update(base.rglob("*.py"))

    for path in sorted(candidate_files):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            collector.visit(tree)
        except Exception as exc:  # pragma: no cover - diagnostic output only
            print(f"[warn] Could not parse {path}: {exc}", file=sys.stderr)

    return collector.node_names


def load_workflow_nodes(workflow_path: Path) -> Set[str]:
    data = json.loads(workflow_path.read_text(encoding="utf-8"))
    discovered: Set[str] = set()

    nodes_section = data.get("nodes")
    if isinstance(nodes_section, list):
        for node in nodes_section:
            if isinstance(node, dict):
                class_type = node.get("class_type") or node.get("type")
                if isinstance(class_type, str):
                    discovered.add(class_type)
    else:
        # Fallback: crawl entire structure in case of non-standard formats
        def _scan(obj: object) -> None:
            if isinstance(obj, dict):
                class_type = obj.get("class_type") or obj.get("type")
                if isinstance(class_type, str):
                    discovered.add(class_type)
                for value in obj.values():
                    _scan(value)
            elif isinstance(obj, list):
                for item in obj:
                    _scan(item)

        _scan(data)

    return discovered


def load_custom_node_catalog(manager_root: Path) -> Dict[str, Dict[str, object]]:
    """
    Build a mapping from any known URL (reference/files) to the corresponding
    custom node entry described in custom-node-list.json.
    """
    candidates = [
        manager_root / "node_db" / "dev" / "custom-node-list.json",
        manager_root / "custom-node-list.json",
    ]

    catalog: Dict[str, Dict[str, object]] = {}
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[warn] Failed to parse custom node list {path}: {exc}", file=sys.stderr)
            continue

        entries = data.get("custom_nodes")
        if not isinstance(entries, Sequence) or isinstance(entries, str):
            continue

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            reference = entry.get("reference")
            if isinstance(reference, str) and reference:
                catalog.setdefault(reference, entry)
            files = entry.get("files")
            if isinstance(files, Sequence) and not isinstance(files, str):
                for candidate in files:
                    if isinstance(candidate, str) and candidate:
                        catalog.setdefault(candidate, entry)

        # Prefer the first successfully parsed file
        if catalog:
            break

    return catalog


def load_extension_node_map(
    raw_data: Dict[str, object],
    custom_catalog: Dict[str, Dict[str, object]],
) -> Tuple[
    Dict[str, List[str]],
    Dict[str, Dict[str, object]],
    Dict[str, str],
    List[Tuple[Pattern[str], str]],
    Set[str],
]:
    node_to_plugins: Dict[str, List[str]] = defaultdict(list)
    plugin_metadata: Dict[str, Dict[str, object]] = {}
    preemption_map: Dict[str, str] = {}
    pattern_entries: List[Tuple[Pattern[str], str]] = []
    comfy_nodes: Set[str] = set()

    for raw_plugin_id, value in raw_data.items():
        nodes: List[str] = []
        metadata: Dict[str, object] = {}

        if isinstance(value, list) and value:
            first, *rest = value
            if isinstance(first, list):
                nodes = [entry for entry in first if isinstance(entry, str)]
            if rest and isinstance(rest[0], dict):
                metadata = {k: v for k, v in rest[0].items()}
        elif isinstance(value, dict):
            metadata = value

        custom_entry = custom_catalog.get(raw_plugin_id)
        canonical_id = raw_plugin_id
        if custom_entry:
            reference = custom_entry.get("reference")
            if isinstance(reference, str) and reference:
                canonical_id = reference
            combined_metadata = dict(metadata)
            combined_metadata.setdefault("reference", custom_entry.get("reference"))
            combined_metadata.setdefault("author", custom_entry.get("author"))
            combined_metadata.setdefault("title", custom_entry.get("title"))
            combined_metadata.setdefault("install_type", custom_entry.get("install_type"))
            description = custom_entry.get("description")
            if description and "description" not in combined_metadata:
                combined_metadata["description"] = description
            files = custom_entry.get("files")
            if isinstance(files, Sequence) and not isinstance(files, str):
                combined_metadata.setdefault("files", [item for item in files if isinstance(item, str)])
        else:
            combined_metadata = metadata

        existing_meta = plugin_metadata.get(canonical_id)
        if existing_meta:
            merged = dict(existing_meta)
            merged.update({k: v for k, v in combined_metadata.items() if v is not None})
            plugin_metadata[canonical_id] = merged
        else:
            plugin_metadata[canonical_id] = combined_metadata

        for node_name in nodes:
            normalized = node_name.strip()
            if normalized:
                node_to_plugins[normalized].append(canonical_id)

        if canonical_id == "https://github.com/comfyanonymous/ComfyUI":
            comfy_nodes.update(node.strip() for node in nodes if isinstance(node, str))

        preemptions = metadata.get("preemptions")
        if isinstance(preemptions, Sequence) and not isinstance(preemptions, str):
            for entry in preemptions:
                if isinstance(entry, str):
                    preemption_map[entry] = canonical_id

        pattern = metadata.get("nodename_pattern")
        if isinstance(pattern, str) and pattern:
            try:
                compiled = re.compile(pattern)
            except re.error:  # pragma: no cover - invalid pattern in source data
                continue
            pattern_entries.append((compiled, canonical_id))

    return node_to_plugins, plugin_metadata, preemption_map, pattern_entries, comfy_nodes


def resolve_dependencies(
    workflow_nodes: Set[str],
    builtin_nodes: Set[str],
    builtin_patterns: Sequence[Pattern[str]],
    node_to_plugins: Dict[str, List[str]],
    plugin_metadata: Dict[str, Dict[str, object]],
    preemption_map: Dict[str, str],
    pattern_entries: List[Tuple[Pattern[str], str]],
    plugin_overrides: Dict[str, str],
) -> Tuple[List[Dict[str, object]], List[str]]:
    plugins: Dict[str, Dict[str, object]] = {}
    unresolved: Set[str] = set()

    for node_name in sorted(workflow_nodes):
        if node_name in builtin_nodes or any(pattern.search(node_name) for pattern in builtin_patterns):
            continue
        override_plugin = plugin_overrides.get(node_name)
        plugin_id: Optional[str]
        plugin_ids = node_to_plugins.get(node_name)

        if override_plugin is not None:
            plugin_id = override_plugin
        else:
            plugin_id = preemption_map.get(node_name)
            if plugin_id is None and plugin_ids:
                plugin_id = plugin_ids[0]

            if plugin_id is None:
                for pattern, candidate_plugin in pattern_entries:
                    if pattern.search(node_name):
                        plugin_id = candidate_plugin
                        break

        if plugin_id is None and plugin_ids:
            plugin_id = plugin_ids[0]

        if plugin_id is None:
            unresolved.add(node_name)
            continue

        entry = plugins.setdefault(
            plugin_id,
            {
                "id": plugin_id,
                "nodes": set(),  # type: ignore[dict-item]
                "metadata": plugin_metadata.get(plugin_id, {}),
            },
        )
        entry["nodes"].add(node_name)  # type: ignore[arg-type]

    plugin_list: List[Dict[str, object]] = []
    for plugin_id, entry in sorted(plugins.items(), key=lambda item: item[0]):
        node_list = sorted(entry["nodes"])  # type: ignore[arg-type]
        plugin_entry = {"id": plugin_id, "nodes": node_list}
        if entry["metadata"]:
            plugin_entry["metadata"] = entry["metadata"]
        plugin_list.append(plugin_entry)

    return plugin_list, sorted(unresolved)


def load_special_config(path: Optional[Path]) -> Tuple[Set[str], List[Pattern[str]], Dict[str, str]]:
    builtin_overrides: Set[str] = set()
    builtin_patterns: List[Pattern[str]] = []
    plugin_overrides: Dict[str, str] = {}

    if path is None:
        return builtin_overrides, builtin_patterns, plugin_overrides

    if not path.exists():
        print(f"[error] Special config not found: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[error] Failed to parse special config {path}: {exc}", file=sys.stderr)
        sys.exit(1)

    builtin_list = data.get("builtin_nodes")
    if isinstance(builtin_list, Sequence) and not isinstance(builtin_list, str):
        for item in builtin_list:
            if isinstance(item, str):
                pattern = _maybe_compile_pattern(item)
                if pattern is None:
                    builtin_overrides.add(item)
                else:
                    builtin_patterns.append(pattern)

    plugin_map = data.get("plugin_overrides")
    if isinstance(plugin_map, dict):
        for node_name, plugin_id in plugin_map.items():
            if isinstance(node_name, str) and isinstance(plugin_id, str):
                plugin_overrides[node_name] = plugin_id

    return builtin_overrides, builtin_patterns, plugin_overrides


def _maybe_compile_pattern(value: str) -> Optional[Pattern[str]]:
    """Attempt to detect and compile regex-like builtin node declarations."""
    regex_tokens = set(".*+?[](){}|^$")
    if not any(token in value for token in regex_tokens):
        return None
    try:
        return re.compile(value)
    except re.error:
        print(f"[warn] 无法解析内置节点正则: {value}", file=sys.stderr)
        return None


def ensure_repo(path: Path, repo_url: str) -> Path:
    if path.exists():
        return path

    print(f"[info] Cloning {repo_url} into {path}", file=sys.stderr)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        print("[error] git executable not found; cannot clone required repository.", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="ignore") if exc.stderr else ""
        print(f"[error] Failed to clone {repo_url}:\n{stderr}", file=sys.stderr)
        sys.exit(1)

    return path


def main() -> None:
    args = parse_args()

    script_dir = Path(__file__).resolve().parent

    comfy_root = args.comfy_root or (script_dir / "ComfyUI")
    manager_root = args.manager_root or (script_dir / "ComfyUI-Manager")

    comfy_root = ensure_repo(comfy_root, "https://github.com/comfyanonymous/ComfyUI") if not comfy_root.exists() else comfy_root
    manager_root = ensure_repo(manager_root, "https://github.com/Comfy-Org/ComfyUI-Manager") if not manager_root.exists() else manager_root

    required_manager_files = [
        manager_root / "node_db" / "dev" / "extension-node-map.json",
    ]
    if not any(path.exists() for path in required_manager_files):
        fallback_root = manager_root.parent / f"{manager_root.name}-download"
        manager_root = ensure_repo(fallback_root, "https://github.com/Comfy-Org/ComfyUI-Manager")

    required_comfy_files = [
        comfy_root / "nodes.py",
        comfy_root / "comfy" / "__init__.py",
    ]
    if not any(path.exists() for path in required_comfy_files):
        fallback_root = comfy_root.parent / f"{comfy_root.name}-download"
        comfy_root = ensure_repo(fallback_root, "https://github.com/comfyanonymous/ComfyUI")

    workflow_nodes = load_workflow_nodes(args.workflow)
    builtin_nodes = gather_builtin_nodes(comfy_root)

    node_map_data: Optional[Dict[str, object]] = None
    if args.node_map:
        node_map_path = args.node_map
        if not node_map_path.exists():
            print(f"[error] Could not find extension-node-map.json at {node_map_path}", file=sys.stderr)
            sys.exit(1)
        node_map_data = json.loads(node_map_path.read_text(encoding="utf-8"))
    else:
        preferred = manager_root / "node_db" / "dev" / "extension-node-map.json"
        if preferred.exists():
            node_map_data = json.loads(preferred.read_text(encoding="utf-8"))
            if fallback.exists():
                fallback_data = json.loads(fallback.read_text(encoding="utf-8"))
                for key, value in fallback_data.items():
                    node_map_data.setdefault(key, value)
        elif fallback.exists():
            node_map_data = json.loads(fallback.read_text(encoding="utf-8"))
        else:
            print(
                f"[error] Could not find extension-node-map.json at either {preferred} or {fallback}",
                file=sys.stderr,
            )
            sys.exit(1)

    if node_map_data is None:
        print("[error] Failed to load extension-node-map.json data.", file=sys.stderr)
        sys.exit(1)

    builtin_overrides, builtin_patterns, plugin_overrides = load_special_config(args.special_config)

    custom_catalog = load_custom_node_catalog(manager_root)

    (
        node_to_plugins,
        plugin_metadata,
        preemption_map,
        pattern_entries,
        comfy_nodes,
    ) = load_extension_node_map(node_map_data, custom_catalog)

    builtin_nodes.update(comfy_nodes)
    builtin_nodes.update(builtin_overrides)
    plugin_list, unresolved_nodes = resolve_dependencies(
        workflow_nodes,
        builtin_nodes,
        builtin_patterns,
        node_to_plugins,
        plugin_metadata,
        preemption_map,
        pattern_entries,
        plugin_overrides,
    )

    result: Dict[str, object] = {"plugins": plugin_list}
    if unresolved_nodes:
        result["unresolved_nodes"] = unresolved_nodes

    output_text = json.dumps(result, indent=2, sort_keys=False)

    if args.output:
        args.output.write_text(output_text + "\n", encoding="utf-8")
    else:
        print(output_text)


if __name__ == "__main__":
    main()
