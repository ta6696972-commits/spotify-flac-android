# Spotify FLAC Downloader for Android

> 在安卓手机上，用 **Termux + proot Debian** 把 Spotify 链接解析并下载为 **FLAC**，自动嵌入歌手 / 专辑 / 封面 / 歌词等元数据。

A Spotify-link → FLAC downloader that runs on Android via Termux + proot Debian. Uses `spotdl` for metadata parsing, `yt-dlp` for audio acquisition, and `ffmpeg` + `mutagen` for FLAC conversion and tagging.

---

## ⚠️ 法律与合规声明（务必先读）

- 本项目**仅用于个人合法用途**：备份你已购买的无 DRM 内容、公有领域录音、或你自己拥有版权的音频。
- 项目**不破解、不绕过**任何 DRM、VIP 或付费墙，也不提供任何绕过手段。
- 通过 Spotify 链接仅获取**曲目元数据**（歌名、歌手、专辑、封面地址）；音频本体来自公开的第三方音源，由使用者自行确保其使用行为符合当地法律。
- 请勿将本项目用于侵犯他人版权。由此产生的一切责任由使用者自行承担。

---

## 特性

- **安卓可跑**：不需要 root，Termux + proot Debian 即可，输出到手机共享存储。
- **FLAC 无损**：`ffmpeg` 转码，保留原始音频信息。
- **完整元数据**：写入标题、歌手、专辑、专辑歌手、年份、曲目号、封面、同步/非同步歌词。
- **真实进度显示**：单行进度条 + 百分比 + 速度 + 剩余时间，自适应手机窄屏不折行。
- **断点续传**：重跑同一链接时跳过已完成曲目，只补下缺的；下载一半的残缺文件自动识别重下。
- **稳定下载**：带进度下载失败时自动降级到安静模式重试；解析阶段主线程直接调用，不设短超时。
- **健壮性**：歌词 / 封面 / 转码各有硬超时，避免卡死；链接自动清洗输入法带入的引号。
- **按专辑归档**：下载即按 `专辑名/歌手 - 歌名.flac` 存放，另附整理脚本收拾历史散文件。

## 工作原理

```
Spotify 链接
   │
   ├─ spotdl  ── 只解析元数据（歌名/歌手/专辑/封面），不下载
   │
   ├─ yt-dlp  ── 用 "歌手 - 歌名" 搜索公开音源并下载
   │              （player_client=all 应对 403；进度重定向到文件读取）
   │
   ├─ ffmpeg  ── 转码为 FLAC，附加封面
   │
   └─ mutagen ─ 写入 Vorbis Comment 标签 + 歌词 + 封面
```

> 为什么不用 spotdl 自带的下载？它在本环境会把 yt-dlp 的提示误判为致命错误（`YT-DLP download error`）。本项目绕开其下载逻辑，只用解析能力。

## 项目主要文件

| 文件 | 角色 | 说明 |
|------|------|------|
| **`ytdlp_flac.py`** | ⭐ 核心 | 主程序。解析 → 下载 → 转 FLAC → 嵌标签/歌词/封面 → 按专辑归档，含进度条、断点续传、超时保护 |
| **`organize_flac.py`** | ⭐ 辅助 | 把散落在单一目录的 FLAC 按 `ALBUM` 标签归入 `专辑名/`，支持 `--dry-run` 预览、`--by-artist` 按歌手分 |
| **`termux_proot_setup.sh`** | 部署参考 | 在 Termux 里用 proot-distro 装 Debian / Python / ffmpeg / spotdl / deno 的一键脚本（建议按下方"快速开始"的验证命令部署） |
| **`Termux_FLAC下载_完整操作手册.md`** | 文档 | 从零开始的完整分步指令、排错速查表、长期记忆要点 |
| `termux_flac.py` | 历史 | 早期 Termux 原生版；原生环境依赖链不兼容，已由 proot 方案取代 |
| `termux_install.sh` / `termux_daemon.py` | 历史 | 原生安装脚本 / 剪贴板监听守护实验脚本 |
| `spotify_to_flac.py` / `spotify_to_flac_app.py` | 历史 | 早期 CLI 版与 Kivy GUI 版 |
| `buildozer.spec` / `build_apk.sh` / `run_gui_desktop.sh` | 历史 | APK 打包脚手架（未实际产出可用 APK）与桌面启动脚本 |

> 实际日常使用只需要 **`ytdlp_flac.py`** 和 **`organize_flac.py`** 两个文件。

## 快速开始

### 1. 准备

- 安装 [Termux](https://f-droid.org/packages/com.termux/)（F-Droid 版）。
- 手机上的 Clash 开启全局 / TUN 模式（本地代理端口通常为 `7890`）。
- 下载本项目文件到手机 `/storage/emulated/0/MT2/`（或任意目录）。

### 2. 部署 Debian 环境（仅一次）

在 Termux 中整块复制以下命令回车，看到 `=== ALL DONE ===` 即成功。配置冲突时：证书类选 `Y`，`sources.list` / `bash.bashrc` 选 `N`。

```bash
clear && pkg install -y proot-distro && (proot-distro list | grep -q 'debian.*installed' || proot-distro install debian) && proot-distro login debian -- sh -c "export DEBIAN_FRONTEND=noninteractive && apt update -qq && apt install -y python3 python3-venv python3-pip ffmpeg curl unzip ca-certificates && python3 -m venv /root/spotdl-venv && . /root/spotdl-venv/bin/activate && pip install 'pip==25.1.1' -i https://pypi.tuna.tsinghua.edu.cn/simple && pip install spotdl -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 120 --retries 10 && (curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/root/.deno sh || true) && ln -sf /root/.deno/bin/deno /usr/local/bin/deno && echo '=== ALL DONE ===' && spotdl --version && deno --version | head -1 && ffmpeg -version | head -1"
```

### 3. 下载

```bash
# 进入环境
proot-distro login debian -- sh -c ". /root/spotdl-venv/bin/activate && bash"

# 设置代理（按你的 Clash 端口）
export https_proxy=http://127.0.0.1:7890 && export http_proxy=http://127.0.0.1:7890

# 下载（链接放引号里）
python3 /storage/emulated/0/MT2/ytdlp_flac.py "https://open.spotify.com/album/你的专辑ID"
```

输出位置：`/sdcard/Download/SpotifyFLAC/专辑名/歌手 - 歌名.flac`

常用参数：

```bash
python3 ytdlp_flac.py "链接" --no-lyrics      # 不获取歌词
python3 ytdlp_flac.py "链接" --simple         # 关闭动画，简洁逐行输出
python3 ytdlp_flac.py "链接" --output DIR     # 指定输出目录
```

看到 `[解析] 正在解析链接…` 后请耐心等待 1–2 分钟——解析走 Spotify API，慢网络下不要中断。

### 4. 整理历史散文件（可选）

```bash
python3 /storage/emulated/0/MT2/organize_flac.py /sdcard/Download/SpotifyFLAC --dry-run   # 先预览
python3 /storage/emulated/0/MT2/organize_flac.py /sdcard/Download/SpotifyFLAC             # 实际移动
```

## 常见问题

| 现象 | 处理 |
|------|------|
| 解析一直失败 | Clash 未开全局 / TUN；或换节点；或确认 `https_proxy` 已设置 |
| 下载报 403 | 脚本已用 `player_client=all` + 双保险降级；仍失败请换住宅 IP 节点 |
| 进度条卡住 / 连不上 | 已改为文件重定向读进度；若仍异常，加 `--simple` 走简洁模式 |
| 全部 connect timeout | 音源站不可达，检查代理是否真正全局生效 |
| `Unable to locate package` | 先 `pkg update` / `apt update` |
| Termux 原生装 spotdl 失败 | 属预期：`curl_cffi` 无 Android 库、需 Rust、Python 3.14 不兼容——必须用 proot Debian |

## 依赖

- [spotdl](https://github.com/spotDL/spotify-downloader) — Spotify 元数据解析
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — 音源搜索与下载
- [ffmpeg](https://ffmpeg.org/) — 音频转码
- [mutagen](https://github.com/quodlibet/mutagen) — FLAC 标签写入
- [deno](https://deno.com/) — YouTube JS challenge 求解

## 许可

本项目仅供个人学习与合法用途。项目本身代码可自由使用；所依赖的第三方库遵循各自许可证。音频内容的版权归各权利人所有，请勿用于侵权。

---

<sub>提示：日常使用只需 `ytdlp_flac.py` 与 `organize_flac.py`；其余文件为历史方案与部署参考，可按需取舍。</sub>
