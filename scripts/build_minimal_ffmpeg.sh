#!/usr/bin/env bash
# 在 MSYS2 MINGW64 / UCRT64 终端执行（先 cd 到项目「源码」目录）：
#   cd /e/tool/ChitoseExtract\ v1.0/源码
#   bash scripts/build_minimal_ffmpeg.sh
#
# 也可用绝对路径（任意当前目录均可）：
#   bash /e/tool/ChitoseExtract\ v1.0/源码/scripts/build_minimal_ffmpeg.sh
#
# 依赖示例（MINGW64）：
#   pacman -S --needed mingw-w64-x86_64-gcc mingw-w64-x86_64-nasm mingw-w64-x86_64-yasm \
#            mingw-w64-x86_64-make mingw-w64-x86_64-pkg-config curl tar xz
# 依赖示例（UCRT64）：
#   pacman -S --needed mingw-w64-ucrt-x86_64-gcc mingw-w64-ucrt-x86_64-nasm \
#            mingw-w64-ucrt-x86_64-yasm mingw-w64-ucrt-x86_64-make \
#            mingw-w64-ucrt-x86_64-pkg-config curl tar xz

set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
if [[ ! -f "${SCRIPT_PATH}" ]]; then
  echo "找不到脚本文件：${SCRIPT_PATH}" >&2
  echo "请先进入项目目录，或使用绝对路径调用本脚本。" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${SCRIPT_PATH}")/.." && pwd)"
BUILD_ROOT="${ROOT}/_build"
OUT_DIR="${ROOT}/ffmpeg-minimal"
FFMPEG_VERSION="${FFMPEG_VERSION:-7.1.1}"
TARBALL="ffmpeg-${FFMPEG_VERSION}.tar.xz"
SRC_DIR="${BUILD_ROOT}/ffmpeg-${FFMPEG_VERSION}"

case "${MSYSTEM:-}" in
  MINGW64|UCRT64) ;;
  *)
    echo "当前 MSYSTEM=${MSYSTEM:-<空>}，请在 MSYS2 MINGW64 或 UCRT64 终端中运行。" >&2
    echo "开始菜单 -> MSYS2 MINGW64  或  MSYS2 UCRT64" >&2
    exit 1
    ;;
esac

if [[ ! -f "${ROOT}/scripts/ffmpeg_minimal_configure.sh" ]]; then
  echo "项目根目录解析错误：${ROOT}" >&2
  echo "请确认从 ChitoseExtract 的「源码」目录调用本脚本。" >&2
  exit 1
fi

if [[ "${MSYSTEM}" == UCRT64 ]]; then
  PKG_PREFIX='mingw-w64-ucrt-x86_64'
else
  PKG_PREFIX='mingw-w64-x86_64'
fi

resolve_make() {
  if command -v make >/dev/null 2>&1; then
    command -v make
    return 0
  fi
  if command -v mingw32-make >/dev/null 2>&1; then
    command -v mingw32-make
    return 0
  fi
  return 1
}

for tool in gcc nasm pkg-config curl; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    echo "缺少工具：${tool}（MSYSTEM=${MSYSTEM}）" >&2
    case "${tool}" in
      gcc) pkg="${PKG_PREFIX}-gcc" ;;
      nasm) pkg="${PKG_PREFIX}-nasm" ;;
      pkg-config) pkg="${PKG_PREFIX}-pkgconf" ;;
      curl) pkg="curl" ;;
      *) pkg="" ;;
    esac
    if [[ -n "${pkg}" ]]; then
      echo "单独安装：pacman -S --needed --noconfirm ${pkg}" >&2
    fi
    echo "或安装完整工具链：pacman -S --needed --noconfirm ${PKG_PREFIX}-toolchain ${PKG_PREFIX}-nasm curl tar xz" >&2
    echo "可先切换国内源加速：bash ${ROOT}/scripts/msys2_china_mirror.sh" >&2
    exit 1
  fi
done

if ! MAKE_CMD="$(resolve_make)"; then
  echo "缺少 make / mingw32-make（MSYSTEM=${MSYSTEM}）" >&2
  echo "MSYS2 的 ${PKG_PREFIX}-make 包提供的是 mingw32-make，请安装：" >&2
  echo "  pacman -S --needed --noconfirm ${PKG_PREFIX}-make" >&2
  echo "可先切换国内源加速：bash ${ROOT}/scripts/msys2_china_mirror.sh" >&2
  exit 1
fi

echo "项目目录：${ROOT}"
echo "输出目录：${OUT_DIR}"
echo "工具链  ：${MSYSTEM} / $(command -v gcc) / ${MAKE_CMD}"

mkdir -p "${BUILD_ROOT}" "${OUT_DIR}"
if [[ ! -d "${SRC_DIR}" ]]; then
  echo "下载 FFmpeg ${FFMPEG_VERSION} ..."
  FFMPEG_MIRRORS=(
    "${FFMPEG_SRC_URL:-}"
    "https://ffmpeg.org/releases/${TARBALL}"
    "https://mirrors.tuna.tsinghua.edu.cn/ffmpeg/releases/${TARBALL}"
    "https://mirrors.ustc.edu.cn/ffmpeg/releases/${TARBALL}"
  )
  downloaded=0
  for url in "${FFMPEG_MIRRORS[@]}"; do
    [[ -n "${url}" ]] || continue
    echo "  尝试：${url}"
    if curl -fL --connect-timeout 15 --retry 2 "${url}" -o "${BUILD_ROOT}/${TARBALL}"; then
      downloaded=1
      break
    fi
  done
  if [[ "${downloaded}" -ne 1 ]]; then
    echo "FFmpeg 源码下载失败，可手动下载 ${TARBALL} 放到 ${BUILD_ROOT}/" >&2
    exit 1
  fi
  tar -xJf "${BUILD_ROOT}/${TARBALL}" -C "${BUILD_ROOT}"
fi

cd "${SRC_DIR}"

need_configure=0
if [[ "${FORCE_RECONFIGURE:-0}" == "1" ]]; then
  need_configure=1
elif [[ ! -f ffbuild/config.mak ]]; then
  need_configure=1
elif ! grep -qE '^CONFIG_FFMPEG=yes$' ffbuild/config.mak 2>/dev/null; then
  # 禁用项写作 !CONFIG_FFMPEG=yes，启用项写作 CONFIG_FFMPEG=yes
  need_configure=1
elif ! grep -qE '^CONFIG_AVFILTER=yes$' ffbuild/config.mak 2>/dev/null; then
  # ffmpeg 程序硬依赖 avfilter
  need_configure=1
elif ! grep -qE '^CONFIG_SWRESAMPLE=yes$' ffbuild/config.mak 2>/dev/null; then
  # float WAV → FLAC 需要 aresample / swresample
  need_configure=1
elif ! grep -qE '^CONFIG_ARESAMPLE_FILTER=yes$' ffbuild/config.mak 2>/dev/null; then
  need_configure=1
fi

if [[ "${need_configure}" -eq 1 ]]; then
  echo "清理旧配置后重新 configure ..."
  if [[ -f Makefile ]]; then
    "${MAKE_CMD}" distclean || true
  fi
  rm -f config.h config.asm ffbuild/config.mak
  # shellcheck disable=SC1091
  source "${ROOT}/scripts/ffmpeg_minimal_configure.sh"
  echo "配置 minimal ffmpeg ..."
  ./configure \
    --prefix="${OUT_DIR}" \
    "${FFMPEG_MINIMAL_CONFIGURE_FLAGS[@]}"
  if ! grep -qE '^CONFIG_FFMPEG=yes$' ffbuild/config.mak 2>/dev/null; then
    echo "configure 后仍未启用 CONFIG_FFMPEG，请检查上方配置输出中的 Programs 段。" >&2
    exit 1
  fi
fi

JOBS="${JOBS:-$(nproc 2>/dev/null || echo 4)}"
echo "编译中（-j${JOBS}）..."
"${MAKE_CMD}" -j"${JOBS}"
"${MAKE_CMD}" install

if [[ -f "${OUT_DIR}/bin/ffmpeg.exe" ]]; then
  cp -f "${OUT_DIR}/bin/ffmpeg.exe" "${OUT_DIR}/ffmpeg.exe"
elif [[ -f "${SRC_DIR}/ffmpeg.exe" ]]; then
  cp -f "${SRC_DIR}/ffmpeg.exe" "${OUT_DIR}/ffmpeg.exe"
elif [[ -f "${OUT_DIR}/ffmpeg.exe" ]]; then
  :
else
  echo "未找到 ffmpeg.exe，请检查 ${OUT_DIR}" >&2
  exit 1
fi

SIZE="$(du -h "${OUT_DIR}/ffmpeg.exe" | awk '{print $1}')"
echo "完成：${OUT_DIR}/ffmpeg.exe (${SIZE})"
"${OUT_DIR}/ffmpeg.exe" -hide_banner -version | head -n 1
