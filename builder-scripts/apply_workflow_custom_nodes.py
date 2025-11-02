#!/usr/bin/env python3
"""
Fetch workflow-specific custom nodes and aggregate their Python requirements.

The script performs three tasks:
1. Resolve plugin repositories from the workflow dependency manifest.
2. Clone the required plugins into the provided custom node directory.
3. Collect requirement entries that are not already covered by pak3/pak7.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


GIT_CLONE_FLAGS: Tuple[str, ...] = (
    "--depth=1",
    "--no-tags",
    "--recurse-submodules",
    "--shallow-submodules",
)


@dataclass
class RequirementEntry:
    original: str
    kind: str  # package | vcs | other
    key: str


@dataclass
class PluginPlan:
    plugin_id: str
    nodes: List[str]
    repo_url: Optional[str]
    slug: Optional[str]
    reason: Optional[str]
    status: str = "planned"  # planned | skipped | cloned | failed
    message: Optional[str] = None
    requirements: List[Path] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clone workflow-required custom nodes and gather requirements."
    )
    parser.add_argument("--deps", required=True, type=Path, help="Path to workflow dependencies JSON.")
    parser.add_argument(
        "--custom-node-root",
        required=True,
        type=Path,
        help="Directory where custom nodes should be cloned.",
    )
    parser.add_argument(
        "--requirements-output",
        required=True,
        type=Path,
        help="Where to write deduplicated requirements for workflow plugins.",
    )
    parser.add_argument(
        "--summary-output",
        required=True,
        type=Path,
        help="Where to write a JSON summary describing processed plugins.",
    )
    parser.add_argument(
        "--pak3",
        required=True,
        type=Path,
        help="Path to pak3 baseline requirements.",
    )
    parser.add_argument(
        "--pak7",
        required=True,
        type=Path,
        help="Path to pak7 baseline requirements.",
    )
    return parser.parse_args()


def load_json(path: Path) -> Dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive error reporting
        print(f"[error] 无法读取工作流依赖文件 {path}: {exc}", file=sys.stderr)
        sys.exit(1)


def normalize_git_url(candidate: Optional[str]) -> Optional[str]:
    if not candidate:
        return None
    url = candidate.strip()
    if not url:
        return None
    if url.startswith("git+"):
        url = url[4:]
    lowered = url.lower()
    if lowered.startswith(("http://", "https://", "ssh://")) or "@" in url.split("/")[0]:
        return url
    return None


def slugify(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-").lower()
    return cleaned or "custom-node"


def ensure_unique_slug(slug: str, existing: Set[str]) -> str:
    if slug not in existing:
        existing.add(slug)
        return slug
    idx = 2
    while True:
        candidate = f"{slug}-{idx}"
        if candidate not in existing:
            existing.add(candidate)
            return candidate
        idx += 1


def derive_slug(repo_url: str, used: Set[str]) -> str:
    cleaned = repo_url.split("?", 1)[0].rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    slug_part = cleaned.rsplit("/", 1)[-1]
    return ensure_unique_slug(slugify(slug_part), used)


def plan_plugins(deps: Dict[str, object]) -> Tuple[List[PluginPlan], List[str]]:
    raw_plugins = deps.get("plugins")
    plans: List[PluginPlan] = []
    unresolved_nodes: List[str] = list(map(str, deps.get("unresolved_nodes", []) or []))
    used_slugs: Set[str] = set()

    if not isinstance(raw_plugins, Sequence):
        return plans, unresolved_nodes

    for entry in raw_plugins:
        if not isinstance(entry, dict):
            continue
        plugin_id = str(entry.get("id", "")).strip()
        nodes = [str(node) for node in entry.get("nodes", []) if isinstance(node, str)]
        metadata = entry.get("metadata")
        repo_url: Optional[str] = None
        reason: Optional[str] = None
        if isinstance(metadata, dict):
            for key in ("repo", "repository", "github", "git", "url", "homepage"):
                candidate = metadata.get(key)
                repo_url = normalize_git_url(candidate if isinstance(candidate, str) else None)
                if repo_url:
                    reason = f"metadata.{key}"
                    break

        if repo_url is None:
            repo_url = normalize_git_url(plugin_id)
            if repo_url:
                reason = "plugin_id"

        slug = derive_slug(repo_url, used_slugs) if repo_url else None
        plans.append(
            PluginPlan(
                plugin_id=plugin_id or "<unknown>",
                nodes=nodes,
                repo_url=repo_url,
                slug=slug,
                reason=reason,
            )
        )

    return plans, unresolved_nodes


def clone_plugin(plan: PluginPlan, root: Path) -> PluginPlan:
    if plan.repo_url is None or plan.slug is None:
        plan.status = "skipped"
        plan.message = "未找到可用的仓库地址"
        return plan

    target_dir = root / plan.slug
    if target_dir.exists():
        plan.status = "skipped"
        plan.message = "目录已存在，跳过克隆"
        plan.requirements = find_requirement_files(target_dir)
        return plan

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", *GIT_CLONE_FLAGS, plan.repo_url, str(target_dir)]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        plan.status = "cloned"
        plan.requirements = find_requirement_files(target_dir)
    except FileNotFoundError:
        plan.status = "failed"
        plan.message = "未找到 git 可执行文件"
    except subprocess.CalledProcessError as exc:
        plan.status = "failed"
        stderr = exc.stderr.decode("utf-8", errors="ignore") if exc.stderr else ""
        plan.message = stderr.strip() or "git clone 执行失败"
    return plan


def find_requirement_files(plugin_dir: Path) -> List[Path]:
    results: List[Path] = []
    for candidate in sorted(plugin_dir.glob("requirements*")):
        if candidate.is_file():
            results.append(candidate)
    standalone = plugin_dir / "requirements"
    if standalone.is_file():
        results.append(standalone)
    return results


REQ_PATTERN = re.compile(r"^\s*([A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?)")


def parse_requirement_line(line: str) -> Optional[RequirementEntry]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    lowered = stripped.lower()
    if lowered.startswith(("-r ", "--requirement", "--requirements", "-c ", "--constraint")):
        return None
    if "git+" in lowered or lowered.startswith(("-e ", "--editable")):
        return RequirementEntry(original=stripped, kind="vcs", key=lowered)
    if "@" in stripped:
        name, _rest = stripped.split("@", 1)
        base = name.strip()
    else:
        match = REQ_PATTERN.match(stripped)
        if not match:
            return RequirementEntry(original=stripped, kind="other", key=lowered)
        base = match.group(1)
    normalized = base.replace("_", "-").lower()
    return RequirementEntry(original=stripped, kind="package", key=normalized)


def load_known_requirements(paths: Iterable[Path]) -> Tuple[Set[str], Set[str]]:
    packages: Set[str] = set()
    vcs_entries: Set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:  # pragma: no cover - defensive
            continue
        for line in lines:
            entry = parse_requirement_line(line)
            if entry is None:
                continue
            if entry.kind == "package":
                packages.add(entry.key)
            elif entry.kind == "vcs":
                vcs_entries.add(entry.key)
    return packages, vcs_entries


def collect_requirements(
    plans: Sequence[PluginPlan],
    requirements_output: Path,
    known_packages: Set[str],
    known_vcs: Set[str],
) -> List[str]:
    collected: List[str] = []
    current_packages = set(known_packages)
    current_vcs = set(known_vcs)

    for plan in plans:
        if plan.status not in {"cloned", "skipped"}:
            continue
        for req_file in plan.requirements:
            try:
                lines = req_file.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for line in lines:
                entry = parse_requirement_line(line)
                if entry is None:
                    continue
                if entry.kind == "package":
                    if entry.key in current_packages:
                        continue
                    current_packages.add(entry.key)
                elif entry.kind == "vcs":
                    if entry.key in current_vcs:
                        continue
                    current_vcs.add(entry.key)
                else:
                    identifier = f"{entry.kind}:{entry.key}"
                    if identifier in current_vcs:
                        continue
                    current_vcs.add(identifier)
                collected.append(entry.original)

    if collected:
        requirements_output.parent.mkdir(parents=True, exist_ok=True)
        requirements_output.write_text("\n".join(collected) + "\n", encoding="utf-8")
    elif requirements_output.exists():
        requirements_output.unlink()

    return collected


def write_summary(
    summary_output: Path,
    plans: Sequence[PluginPlan],
    unresolved_nodes: Sequence[str],
    collected_requirements: Sequence[str],
) -> None:
    summary = {
        "workflow_plugins": [
            {
                "id": plan.plugin_id,
                "repo_url": plan.repo_url,
                "slug": plan.slug,
                "nodes": plan.nodes,
                "status": plan.status,
                "reason": plan.reason,
                "message": plan.message,
                "requirements_files": [str(path) for path in plan.requirements],
            }
            for plan in plans
        ],
        "unresolved_nodes": list(unresolved_nodes),
        "collected_requirements": list(collected_requirements),
    }
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()

    if not args.deps.exists():
        print(f"[info] 未找到工作流依赖文件 {args.deps}，跳过按需插件。")
        return

    deps_data = load_json(args.deps)
    plans, unresolved_nodes = plan_plugins(deps_data)
    if not plans:
        print("[info] 工作流未声明任何额外插件，跳过克隆。")
        if args.requirements_output.exists():
            args.requirements_output.unlink()
        write_summary(args.summary_output, plans, unresolved_nodes, [])
        return

    args.custom_node_root.mkdir(parents=True, exist_ok=True)

    processed_plans: List[PluginPlan] = []
    missing_repos = 0
    clone_failures = 0

    print(f"[info] 需要处理的插件数量: {len(plans)}")
    for plan in plans:
        updated = clone_plugin(plan, args.custom_node_root)
        processed_plans.append(updated)
        if updated.repo_url is None:
            print(f"[warn] 插件 {updated.plugin_id} 无法解析仓库地址。")
            missing_repos += 1
        elif updated.status == "failed":
            print(f"[warn] 插件 {updated.plugin_id} 克隆失败: {updated.message}")
            clone_failures += 1
        else:
            print(f"[info] 插件 {updated.plugin_id} -> {updated.status} ({updated.slug})")

    known_packages, known_vcs = load_known_requirements([args.pak3, args.pak7])
    collected_requirements = collect_requirements(processed_plans, args.requirements_output, known_packages, known_vcs)

    if collected_requirements:
        print(f"[info] 新增依赖 {len(collected_requirements)} 条，已写入 {args.requirements_output}")
    else:
        print("[info] 未发现新的依赖需求。")

    if unresolved_nodes:
        print(f"[warn] 未能解析以下节点: {', '.join(unresolved_nodes)}")

    write_summary(args.summary_output, processed_plans, unresolved_nodes, collected_requirements)

    if missing_repos or clone_failures:
        print(
            f"[error] 存在 {missing_repos} 个缺少仓库地址的插件，"
            f"{clone_failures} 个插件克隆失败，构建已中止。",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
