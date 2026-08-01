import os
import shutil
import sys

APP_NAME = 'ChitoseExtract'
APP_VERSION = 'v1.0'
APP_TITLE = f'{APP_NAME} {APP_VERSION}'
LEGACY_STAGING_DIR_NAMES = frozenset({'Prekikoeru-KM'})


def is_staging_dir_name(name: str) -> bool:
    if name.startswith('.pk_'):
        return True
    if name in LEGACY_STAGING_DIR_NAMES:
        return True
    return name == APP_NAME


_SYSTEM_7ZIP_DIRS = (
    r'C:\Program Files\7-Zip',
    r'C:\Program Files (x86)\7-Zip',
)


def is_frozen() -> bool:
    return getattr(sys, 'frozen', False)


def app_dir() -> str:
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def bundle_path(*parts: str) -> str:
    if is_frozen():
        base = getattr(sys, '_MEIPASS', app_dir())
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


def user_path(*parts: str) -> str:
    return os.path.join(app_dir(), *parts)


_STARTUP_ERROR_LOG = 'startup_error.log'
_STARTUP_ERROR_MAX_BYTES = 2 * 1024 * 1024
_STARTUP_ERROR_BACKUPS = 2


def startup_error_log_path() -> str:
    return user_path(_STARTUP_ERROR_LOG)


def rotate_startup_error_log_if_needed(
        max_bytes: int = _STARTUP_ERROR_MAX_BYTES,
        backups: int = _STARTUP_ERROR_BACKUPS) -> None:
    """startup_error.log 超过上限时轮转，避免无限增大。"""
    path = startup_error_log_path()
    try:
        if not os.path.isfile(path) or os.path.getsize(path) <= max_bytes:
            return
        oldest = f'{path}.{backups}'
        if os.path.isfile(oldest):
            os.remove(oldest)
        for index in range(backups - 1, 0, -1):
            src = f'{path}.{index}'
            dest = f'{path}.{index + 1}'
            if os.path.isfile(src):
                os.replace(src, dest)
        os.replace(path, f'{path}.1')
        open(path, 'a', encoding='utf-8').close()
    except OSError:
        pass


def append_startup_error_log(header: str) -> None:
    """追加 startup_error.log 条目；写入前自动检查轮转。"""
    import traceback

    rotate_startup_error_log_if_needed()
    try:
        with open(startup_error_log_path(), 'a', encoding='utf-8') as f:
            f.write(f'\n--- {header} ---\n')
            traceback.print_exc(file=f)
    except OSError:
        traceback.print_exc()


def seven_zip_dir() -> str:
    return user_path('7zip')


def seven_zip_exe() -> str:
    installed = user_path('7zip', '7z.exe')
    if os.path.isfile(installed):
        return installed

    dev = os.path.join(os.path.dirname(os.path.abspath(__file__)), '7zip', '7z.exe')
    if os.path.isfile(dev):
        return dev

    bundled = bundle_path('7zip', '7z.exe')
    if os.path.isfile(bundled):
        return bundled

    for folder in _SYSTEM_7ZIP_DIRS:
        candidate = os.path.join(folder, '7z.exe')
        if os.path.isfile(candidate):
            return candidate

    return installed


def flac_dir() -> str:
    return user_path('flac')


def flac_exe() -> str | None:
    """返回可用的 flac 可执行文件路径；优先使用内置副本。"""
    installed = user_path('flac', 'flac.exe')
    if os.path.isfile(installed):
        return installed

    dev = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'flac', 'flac.exe')
    if os.path.isfile(dev):
        return dev

    bundled = bundle_path('flac', 'flac.exe')
    if os.path.isfile(bundled):
        return bundled

    which = shutil.which('flac')
    if which:
        return which

    return None


def ffmpeg_minimal_dir() -> str:
    return user_path('ffmpeg-minimal')


def ffmpeg_minimal_exe() -> str | None:
    """返回内置 minimal ffmpeg（仅 WAV/AIFF→FLAC），供 float WAV 回退。"""
    installed = user_path('ffmpeg-minimal', 'ffmpeg.exe')
    if os.path.isfile(installed):
        return installed

    dev = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ffmpeg-minimal', 'ffmpeg.exe')
    if os.path.isfile(dev):
        return dev

    bundled = bundle_path('ffmpeg-minimal', 'ffmpeg.exe')
    if os.path.isfile(bundled):
        return bundled

    return None


def _install_bundled_7zip():
    target_dir = seven_zip_dir()
    target_exe = os.path.join(target_dir, '7z.exe')
    if os.path.isfile(target_exe):
        return

    src_dir = bundle_path('7zip')
    if not os.path.isdir(src_dir) or not os.path.isfile(os.path.join(src_dir, '7z.exe')):
        return

    os.makedirs(target_dir, exist_ok=True)
    for name in os.listdir(src_dir):
        src = os.path.join(src_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(target_dir, name))


def _install_bundled_flac():
    target_dir = flac_dir()
    target_exe = os.path.join(target_dir, 'flac.exe')
    if os.path.isfile(target_exe):
        return

    src_dir = bundle_path('flac')
    if not os.path.isdir(src_dir) or not os.path.isfile(os.path.join(src_dir, 'flac.exe')):
        return

    os.makedirs(target_dir, exist_ok=True)
    for name in os.listdir(src_dir):
        src = os.path.join(src_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(target_dir, name))


def _install_bundled_ffmpeg_minimal():
    target_dir = ffmpeg_minimal_dir()
    target_exe = os.path.join(target_dir, 'ffmpeg.exe')
    if os.path.isfile(target_exe):
        return

    src_exe = bundle_path('ffmpeg-minimal', 'ffmpeg.exe')
    if not os.path.isfile(src_exe):
        return

    os.makedirs(target_dir, exist_ok=True)
    shutil.copy2(src_exe, target_exe)


def setup_runtime():
    """冻结 exe 启动时：工作目录切到 exe 旁，并补齐用户配置文件。"""
    os.chdir(app_dir())
    os.makedirs(user_path('dlrenamer'), exist_ok=True)
    rotate_startup_error_log_if_needed()
    _install_bundled_7zip()
    _install_bundled_flac()
    _install_bundled_ffmpeg_minimal()

    for name in ('config.yaml', 'password.txt'):
        target = user_path(name)
        if os.path.isfile(target):
            continue
        bundled = bundle_path(name)
        if os.path.isfile(bundled):
            shutil.copy2(bundled, target)


# 打包 exe 时尽早初始化运行目录（早于 scraper.db 等模块导入）
if is_frozen():
    setup_runtime()
