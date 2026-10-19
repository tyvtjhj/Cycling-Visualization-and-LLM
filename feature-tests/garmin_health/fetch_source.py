#!/usr/bin/env python3
"""按固定 commit 下载「佳明健康看板」核心源码到本地 vendor/raw。

仅用于开发期阅读/评估，第三方代码无 LICENSE，不得提交或再分发。
脚本只用 Python 标准库。

用法：
    python feature-tests/garmin_health/fetch_source.py
    python feature-tests/garmin_health/fetch_source.py --force
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

REPO = "Jonathan-614/astrbot_plugin_garmin_health"
REPO_URL = f"https://github.com/{REPO}"
COMMIT = "9f2655cf5f6378651f6e25f3a517fb8eb61f90aa"
TARBALL_URL = f"https://codeload.github.com/{REPO}/tar.gz/{COMMIT}"

# 核心功能相关文件；logo.png 为二进制图标，不下载
CORE_FILES = (
    "README.md",
    "CHANGELOG.md",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "main.py",
    "services.py",
    "client_manager.py",
    "tools.py",
    "utils.py",
)

HERE = Path(__file__).resolve().parent
DEFAULT_DEST = HERE / "vendor" / "raw"


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ride-tracker-eval/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (固定 HTTPS 地址)
        return resp.read()


def _extract_core_files(tarball: bytes, dest: Path) -> dict[str, bytes]:
    """从 GitHub tarball 中提取核心文件，返回 {文件名: 内容}。"""
    extracted: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = member.name.rsplit("/", 1)[-1]
            if name not in CORE_FILES:
                continue
            # 只接受仓库根目录下的文件（tarball 内层级为 <repo>-<sha>/<file>）
            if "/" in member.name and len(member.name.split("/")) != 2:
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            extracted[name] = handle.read()
    return extracted


def _write_manifest(dest: Path, files: dict[str, bytes]) -> Path:
    lines = []
    for name in sorted(files):
        data = files[name]
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {len(data):>8d}  {name}")
    manifest = dest.parent / "MANIFEST.sha256"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载 garmin_health 核心源码到本地 vendor/raw")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="输出目录，默认 vendor/raw")
    parser.add_argument("--force", action="store_true", help="已存在文件时覆盖")
    args = parser.parse_args(argv)

    dest: Path = args.dest
    if dest.exists() and any(dest.iterdir()) and not args.force:
        print(f"目标目录已有文件，未覆盖：{dest}（加 --force 覆盖）", file=sys.stderr)
        return 2

    print(f"下载 {TARBALL_URL}")
    tarball = _download(TARBALL_URL)
    print(f"下载完成：{len(tarball)} bytes")

    files = _extract_core_files(tarball, dest)
    missing = [name for name in CORE_FILES if name not in files]
    if missing:
        print(f"缺少核心文件：{missing}", file=sys.stderr)
        return 1

    dest.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (dest / name).write_bytes(data)

    retrieved_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (dest / "COMMIT.txt").write_text(
        f"repo={REPO_URL}\ncommit={COMMIT}\nretrieved_at={retrieved_at}\n"
        f"license=NO LICENSE (all rights reserved)\n",
        encoding="utf-8",
    )

    manifest = _write_manifest(dest, files)
    print(f"已写入 {len(files)} 个文件到 {dest}")
    print(f"清单：{manifest}")
    print("提醒：vendor/ 已被 .gitignore 排除，不要提交第三方源码。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
