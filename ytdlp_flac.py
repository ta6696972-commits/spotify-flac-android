#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ytdlp_flac.py —— 可靠版：Spotify 链接 → FLAC（带歌手/专辑/封面/歌词）

【为什么需要这个脚本】
spotdl 自带的下载逻辑在部分环境会把 yt-dlp 的提示消息误判为致命错误，
导致明明 yt-dlp 能下载却报 "YT-DLP download error"。本脚本绕开这一步：
  1) 用 spotdl 只做「解析 Spotify 链接 → 曲目元数据」（这步一直稳定）
  2) 用 yt-dlp 直接搜索并下载音频（已验证这条链路可正常下载）
  3) 用 ffmpeg 转成 FLAC，并用 mutagen 嵌入 标题/歌手/专辑/年份/曲目号/封面/歌词

【v6 进度条回归 + 双保险下载】
  - 下载阶段真实单行进度条：[4/10] [████░░░░] 45.0% 1.3M/s 26s，\\r 原地刷新不刷屏
    （进度重定向到临时文件读取，而非 Popen 管道——管道读 \\r 进度会阻塞导致"连不上"）
  - 双保险：带进度下载失败时自动降级到第一版 capture_output 安静模式重试一次，保证能下载
  - 旋转 spinner（| / - \\）按阶段显示「搜索/歌词/封面/写入」，各阶段独立超时
  - 歌词 10s / 封面 12s / ffmpeg 120s 硬超时，杜绝「写入标签卡死」
  - 断点续传：重跑时自动跳过已完整下载（≥200KB）的曲目，只补下缺的；残缺文件自动清除重下
  - 解析与第一版一致：主线程直接调用、不设短超时（慢网络需 1-2 分钟，强行超时会失败）
  - 链接自动清洗中英文引号；非交互环境降级为简洁文本
  - 【重要】解析阶段需网络能通 Spotify API：Clash 开全局/TUN；失败会提示换节点或设代理

【运行前提】在 proot Debian 里、且已装好 spotdl / yt-dlp / ffmpeg / deno。
    proot-distro login debian -- sh -c ". /root/spotdl-venv/bin/activate && bash"

【用法】
    # 先设代理（按你手机的 Clash 端口，一般是 7890）
    export https_proxy=http://127.0.0.1:7890
    export http_proxy=http://127.0.0.1:7890

    # 交互
    python3 ytdlp_flac.py

    # 或直接给链接
    python3 ytdlp_flac.py "https://open.spotify.com/album/11nEgSgO36uvYj8YwwzMs8"

【可选参数】
    --output DIR        输出目录，默认 /sdcard/Download/SpotifyFLAC
    --proxy URL         代理，默认读 https_proxy 环境变量
    --audio PROVIDER    音源，默认 youtube（也可试 youtube-music）
    --no-lyrics         跳过歌词（歌词源在国外、超时快，默认忽略失败）
    --simple            关闭动画/进度条，用简洁逐行输出（日志友好）

合规提示：请仅处理你有权使用的音频（自有录音、公有领域、已购无 DRM 内容）。
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Optional

# ---- 依赖自检 ----
try:
    from spotdl.utils.search import get_simple_songs
    from spotdl.providers.lyrics.genius import Genius
    from spotdl.providers.lyrics.musixmatch import MusixMatch
    from spotdl.providers.lyrics.azlyrics import AzLyrics
    from spotdl.providers.lyrics.synced import Synced
except Exception as exc:  # noqa: BLE001
    print(f"[致命] 无法导入 spotdl，请先在虚拟环境里运行：\n"
          f"  . /root/spotdl-venv/bin/activate\n原始错误: {exc}", file=sys.stderr)
    sys.exit(2)

LYRICS_PROVIDERS = [Genius, MusixMatch, AzLyrics, Synced]

# 是否启用动画（非 TTY 或 --simple 时关闭）
_ANIM = True
BAR_W = 10    # 进度条格子数（短，适配手机窄屏）


def interactive() -> bool:
    return _ANIM and sys.stdout.isatty()


def term_cols() -> int:
    """拿到终端真实列数，失败兜底 40；留 1 列避免折行。"""
    try:
        return max(24, shutil.get_terminal_size(fallback=(40, 24)).columns - 1)
    except Exception:  # noqa: BLE001
        return 39


def log(msg: str) -> None:
    print(msg, flush=True)


def clear_line() -> None:
    """\r 回到行首 + ANSI 清除到行尾，避免残影；不填充空格（防止折行）。"""
    if interactive():
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()


def trunc(s: str, n: Optional[int] = None) -> str:
    if n is None:
        n = max(20, term_cols() - 8)
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


class Spinner:
    """后台线程旋转指示符，用 \r 原地刷新；非交互环境自动降级为静态文本。"""

    FRAMES = ("|", "/", "-", "\\")

    def __init__(self, label: str, delay: float = 0.12) -> None:
        self.label = label
        self.delay = delay
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self, label: Optional[str] = None) -> "Spinner":
        if label:
            self.label = label
        if not interactive():
            return self
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        i = 0
        label = trunc(self.label)
        while self._running:
            # \r + ANSI 清行：只占一行，label 已按终端宽度截断，绝不折行
            sys.stdout.write(f"\r\033[K{self.FRAMES[i % len(self.FRAMES)]} {label}")
            sys.stdout.flush()
            time.sleep(self.delay)
            i += 1

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=0.5)
        clear_line()

    def __enter__(self) -> "Spinner":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def _short_size(s: str) -> str:
    """压缩速度/ETA 文本，如 '107.28KiB/s'→'107K/s'、'00:26'→'26s'。"""
    s = (s or "").strip()
    if not s or s == "NA" or "Unknown" in s:
        return ""
    s = s.replace(" ", "")
    for unit, short in (("KiB/s", "K/s"), ("MiB/s", "M/s"), ("GiB/s", "G/s"), ("B/s", "B/s")):
        if unit in s:
            num = s.split(unit)[0]
            # 数字压到最多 4 字符（107.28 -> 107.，保留精度但控宽）
            try:
                v = float(num)
                num = f"{v:.0f}" if v >= 100 else f"{v:.1f}"
            except ValueError:
                num = num[:4]
            return num + short
    if ":" in s:  # ETA 00:26 -> 26s
        parts = s.split(":")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{int(parts[1]) + 60 * int(parts[0])}s"
    return s[:6]


def render_bar(idx: int, total: int, pct: float, speed: str, eta: str) -> None:
    """单行原地更新进度条，宽度按终端自适应，绝不折行。

    形如：[4/10] [████░░░░] 45.0% 1.3M/s 00:03
    """
    if not interactive():
        return
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        pct = 0.0
    pct = max(0.0, min(100.0, pct))

    cols = term_cols()
    sp = _short_size(speed)
    et = _short_size(eta)
    meta = " ".join(x for x in (sp, et) if x)
    head = f"[{idx}/{total}] "
    pstr = f"{pct:5.1f}%"
    tail = (" " + meta) if meta else ""

    # 固定部分 = head + "[" + "]" + 1空格 + 百分比 + tail
    fixed = len(head) + 2 + 1 + len(pstr) + len(tail)
    bar_w = max(3, min(BAR_W, cols - fixed))
    filled = int(round(bar_w * pct / 100.0))
    bar = "█" * filled + "░" * (bar_w - filled)

    line = f"{head}[{bar}] {pstr}{tail}"
    # 兜底截断，确保任何情况下都不超过终端宽度导致折行
    if len(line) > cols:
        line = line[:cols]
    sys.stdout.write("\r\033[K" + line)
    sys.stdout.flush()


@contextlib.contextmanager
def quiet() -> None:
    """静默第三方库日志。只禁用 logging（全局、快速），不重定向 stdout/stderr，
    否则会连同 spinner 后台线程的输出一起吞掉，造成"卡死"假象。"""
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


class _Timeout:
    """用守护线程给阻塞调用加硬超时；超时后函数返回 fallback，不阻塞主流程。"""

    def __init__(self, seconds: float, fallback=None) -> None:
        self.seconds = seconds
        self.fallback = fallback

    def __call__(self, fn, *args, **kwargs):
        box = {}

        def worker() -> None:
            try:
                box["v"] = fn(*args, **kwargs)
            except Exception:  # noqa: BLE001
                box["v"] = self.fallback
            finally:
                box.setdefault("v", self.fallback)

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(self.seconds)
        return box.get("v", self.fallback)


def safe_name(name: str) -> str:
    """去掉文件名非法字符。"""
    for ch in '/\\:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip() or "track"


def run(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, **kwargs)


def have(bin_name: str) -> bool:
    from shutil import which
    return which(bin_name) is not None


def init_spotify() -> None:
    """初始化 spotdl 的 Spotify 客户端（用内置凭据，免申请）。"""
    from spotdl.utils.spotify import SpotifyClient
    from spotdl.utils.config import SPOTIFY_OPTIONS
    SpotifyClient.init(
        client_id=SPOTIFY_OPTIONS["client_id"],
        client_secret=SPOTIFY_OPTIONS["client_secret"],
        use_official_api=False,
        user_auth=False,
        max_retries=3,
    )


def clean_link(raw: str) -> str:
    """清洗粘贴进来的链接：去首尾空白与中英文引号，避免输入法污染导致解析失败。"""
    s = (raw or "").strip()
    quotes = '"\'`“”‘’'
    while s and s[0] in quotes:
        s = s[1:].strip()
    while s and s[-1] in quotes:
        s = s[:-1].strip()
    return s


def parse_songs(links: List[str]) -> list:
    """用 spotdl 解析 Spotify 链接为曲目列表（只解析、不下载）。

    【关键】与第一版一致：直接在主线程调用 init_spotify() + get_simple_songs()，
    不套 _Timeout 守护线程、不加短超时。慢网络下解析可能需要 1-2 分钟，
    之前强行加 50s 超时把它掐断，导致三次重试全部失败。
    现在只要网络通，一次运行就能解析成功——耐心等即可。
    """
    log("[解析] 正在解析链接，网络慢时可能需要 1-2 分钟，请耐心等待…")
    init_spotify()
    songs = get_simple_songs(links)
    log(f"[解析] 共识别到 {len(songs)} 首歌")
    return songs


def _do_search_lyrics(song) -> Optional[str]:
    with quiet():
        for provider_cls in LYRICS_PROVIDERS:
            try:
                lyrics = provider_cls().get_lyrics(song.name, song.artists)
                if lyrics:
                    return lyrics
            except Exception:  # noqa: BLE001 - 歌词失败无所谓
                continue
    return None


def search_lyrics(song, timeout: float = 10.0) -> Optional[str]:
    """尝试多个歌词源；整体硬超时 10 秒，杜绝卡在歌词阶段。"""
    return _Timeout(timeout, None)(_do_search_lyrics, song)


def search_video_url(query: str, proxy: Optional[str]) -> Optional[str]:
    """先搜索拿到明确的视频 URL（与手动下载成功的路径保持一致）。"""
    cmd = ["yt-dlp", "--no-playlist", "--print", "%(webpage_url)s", "--no-warnings"]
    if proxy:
        cmd += ["--proxy", proxy]
    cmd.append(f"ytsearch1:{query}")
    try:
        result = run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("http"):
            return line
    return None


def _kill_after(proc: subprocess.Popen, seconds: int) -> None:
    """兜底超时：到点仍未结束就杀掉，防止卡死。"""
    time.sleep(seconds)
    if proc.poll() is None:
        proc.kill()


def download_audio(song, tmp_dir: Path, proxy: Optional[str], audio: str,
                   idx: int = 1, total: int = 1) -> Optional[Path]:
    """搜索→拿明确 URL→下载音频，实时渲染单行进度条 + 百分比。

    关键设计（避免"连不上"）：
      - yt-dlp 进度用 \\r 刷新；若用 Popen 管道逐行读，迭代器等 \\n 会阻塞缓冲区 → 假死。
        改为把进度输出重定向到【临时文件】，主线程轮询读文件解析，绝不阻塞子进程。
      - 下载命令保留第一版验证成功的 --print after_move:filepath + player_client=all。
      - 若带进度下载失败，自动降级到第一版 capture_output 安静模式重试一次（双保险）。
    """
    query = f"{song.artist} - {song.name}"
    log(f"[下载 {idx}/{total}] {query}")

    # 第一步：搜索拿明确 URL
    with Spinner(f"搜索音源：{trunc(query, 32)}"):
        url = search_video_url(query, proxy)
    if not url:
        log(f"  ✗ 搜索失败，跳过：{trunc(query)}")
        return None

    out_tmpl = str(tmp_dir / "audio.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f", "bestaudio/best",
        "--extractor-args", "youtube:player_client=all",
        "--print", "after_move:filepath",
        "--progress",
        "--progress-template",
        "download:DL|%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
        "-o", out_tmpl,
        "--no-warnings",
    ]
    if proxy:
        cmd += ["--proxy", proxy]
    cmd.append(url)

    # 进度重定向到文件（不是管道），主线程轮询读取渲染，避免 \\r 进度阻塞
    prog_file = tmp_dir / "yt_progress.log"
    printed: List[str] = []
    ret = -1
    try:
        with open(prog_file, "w", encoding="utf-8", errors="replace") as pf:
            proc = subprocess.Popen(
                cmd, stdout=pf, stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            threading.Thread(target=_kill_after, args=(proc, 300), daemon=True).start()
            pos = 0
            pct_printed = -10.0
            while proc.poll() is None:
                try:
                    with open(prog_file, "r", encoding="utf-8", errors="replace") as rf:
                        rf.seek(pos)
                        chunk = rf.read()
                        pos = rf.tell()
                    for line in chunk.splitlines():
                        line = line.strip()
                        if line.startswith("DL|"):
                            parts = line.split("|")
                            ps = parts[1].strip() if len(parts) > 1 else "0%"
                            sp = parts[2].strip() if len(parts) > 2 else ""
                            et = parts[3].strip() if len(parts) > 3 else ""
                            try:
                                p = float(ps.replace("%", "").strip())
                            except ValueError:
                                p = 0.0
                            if interactive():
                                render_bar(idx, total, p, sp, et)
                            elif p - pct_printed >= 25:
                                pct_printed = p
                                log(f"  … 下载 {int(p)}%")
                        elif line.startswith("http") or line.startswith("/"):
                            printed.append(line)
                except OSError:
                    pass
                time.sleep(0.15)
            ret = proc.returncode
            # 读完残留
            try:
                with open(prog_file, "r", encoding="utf-8", errors="replace") as rf:
                    rf.seek(pos)
                    tail = rf.read()
                for line in tail.splitlines():
                    line = line.strip()
                    if line.startswith("DL|"):
                        parts = line.split("|")
                        try:
                            p = float(parts[1].strip().replace("%", "")) if len(parts) > 1 else 0.0
                        except ValueError:
                            p = 0.0
                        sp = parts[2].strip() if len(parts) > 2 else ""
                        et = parts[3].strip() if len(parts) > 3 else ""
                        if interactive():
                            render_bar(idx, total, p, sp, et)
                    elif line.startswith("http") or line.startswith("/"):
                        printed.append(line)
            except OSError:
                pass
    except OSError as exc:  # noqa: BLE001
        log(f"  ✗ 下载启动失败：{exc}")
        return None
    finally:
        clear_line()

    if ret == 0:
        for candidate in reversed(printed):
            p = Path(candidate)
            if p.exists():
                return p
        files = [p for p in tmp_dir.iterdir() if p.is_file() and p.name.startswith("audio.")]
        if files:
            return files[0]

    # 降级：带进度下载失败 → 第一版安静模式重试一次（双保险，保证能下载）
    log("  → 带进度下载未成功，切换安静模式重试一次…")
    cmd2 = [
        "yt-dlp", "--no-playlist", "-f", "bestaudio/best",
        "--extractor-args", "youtube:player_client=all",
        "--print", "after_move:filepath", "-o", out_tmpl, "--no-warnings",
    ]
    if proxy:
        cmd2 += ["--proxy", proxy]
    cmd2.append(url)
    try:
        result = run(cmd2, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        log("  ✗ 安静模式下载超时")
        return None
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        log(f"  ✗ 下载失败：{tail[-1:] if tail else '未知错误'}")
        return None
    for candidate in reversed([ln.strip() for ln in result.stdout.splitlines() if ln.strip()]):
        p = Path(candidate)
        if p.exists():
            return p
    files = [p for p in tmp_dir.iterdir() if p.is_file() and p.name.startswith("audio.")]
    return files[0] if files else None


def embed_flac(src: Path, dst: Path, song, lyrics: Optional[str], cover: Optional[Path]) -> bool:
    """用 ffmpeg 转 FLAC，再用 mutagen 嵌入元数据/封面/歌词。"""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src)]
    if cover and cover.exists():
        cmd += ["-i", str(cover), "-map", "0:a:0", "-map", "1:v:0",
                "-c:v", "copy", "-disposition:v:0", "attached_pic"]
    cmd += ["-c:a", "flac", str(dst)]
    try:
        result = run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        log("  ✗ ffmpeg 转换超时")
        return False
    if result.returncode != 0 or not dst.exists():
        log(f"  ✗ ffmpeg 转换失败：{result.stderr.strip().splitlines()[-1:]}")
        return False

    try:
        from mutagen.flac import FLAC, Picture
        audio = FLAC(str(dst))
        audio["TITLE"] = song.name
        audio["ARTIST"] = song.artist
        audio["ALBUM"] = song.album_name
        if getattr(song, "album_artist", None):
            audio["ALBUMARTIST"] = song.album_artist
        if getattr(song, "year", None):
            audio["DATE"] = str(song.year)
        if getattr(song, "track_number", None):
            audio["TRACKNUMBER"] = str(song.track_number)
        if lyrics:
            audio["LYRICS"] = lyrics
            audio["UNSYNCEDLYRICS"] = lyrics
        if cover and cover.exists():
            pic = Picture()
            pic.type = 3  # front cover
            pic.mime = "image/jpeg"
            with open(cover, "rb") as fh:
                pic.data = fh.read()
            audio.clear_pictures()
            audio.add_picture(pic)
        audio.save()
    except Exception as exc:  # noqa: BLE001
        log(f"  ! 元数据写入失败（音频仍可用）：{exc}")

    return True


def download_cover(song, tmp_dir: Path, proxy: Optional[str]) -> Optional[Path]:
    """下载专辑封面到临时文件（失败返回 None，不影响主流程）。硬超时 12 秒。"""
    url = getattr(song, "cover_url", None)
    if not url:
        return None

    def _get() -> Optional[Path]:
        try:
            import requests
            proxies = {"http": proxy, "https": proxy} if proxy else None
            resp = requests.get(url, timeout=(6, 10), proxies=proxies)
            resp.raise_for_status()
            cover = tmp_dir / "cover.jpg"
            cover.write_bytes(resp.content)
            return cover
        except Exception:  # noqa: BLE001
            return None

    return _Timeout(12, None)(_get)


def main() -> int:
    global _ANIM
    parser = argparse.ArgumentParser(description="Spotify 链接 → FLAC（yt-dlp 直连可靠版）")
    parser.add_argument("links", nargs="*", help="Spotify 歌单/专辑/单曲链接")
    parser.add_argument("--output", default="/sdcard/Download/SpotifyFLAC", help="输出目录")
    parser.add_argument("--proxy", default=None, help="代理地址，如 http://127.0.0.1:7890")
    parser.add_argument("--audio", default="youtube", help="保留参数（本脚本固定用 yt-dlp 搜索）")
    parser.add_argument("--no-lyrics", action="store_true", help="跳过歌词")
    parser.add_argument("--simple", action="store_true", help="关闭动画/进度条，用简洁逐行输出")
    args = parser.parse_args()
    _ANIM = not args.simple

    for bin_name in ("yt-dlp", "ffmpeg"):
        if not have(bin_name):
            log(f"[致命] 缺少 {bin_name}，请先安装。")
            return 2

    proxy = args.proxy or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY")
    if proxy:
        os.environ["https_proxy"] = proxy
        os.environ["http_proxy"] = proxy
        log(f"[代理] {proxy}")

    links = [clean_link(ln) for ln in args.links]
    links = [ln for ln in links if ln]
    if not links:
        log("请粘贴 Spotify 链接（专辑/歌单/单曲），一行一个，空行结束：")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                break
            parts = line.replace(",", " ").split()
            links.extend(clean_link(p) for p in parts)
        links = [ln for ln in links if ln]
    if not links:
        log("[提示] 没有输入任何链接，退出。")
        return 1

    log("=" * 46)
    log("  脚本已启动，正在解析链接，请稍候…")
    log("=" * 46)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        songs = parse_songs(links)
    except Exception as exc:  # noqa: BLE001
        log(f"[致命] 解析失败：{exc}")
        log("[提示] 解析走 Spotify API，请确认 Clash 已开全局/TUN 且网络稳定；")
        log("        若一直失败，在 Clash 里换一个节点，或手动 export https_proxy 后重试。")
        return 3

    # ---- 断点续传：先计算每首歌的目标路径，识别已完成 / 待下载 ----
    min_size = 200 * 1024  # 小于 200KB 视为残缺（下载一半），需重新下载
    plan = []
    for song in songs:
        album_dir = out_dir / safe_name(getattr(song, "album_name", None) or "未知专辑")
        dst = album_dir / f"{safe_name(song.artist)} - {safe_name(song.name)}.flac"
        complete = dst.exists() and dst.stat().st_size >= min_size
        plan.append((song, dst, complete))

    done_n = sum(1 for _, _, c in plan if c)
    pending_n = len(plan) - done_n
    if done_n:
        log(f"[续传] 已完成 {done_n} 首（跳过），待下载 {pending_n} 首")
    else:
        log(f"[下载] 共 {pending_n} 首")

    ok, fail = done_n, []
    total = len(plan)
    for idx, (song, dst, complete) in enumerate(plan, 1):
        if complete:
            log(f"\n=== [{idx}/{total}] {song.name} ===")
            log("  → 已存在，跳过")
            continue

        log(f"\n=== [{idx}/{total}] {song.name} ===")
        dst.parent.mkdir(parents=True, exist_ok=True)
        # 残缺文件先删掉，避免 ffmpeg 覆盖失败或误判
        if dst.exists():
            try:
                dst.unlink()
                log("  → 检测到残缺文件，已清除，重新下载")
            except OSError:
                pass

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            audio = download_audio(song, tmp, proxy, args.audio, idx, total)
            if audio is None:
                fail.append(song.name)
                continue

            with Spinner("  获取歌词…"):
                lyrics = None if args.no_lyrics else search_lyrics(song)
            with Spinner("  获取封面…"):
                cover = download_cover(song, tmp, proxy)
            with Spinner("  写入标签…"):
                done = embed_flac(audio, dst, song, lyrics, cover)
            if done:
                log(f"  ✓ 完成 [{idx}/{total}]：{dst}")
                ok += 1
            else:
                fail.append(song.name)

    log(f"\n===== 完成：成功 {ok}/{total}，失败 {len(fail)} =====")
    if fail:
        log("失败列表：")
        for name in fail:
            log(f"  - {name}")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
