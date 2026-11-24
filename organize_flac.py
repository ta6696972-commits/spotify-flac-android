#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
organize_flac.py —— 把散在一个文件夹里的 FLAC 按「专辑」自动分文件夹。

原理：读取每个 .flac 文件里已嵌入的 ALBUM 标签，
      移动到 输出目录/专辑名/ 下。不联网、不改文件内容。

【用法】在 proot Debian、激活虚拟环境后：
    python3 organize_flac.py /sdcard/Download/SpotifyFLAC

【可选】
    --dry-run   只预览会怎么移动，不实际执行（建议先看一遍）
    --by-artist 改成按歌手分文件夹（默认按专辑）

依赖：mutagen（spotdl 已自带）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def safe_name(name: str) -> str:
    for ch in '/\\:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip() or "未知"


def read_tag(path: Path, key: str) -> str | None:
    """读取 FLAC 的某个 Vorbis Comment 标签。"""
    try:
        from mutagen.flac import FLAC
        audio = FLAC(str(path))
        values = audio.get(key)
        if values:
            return str(values[0]).strip() or None
    except Exception:  # noqa: BLE001
        pass
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="FLAC 按专辑/歌手分文件夹")
    parser.add_argument("folder", help="FLAC 所在目录")
    parser.add_argument("--dry-run", action="store_true", help="只预览不执行")
    parser.add_argument("--by-artist", action="store_true", help="按歌手分（默认按专辑）")
    args = parser.parse_args()

    root = Path(args.folder)
    if not root.is_dir():
        print(f"[错误] 目录不存在: {root}", file=sys.stderr)
        return 2

    key = "ARTIST" if args.by_artist else "ALBUM"
    label = "歌手" if args.by_artist else "专辑"

    # 只处理根目录下的 flac（跳过已在子文件夹里的）
    files = [p for p in root.glob("*.flac") if p.is_file()]
    if not files:
        print(f"没有找到 FLAC 文件: {root}")
        return 0

    moved, skipped = 0, []
    for f in files:
        tag = read_tag(f, key) or f"未识别{label}"
        target_dir = root / safe_name(tag)
        target = target_dir / f.name
        if args.dry_run:
            print(f"[预览] {f.name}  ->  {safe_name(tag)}/")
            moved += 1
            continue
        if target_dir == root.parent:
            skipped.append(f.name)
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        # 同名文件避免覆盖：加序号
        if target.exists() and target != f:
            stem, suffix = f.stem, f.suffix
            n = 2
            while target.exists():
                target = target_dir / f"{stem} ({n}){suffix}"
                n += 1
        f.rename(target)
        print(f"[移动] {f.name}  ->  {safe_name(tag)}/")
        moved += 1

    print(f"\n完成：{moved} 个文件已按{label}归类" + ("（预览模式，未实际移动）" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
