# Spotify → FLAC 完整操作手册（Termux + proot Debian）

> 适用：安卓手机，从零开始部署到下载出带「歌手 / 专辑 / 封面 / 歌词」标签的 FLAC，并自动按专辑分文件夹。
> 本手册是全程踩坑后验证成功的最终版；所有命令可直接复制粘贴。
> 合规提示：仅处理你有权使用的音频（自有录音、公有领域、已购无 DRM 内容），不绕过 DRM/VIP。

---

## 〇、开始前准备

1. 从 **F-Droid 或 Termux 官网**安装 Termux（不要用应用商店旧版）。
2. 手机打开 **Clash**，切到「全局」或开 **TUN 模式**，记住 HTTP 混合端口（默认 `7890`，下文都按 7890 写，不是就替换数字）。
3. 保持代理开启、手机别锁屏，直到部署完成。

---

## 一、部署 proot Debian（一次性，约几分钟）

> 为什么用 Debian：Termux 原生环境装 spotdl 必踩三个无解的坑——`curl_cffi` 无 Android 预编译库、`pydantic-core` 要 Rust 现场编译、Python 3.14 个别包不适配。proot Debian 是标准 Linux，pip 全走预编译包，一次到位。

在 Termux 里**整块复制**下面这一条，回车后全程自动跑完：

```bash
clear && pkg install -y proot-distro && (proot-distro list | grep -q 'debian.*installed' || proot-distro install debian) && proot-distro login debian -- sh -c "export DEBIAN_FRONTEND=noninteractive && apt update -qq && apt install -y python3 python3-venv python3-pip ffmpeg curl unzip ca-certificates && python3 -m venv /root/spotdl-venv && . /root/spotdl-venv/bin/activate && pip install 'pip==25.1.1' -i https://pypi.tuna.tsinghua.edu.cn/simple && pip install spotdl -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 120 --retries 10 && (curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/root/.deno sh || true) && ln -sf /root/.deno/bin/deno /usr/local/bin/deno && echo '=== ALL DONE ===' && spotdl --version && deno --version | head -1 && ffmpeg -version | head -1"
```

**成功标志**：看到 `=== ALL DONE ===`，并打印出
```
spotdl: 4.6.0
deno: deno 2.x.x
ffmpeg: ffmpeg version ...
```

**安装期配置冲突怎么选**：
- `openssl.cnf` / 证书类 → 选 **Y**
- `sources.list`、`bash.bashrc` 等 → 选 **N**

---

## 二、把脚本放进手机

把以下两个文件放到手机目录 `/storage/emulated/0/MT2/`（用你的方式传输即可）：

| 文件 | 作用 |
|------|------|
| `ytdlp_flac.py` | 主程序：解析 Spotify 链接 → 下载 → 转 FLAC → 嵌标签/封面/歌词 → **按专辑分文件夹** |
| `organize_flac.py` | 整理工具：把已下载、散放的文件按专辑标签归类 |

> 主脚本已内置三个关键机制：先用 `yt-dlp` 搜索拿到明确视频 URL 再下载、`--extractor-args youtube:player_client=all` 绕开 403、spotdl 只负责解析不负责下载。

---

## 三、日常下载（每次用这段）

**① 进入 Debian 并激活环境**（Termux 里执行）：
```bash
proot-distro login debian -- sh -c ". /root/spotdl-venv/bin/activate && bash"
```

**② 设置代理**（看到 `root@localhost:~#` 后执行；已设过可跳过）：
```bash
export https_proxy=http://127.0.0.1:7890
export http_proxy=http://127.0.0.1:7890
```
验证是否通：`curl -sI -m 10 https://music.youtube.com | head -3`，出现 `HTTP/2 200` 即通。

**③ 下载**（链接放引号里，可放多个专辑/歌单链接）：
```bash
python3 /storage/emulated/0/MT2/ytdlp_flac.py "https://open.spotify.com/album/你的专辑ID"
```

输出位置：手机 `下载/SpotifyFLAC/`，结构为：
```
SpotifyFLAC/
└── 专辑名/
    └── 歌手 - 歌名.flac
```

歌词源（Genius/Musixmatch）超时会自动跳过，不影响音频。想完全关掉歌词加 `--no-lyrics`。

---

## 四、整理已经下载好的散文件（如需要）

FLAC 里已嵌入 `ALBUM` 标签，整理脚本**不用联网**，直接读标签归类。先在 Debian 会话里：

```bash
# 先预览，确认分类对不对
python3 /storage/emulated/0/MT2/organize_flac.py /sdcard/Download/SpotifyFLAC --dry-run

# 没问题后实际移动
python3 /storage/emulated/0/MT2/organize_flac.py /sdcard/Download/SpotifyFLAC
```

想按歌手分文件夹加 `--by-artist`。

---

## 五、常见错误与解法（排错速查）

| 现象 | 原因 | 解法 |
|------|------|------|
| Termux 原生装 spotdl 报 `curl_cffi .so not found` / `pydantic-core` 编译失败 / Python 3.14 不兼容 | Android 无对应预编译库 | 放弃原生，**走 proot Debian**（本手册路线） |
| pip 下载报 SSL `UNEXPECTED_EOF_WHILE_READING` | pythonhosted 源被干扰 | 降 `pip==25.1.1` + 清华源（部署命令已内置） |
| 歌词源/音源全部 `connect timeout` | 手机没开全局代理 | Clash 切全局/TUN，确认端口 7890 |
| 下载报 `HTTP 403 Forbidden` | YouTube 新机制要 PO Token | 脚本已用 `player_client=all` + 两步下载解决；仍失败就换住宅 IP 节点 |
| spotdl 下载报 `YT-DLP download error` | spotdl 内嵌下载器误判 yt-dlp 消息 | 脚本已绕开 spotdl 下载，只用它解析 |
| 提示 `Unable to locate package` | 软件源索引旧 | 先 `pkg update` |
| 命令行粘贴后括号被解析 / 链接字符被改坏 | 输入法干扰 | 长按整块复制，别手动打字；避免 `%(ext)s` 写法 |

---

## 六、环境与文件清单

- **运行环境**：Termux → proot-distro Debian
- **虚拟环境**：`/root/spotdl-venv`（Python 3.11 + spotdl 4.6.0 + yt-dlp + mutagen + deno + ffmpeg）
- **代理**：`http://127.0.0.1:7890`（Clash）
- **输出目录**：`/sdcard/Download/SpotifyFLAC/`
- **脚本目录**：`/storage/emulated/0/MT2/`

最终验证成功的诊断命令（保留备查）：
```bash
yt-dlp --proxy http://127.0.0.1:7890 --extractor-args youtube:player_client=all -f bestaudio -o /root/t1.m4a "ytsearch1:Emotional Billiards - Home Sick"
```
出现 `[download] 100%` 即代表链路完全正常。
