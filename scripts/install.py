#!/usr/bin/env python3
"""Install only a pinned, reviewed Skill package manifest, never local secrets."""
import argparse
import os
import shutil
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = "api-sse-probe"
MANIFEST = "TEAM_MANIFEST.txt"
ROOT_FILES = {".gitignore", "SKILL.md", "README.md", "SECURITY.md", "LICENSE",
              "config.example.json", "config.annotated.jsonc", MANIFEST}
FOLDERS = {"scripts": {".py"}, "references": {".md"},
           "assets": {".csv", ".json", ".sse", ".md"},
           "tests": {".py"}, "examples": {".json", ".md"}}

def package_paths():
    manifest = ROOT / MANIFEST
    if not manifest.is_file() or manifest.is_symlink():
        raise ValueError("missing_reviewed_team_manifest")
    paths = []
    seen = set()
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        name = raw.strip()
        if not name or name.startswith("#"):
            continue
        rel = PurePosixPath(name)
        if (rel.is_absolute() or ".." in rel.parts or "\\" in name or
                name in seen or not rel.parts):
            raise ValueError("invalid_or_duplicate_manifest_entry")
        seen.add(name)
        if name not in ROOT_FILES and not (
                rel.parts[0] in FOLDERS and len(rel.parts) >= 2
                and Path(name).suffix in FOLDERS[rel.parts[0]]):
            raise ValueError("manifest_entry_not_allowed")
        file = ROOT.joinpath(*rel.parts)
        if not file.is_file() or file.is_symlink() or any(
                folder.is_symlink() for folder in file.parents if folder != ROOT
                and ROOT in folder.parents):
            raise ValueError("missing_or_symlinked_package_file")
        paths.append((file, Path(*rel.parts)))
    names = {p.as_posix() for _, p in paths}
    required = {MANIFEST, "SKILL.md", "config.example.json",
                "scripts/probe.py", "scripts/lib/tencent_mcp.py",
                "scripts/lib/feishu_transport.py", "scripts/install.py",
                "scripts/setup.py", "scripts/check_skill_contract.py", "SECURITY.md", ".gitignore"}
    if not required.issubset(names):
        raise ValueError("required_package_entries_missing")
    return paths

def main(argv=None):
    parser = argparse.ArgumentParser(description="Install verified api-sse-probe Skill")
    parser.add_argument("--target", required=True,
                        help="Existing Agent Skills *root* folder")
    args = parser.parse_args(argv)
    root = Path(args.target).expanduser().resolve()
    if not root.is_dir():
        parser.error("target 必须是已存在的 Agent Skills 根目录")
    target = root / SKILL_NAME
    if target.exists():
        parser.error("目标 Skill 目录已存在；为了保护配置不自动覆盖")
    files = package_paths()  # validate manifest before touching destination
    target.mkdir(mode=0o700)
    try:
        for source, relative in files:
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        # No private config is created during installation. User runs
        # scripts/setup.py init on the installed copy in a local terminal.
        print(str(target))
    except Exception:
        shutil.rmtree(target)
        raise

if __name__ == "__main__":
    main()