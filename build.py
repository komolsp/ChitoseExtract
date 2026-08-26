"""
打包 ChitoseExtract 为 Windows exe。

用法（在项目源码目录下）：
    pip install -r requirements-build.txt
    python build.py

注意：请使用本脚本打包，勿直接运行 pyinstaller *.spec（已废弃）。

输出：
    dist/ChitoseExtract.exe
    dist/config.yaml
    dist/password.txt
    dist/7zip/          （内置 7-Zip，首次运行也会解压到 exe 旁 7zip/）
    dist/flac/           （内置 flac，首次运行也会解压到 exe 旁 flac/）
    dist/ffmpeg-minimal/（可选，自编译 minimal ffmpeg，处理 float WAV）
    dist/dlrenamer/
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, 'dist')
BUILD_DIR = os.path.join(ROOT, 'build')

from app_paths import APP_NAME, APP_TITLE  # noqa: E402
SEVEN_ZIP_DIR = os.path.join(ROOT, '7zip')
FLAC_DIR = os.path.join(ROOT, 'flac')
FFMPEG_MINIMAL_DIR = os.path.join(ROOT, 'ffmpeg-minimal')
_FFMPEG_MINIMAL_EXE = 'ffmpeg.exe'
_SEVEN_ZIP_FILES = ('7z.exe', '7z.dll')
_FLAC_EXE = 'flac.exe'
_FLAC_RUNTIME_FILES = ('flac.exe', 'libFLAC.dll', 'libFLAC++.dll')
_FLAC_DOWNLOAD_URL = 'https://github.com/xiph/flac/releases/download/1.5.0/flac-1.5.0-win.zip'
_FLAC_WIN64_PREFIX = 'flac-1.5.0-win/Win64/'
_SYSTEM_7ZIP_DIRS = (
    r'C:\Program Files\7-Zip',
    r'C:\Program Files (x86)\7-Zip',
)


def prepare_7zip_bundle() -> str:
    """从本机 7-Zip 安装目录复制 7z.exe / 7z.dll 到项目 7zip/ 供打包内置。"""
    os.makedirs(SEVEN_ZIP_DIR, exist_ok=True)
    if all(os.path.isfile(os.path.join(SEVEN_ZIP_DIR, name)) for name in _SEVEN_ZIP_FILES):
        return SEVEN_ZIP_DIR

    for folder in _SYSTEM_7ZIP_DIRS:
        exe = os.path.join(folder, '7z.exe')
        dll = os.path.join(folder, '7z.dll')
        if os.path.isfile(exe) and os.path.isfile(dll):
            shutil.copy2(exe, os.path.join(SEVEN_ZIP_DIR, '7z.exe'))
            shutil.copy2(dll, os.path.join(SEVEN_ZIP_DIR, '7z.dll'))
            print(f'Copied 7-Zip from {folder}')
            return SEVEN_ZIP_DIR

    which = shutil.which('7z')
    if which:
        folder = os.path.dirname(os.path.abspath(which))
        exe = os.path.join(folder, '7z.exe')
        dll = os.path.join(folder, '7z.dll')
        if os.path.isfile(exe) and os.path.isfile(dll):
            shutil.copy2(exe, os.path.join(SEVEN_ZIP_DIR, '7z.exe'))
            shutil.copy2(dll, os.path.join(SEVEN_ZIP_DIR, '7z.dll'))
            print(f'Copied 7-Zip from {folder}')
            return SEVEN_ZIP_DIR

    raise SystemExit(
        '未找到 7-Zip。请先安装 https://www.7-zip.org/ 后再运行 build.py'
    )


def _copy_flac_runtime_folder(src_bin: str) -> str:
    os.makedirs(FLAC_DIR, exist_ok=True)
    copied = 0
    for name in _FLAC_RUNTIME_FILES:
        src = os.path.join(src_bin, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(FLAC_DIR, name))
            copied += 1
    if not os.path.isfile(os.path.join(FLAC_DIR, _FLAC_EXE)):
        raise SystemExit(f'复制 flac 失败：{src_bin}')
    print(f'Copied flac from {src_bin} ({copied} files)')
    return FLAC_DIR


def _download_flac_windows() -> str:
    import tempfile
    import urllib.request
    import zipfile

    print(f'Downloading flac from {_FLAC_DOWNLOAD_URL} ...')
    os.makedirs(FLAC_DIR, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, 'flac.zip')
        urllib.request.urlretrieve(_FLAC_DOWNLOAD_URL, zip_path)
        with zipfile.ZipFile(zip_path) as archive:
            copied = 0
            for name in archive.namelist():
                if not name.startswith(_FLAC_WIN64_PREFIX) or name.endswith('/'):
                    continue
                base = os.path.basename(name)
                if base not in _FLAC_RUNTIME_FILES:
                    continue
                with archive.open(name) as src, open(os.path.join(FLAC_DIR, base), 'wb') as dst:
                    shutil.copyfileobj(src, dst)
                copied += 1
    if not os.path.isfile(os.path.join(FLAC_DIR, _FLAC_EXE)):
        raise SystemExit('下载 flac 失败：未生成 flac.exe')
    print(f'Downloaded flac ({copied} files)')
    return FLAC_DIR


def prepare_flac_bundle() -> str:
    """准备项目 flac/ 目录，供开发与打包内置。"""
    os.makedirs(FLAC_DIR, exist_ok=True)
    if os.path.isfile(os.path.join(FLAC_DIR, _FLAC_EXE)):
        return FLAC_DIR

    which = shutil.which('flac')
    if which:
        return _copy_flac_runtime_folder(os.path.dirname(os.path.abspath(which)))

    for candidate in (
        os.path.expanduser(r'~\scoop\apps\flac\current'),
        os.path.expanduser(r'~\scoop\shims'),
    ):
        exe = os.path.join(candidate, _FLAC_EXE)
        if os.path.isfile(exe):
            return _copy_flac_runtime_folder(candidate)

    try:
        return _download_flac_windows()
    except Exception as err:
        raise SystemExit(
            '未找到 flac，且自动下载失败：{}\n'
            '请手动下载 https://github.com/xiph/flac/releases/download/1.5.0/flac-1.5.0-win.zip\n'
            '解压后将 Win64 目录内 flac.exe 与 libFLAC*.dll 复制到项目 flac/ 目录'.format(err)
        ) from err


def _try_build_minimal_ffmpeg() -> bool:
    ps1 = os.path.join(ROOT, 'scripts', 'build_minimal_ffmpeg.ps1')
    sh = os.path.join(ROOT, 'scripts', 'build_minimal_ffmpeg.sh')
    if not os.path.isfile(ps1) and not os.path.isfile(sh):
        return False
    print('尝试自编译 minimal ffmpeg（需要 MSYS2 MINGW64）...')
    if os.name == 'nt' and os.path.isfile(ps1):
        completed = subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-File', ps1],
            cwd=ROOT,
        )
        return completed.returncode == 0
    if os.path.isfile(sh):
        completed = subprocess.run(['bash', sh], cwd=ROOT)
        return completed.returncode == 0
    return False


def prepare_ffmpeg_minimal_bundle(*, try_build: bool = False) -> str | None:
    """返回 ffmpeg-minimal/ 目录；不存在时可选触发 MSYS2 自编译。"""
    target = os.path.join(FFMPEG_MINIMAL_DIR, _FFMPEG_MINIMAL_EXE)
    if os.path.isfile(target):
        return FFMPEG_MINIMAL_DIR
    if try_build and _try_build_minimal_ffmpeg() and os.path.isfile(target):
        print(f'Built minimal ffmpeg: {target}')
        return FFMPEG_MINIMAL_DIR
    return None


def _sep() -> str:
    return ';' if os.name == 'nt' else ':'


def _add_data(src: str, dest: str) -> str:
    return f'{src}{_sep()}{dest}'


def verify_build_environment() -> None:
    """在 PyInstaller 清理旧产物前确认当前解释器具备完整运行依赖。"""
    check_script = os.path.join(ROOT, 'check_runtime.py')
    completed = subprocess.run([sys.executable, check_script], cwd=ROOT)
    if completed.returncode:
        raise SystemExit(
            '当前 Python 缺少运行依赖。请先执行：\n'
            f'  "{sys.executable}" -m pip install -r requirements.txt\n'
            f'  "{sys.executable}" -m pip install -r requirements-build.txt'
        )
    try:
        import PyInstaller  # noqa: F401
    except ImportError as err:
        raise SystemExit(
            f'当前 Python 未安装 PyInstaller："{sys.executable}" '
            '-m pip install -r requirements-build.txt'
        ) from err


def verify_build_output(exe_path: str) -> None:
    """运行冻结程序的无 GUI 自检，避免发布只能生成、不能启动的 exe。"""
    if not os.path.isfile(exe_path):
        raise SystemExit(f'打包失败，未生成：{exe_path}')
    completed = subprocess.run(
        [exe_path, '--build-smoke-test'],
        cwd=DIST,
        capture_output=True,
        text=True,
        timeout=60,
    )
    # windowed exe 没有控制台，不能依赖 stdout；退出码 0 即代表自检通过。
    if completed.returncode != 0:
        detail = (completed.stdout + '\n' + completed.stderr).strip()
        raise SystemExit(f'打包后运行时自检失败（退出码 {completed.returncode}）：\n{detail}')


def main():
    import argparse

    parser = argparse.ArgumentParser(description=f'打包 {APP_TITLE}')
    parser.add_argument(
        '--build-minimal-ffmpeg',
        action='store_true',
        help='打包前尝试用 MSYS2 自编译 minimal ffmpeg（WAV/AIFF→FLAC）',
    )
    args = parser.parse_args()

    verify_build_environment()

    icon = os.path.join(ROOT, 'assets', 'icon.ico')
    if not os.path.isfile(icon):
        raise SystemExit(f'缺少图标文件: {icon}')

    seven_zip = prepare_7zip_bundle()
    flac_bundle = prepare_flac_bundle()
    ffmpeg_minimal = prepare_ffmpeg_minimal_bundle(try_build=args.build_minimal_ffmpeg)

    hidden_imports = [
        'app_paths',
        'audio_convert',
        'audio_tagger',
        'mutagen',
        'pyzipper',
        'mutagen.flac',
        'mutagen.id3',
        'mutagen.mp3',
        'mutagen.wave',
        'chardet',
        'ui_scaling',
        'peewee',
        'pydantic',
        'ruamel.yaml',
        'typing_extensions',
        'windnd',
        'win32api',
        'win32con',
        'win32pdh',
        'pywintypes',
        'PIL',
        'PIL.Image',
        'scraper.cached_scraper',
        'scraper.db',
        'scraper.dlsite',
        'scraper.locale',
        'scraper.scraper',
        'scraper.rjcode_locales',
        'scraper.translation',
        'scraper.work_metadata',
        'scraper.langs.en_us',
        'scraper.langs.ja_jp',
        'scraper.langs.ko_kr',
        'scraper.langs.zh_cn',
        'scraper.langs.zh_tw',
    ]

    add_data = [
        _add_data(os.path.join(ROOT, 'assets'), 'assets'),
        _add_data(os.path.join(ROOT, 'config.yaml'), '.'),
        _add_data(os.path.join(ROOT, 'password.txt'), '.'),
        _add_data(seven_zip, '7zip'),
        _add_data(flac_bundle, 'flac'),
    ]
    if ffmpeg_minimal:
        add_data.append(_add_data(ffmpeg_minimal, 'ffmpeg-minimal'))

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        'main.py',
        '--name', APP_NAME,
        '--onefile',
        '--windowed',
        '--clean',
        '--noconfirm',
        '--icon', icon,
        '--specpath', BUILD_DIR,
    ]
    for item in add_data:
        cmd.extend(['--add-data', item])
    cmd.extend([
        '--paths', ROOT,
        '--collect-submodules', 'scraper',
        '--collect-submodules', 'volume',
    ])
    for item in hidden_imports:
        cmd.extend(['--hidden-import', item])

    print('Running:', ' '.join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)

    os.makedirs(os.path.join(DIST, 'dlrenamer'), exist_ok=True)
    dist_7zip = os.path.join(DIST, '7zip')
    if os.path.isdir(dist_7zip):
        shutil.rmtree(dist_7zip)
    shutil.copytree(seven_zip, dist_7zip)
    dist_flac = os.path.join(DIST, 'flac')
    if os.path.isdir(dist_flac):
        shutil.rmtree(dist_flac)
    shutil.copytree(flac_bundle, dist_flac)
    if ffmpeg_minimal:
        dist_ffmpeg = os.path.join(DIST, 'ffmpeg-minimal')
        if os.path.isdir(dist_ffmpeg):
            shutil.rmtree(dist_ffmpeg)
        shutil.copytree(ffmpeg_minimal, dist_ffmpeg)
    for name in ('config.yaml', 'password.txt'):
        src = os.path.join(ROOT, name)
        dst = os.path.join(DIST, name)
        if os.path.isfile(src):
            shutil.copy2(src, dst)

    exe_path = os.path.join(DIST, f'{APP_NAME}.exe')
    verify_build_output(exe_path)
    print()
    print('Build complete:')
    print(' ', exe_path)
    print('Place config.yaml / password.txt beside the exe (already copied to dist/).')
    print('Bundled 7-Zip copied to dist/7zip/ (also embedded in exe).')
    print('Bundled flac copied to dist/flac/ (also embedded in exe).')
    if ffmpeg_minimal:
        print('Bundled minimal ffmpeg copied to dist/ffmpeg-minimal/ (also embedded in exe).')
    else:
        print('未内置 minimal ffmpeg；float WAV 需配置 ffmpeg_fallback_path 或先运行：')
        print('  powershell -File scripts/build_minimal_ffmpeg.ps1')


if __name__ == '__main__':
    main()
