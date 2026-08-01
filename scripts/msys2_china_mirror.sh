#!/usr/bin/env bash
# 将 MSYS2 pacman 切换为国内镜像（清华 / 中科大 / 北外），加速依赖下载。
# 在 MSYS2 任意终端（建议 UCRT64）执行：
#   bash /e/tool/Prekikoeru-KM.v1.0/源码/scripts/msys2_china_mirror.sh
#
# 切换后执行：
#   pacman -Syy
#   pacman -S --needed --noconfirm mingw-w64-ucrt-x86_64-toolchain mingw-w64-ucrt-x86_64-nasm curl tar xz

set -euo pipefail

MIRROR_DIR="/etc/pacman.d"
if [[ ! -d "${MIRROR_DIR}" ]]; then
  echo "未找到 ${MIRROR_DIR}，请在 MSYS2 终端内运行本脚本。" >&2
  exit 1
fi

replace_repo_hosts() {
  local file="$1"
  [[ -f "${file}" ]] || return 0
  cp -f "${file}" "${file}.bak.$(date +%Y%m%d%H%M%S)"
  sed -i \
    -e 's#https\?://mirror\.msys2\.org/#https://mirrors.tuna.tsinghua.edu.cn/msys2/#g' \
    -e 's#https\?://repo\.msys2\.org/#https://mirrors.tuna.tsinghua.edu.cn/msys2/#g' \
    "${file}"
}

prepend_servers() {
  local file="$1"
  shift
  [[ -f "${file}" ]] || return 0
  local tmp
  tmp="$(mktemp)"
  {
    echo "## China mirrors (prepended by msys2_china_mirror.sh)"
    for server in "$@"; do
      echo "Server = ${server}"
    done
    echo
    cat "${file}"
  } > "${tmp}"
  mv "${tmp}" "${file}"
}

echo "备份并替换 mirrorlist 中的官方源地址 ..."
for file in "${MIRROR_DIR}"/mirrorlist*; do
  [[ -f "${file}" ]] || continue
  replace_repo_hosts "${file}"
  echo "  已处理：${file}"
done

for target in mirrorlist.ucrt64 mirrorlist.mingw64 mirrorlist.msys; do
  file="${MIRROR_DIR}/${target}"
  if [[ -f "${file}" ]]; then
    case "${target}" in
      mirrorlist.ucrt64)
        prepend_servers "${file}" \
          "https://mirrors.tuna.tsinghua.edu.cn/msys2/mingw/ucrt64" \
          "https://mirrors.ustc.edu.cn/msys2/mingw/ucrt64" \
          "https://mirrors.bfsu.edu.cn/msys2/mingw/ucrt64"
        ;;
      mirrorlist.mingw64)
        prepend_servers "${file}" \
          "https://mirrors.tuna.tsinghua.edu.cn/msys2/mingw/mingw64" \
          "https://mirrors.ustc.edu.cn/msys2/mingw/mingw64" \
          "https://mirrors.bfsu.edu.cn/msys2/mingw/mingw64"
        ;;
      mirrorlist.msys)
        prepend_servers "${file}" \
          'https://mirrors.tuna.tsinghua.edu.cn/msys2/msys/$arch' \
          'https://mirrors.ustc.edu.cn/msys2/msys/$arch'
        ;;
    esac
    echo "  已置顶国内源：${file}"
  fi
done

echo
echo "完成。请执行："
echo "  pacman -Syy"
echo "  pacman -S --needed --noconfirm mingw-w64-ucrt-x86_64-toolchain mingw-w64-ucrt-x86_64-nasm curl tar xz"
