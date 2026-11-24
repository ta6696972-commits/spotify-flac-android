#!/bin/bash
# ============================================================
# Termux 上通过 proot-distro(Debian) 部署 spotdl 的一键脚本
#
# 为什么需要它：
#   Termux 原生环境里，spotdl 4.6 依赖 spotipyfree -> spotapi -> curl_cffi，
#   而 curl_cffi / tls-client 都没有 Android 版预编译库，加载必失败；
#   降级到 4.4.0 又因它是 Python 3.14 而装不上。
#   proot-distro 里的 Debian 是标准 Linux，pip 全部走预编译 wheel，
#   没有这些 Android 原生库问题。
#
# 用法：在 Termux 里执行： bash termux_proot_setup.sh
# ============================================================
set -e

echo "===== 1/4 安装 proot-distro ====="
pkg install -y proot-distro

echo "===== 2/4 安装 Debian 根系统（首次约几百 MB，耐心等）====="
if proot-distro list | grep -q "debian.*installed"; then
    echo "Debian 已安装，跳过"
else
    proot-distro install debian
fi

echo "===== 3/4 在 Debian 里装 Python / ffmpeg / spotdl ====="
proot-distro login debian -- sh -c '
    export DEBIAN_FRONTEND=noninteractive
    apt update -y
    apt install -y python3 python3-pip python3-venv ffmpeg ca-certificates
    # Debian 是标准 glibc Linux，spotdl 4.6 的依赖（含 curl_cffi）都有
    # manylinux aarch64 预编译包，直接装最新版即可，无 Termux 的原生库问题
    python3 -m venv /root/spotdl-venv
    . /root/spotdl-venv/bin/activate
    pip install --upgrade pip
    pip install spotdl
    echo "--- 验证 ---"
    python3 -c "import spotdl; print(\"spotdl OK\", spotdl.__version__)"
'

echo "===== 4/4 安装 deno（YouTube 音源需要）====="
proot-distro login debian -- sh -c '
    if ! command -v deno >/dev/null 2>&1; then
        apt install -y curl unzip || true
        curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/root/.deno sh
        ln -sf /root/.deno/bin/deno /usr/local/bin/deno
    fi
    deno --version | head -1
'

cat <<'EOF'

============================================================
 部署完成！以后这样用（都在 Termux 里执行）：

 1) 把 termux_flac.py 复制进 Debian：
    proot-distro login debian --shared-termux-home -- sh -c \
      "cp /data/data/com.termux/files/home/termux_flac.py /root/ 2>/dev/null || true"

    如果上面没复制到，先在 Termux 里手动：
    cp /storage/emulated/0/MT2/termux_flac.py $HOME/termux_flac.py
    然后： proot-distro login debian --shared-termux-home
           （进到 Debian 后）cp /data/data/com.termux/files/home/termux_flac.py /root/

 2) 进入 Debian 并激活环境：
    proot-distro login debian
    . /root/spotdl-venv/bin/activate

 3) 下载（链接放引号里）：
    python3 /root/termux_flac.py "你的歌单链接"

 提示：Debian 里的 /sdcard 通常已挂载，可直接输出到手机存储。
============================================================
EOF
