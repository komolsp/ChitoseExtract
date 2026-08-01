import multiprocessing
import sys


def _enable_windows_dpi_awareness():
    """
    程序未声明 DPI 感知时，Windows 会在高分屏/系统缩放下对整个窗口位图做拉伸，
    导致界面（尤其是图标、GIF 等小图）明显模糊。在创建任何窗口前告知系统本程序
    自行处理 DPI 缩放，交由 Tk 按真实像素渲染，从而保持清晰。
    """
    if sys.platform != 'win32':
        return
    import ctypes
    try:
        # Per-Monitor v2：多屏不同缩放下也能保持清晰（Win10 1703+）
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
        return
    except (AttributeError, OSError):
        pass
    try:
        # Per-Monitor DPI aware（Win8.1+）
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        # System DPI aware（Vista+，兜底）
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


# 须在 import tkinter / 创建任何窗口之前声明，否则 Windows 会把整窗位图拉伸导致图标发糊。
_enable_windows_dpi_awareness()

import app_paths  # noqa: F401 — frozen 模式下导入时即初始化运行目录

import config
import dlrenamer.ez_client
import filter
import gui
import password
import pk_logger
import task_runner
import unzip_process_pool
import unzipper


def _run_build_smoke_test() -> int:
    """打包后无 GUI 自检：确认关键模块、配置和内置命令可用。"""
    import os

    app_paths.setup_runtime()
    required_files = (
        app_paths.seven_zip_exe(),
        app_paths.flac_exe(),
        app_paths.user_path('config.yaml'),
        app_paths.user_path('password.txt'),
    )
    missing = [str(path) for path in required_files if not path or not os.path.isfile(path)]
    if missing:
        if sys.stdout is not None:
            print('BUILD_SMOKE_TEST_FAILED')
            for path in missing:
                print(f'missing: {path}')
        return 2
    # 同时解析配置，捕获冻结后 YAML/Pydantic/本地模块收集问题。
    config.Config()
    if sys.stdout is not None:
        print('BUILD_SMOKE_TEST_OK')
    return 0


def _show_config_error(message: str):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            f'{app_paths.APP_NAME} — 配置错误',
            message,
            parent=root,
        )
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)


# 必须保持引用，否则句柄被回收后互斥量释放，单实例会失效。
_single_instance_mutex = None


def _acquire_single_instance() -> bool:
    """避免重复启动导致 log 文件争用、界面无响应。"""
    global _single_instance_mutex
    if sys.platform != 'win32':
        return True
    import ctypes
    kernel32 = ctypes.windll.kernel32
    _single_instance_mutex = kernel32.CreateMutexW(
        None, False, 'Local\\ChitoseExtract.v1.0.SingleInstance',
    )
    return kernel32.GetLastError() != 183


def _activate_existing_window() -> bool:
    """把已运行实例的主窗口还原并尝试置前。"""
    if sys.platform != 'win32':
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, app_paths.APP_TITLE)
    if not hwnd:
        matches: list[int] = []
        EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _enum(hwnd_enum, _lparam):
            length = user32.GetWindowTextLengthW(hwnd_enum)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd_enum, buf, length + 1)
            title = buf.value
            if title == app_paths.APP_TITLE or title.startswith(f'{app_paths.APP_NAME} '):
                matches.append(int(hwnd_enum))
            return True

        # 保持回调引用，避免 EnumWindows 期间被 GC
        enum_cb = EnumProc(_enum)
        user32.EnumWindows(enum_cb, 0)
        if not matches:
            return False
        hwnd = matches[0]

    SW_RESTORE = 9
    SW_SHOW = 5
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        user32.ShowWindow(hwnd, SW_SHOW)
    user32.SetForegroundWindow(hwnd)
    user32.FlashWindow(hwnd, True)
    return True


def _show_already_running():
    if _activate_existing_window():
        return
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            app_paths.APP_TITLE,
            '程序已在运行中，但未找到可见窗口。\n'
            '请在任务管理器中结束 pythonw.exe / python3.13 后重试。',
            parent=root,
        )
        root.destroy()
    except Exception:
        pass


if __name__ == '__main__':
    resource = None
    pool = None
    crashed = False
    try:
        multiprocessing.freeze_support()
        if '--build-smoke-test' in sys.argv:
            raise SystemExit(_run_build_smoke_test())
        _enable_windows_dpi_awareness()
        if not _acquire_single_instance():
            _show_already_running()
            sys.exit(0)
        app_paths.setup_runtime()
        try:
            conf = config.Config()
        except config.ConfigError as err:
            _show_config_error(str(err))
            sys.exit(1)
        gui.output = conf.output_path

        passwords = password.read_password()
        logger = pk_logger.Pk_logger('task_runner', 'log.txt').add_log_handler().get_logger()

        # 任务队列容量与并行解压进程数对齐，避免 submit 频繁阻塞
        resource = unzip_process_pool.ProcessResourceManager(conf.max_thread)

        task_runner.logger = logger
        task_runner.conf = conf
        task_runner.passwords = passwords
        task_runner.unzipper = unzipper.Unzipper(logger, resource, seven_z_mmt=conf.seven_z_mmt)
        task_runner.filter = filter.Filter(conf.filter_kw, conf.filter_dir, logger)
        task_runner.renamer = dlrenamer.ez_client.ensure_client(conf.renamer_config)

        gui.init_ui(resource.log_queue)

        pool = unzip_process_pool.ProcessPool(conf.max_thread, resource)

        gui.mainloop_ui()
    except Exception:
        crashed = True
        import app_paths
        app_paths.append_startup_error_log('startup crash')
        raise
    finally:
        # GUI 正常关闭或异常退出时都回收后台进程。变量预先置空，
        # 可覆盖配置读取、Manager 创建、进程池创建到一半等失败阶段。
        if resource is not None:
            try:
                resource.log_queue.put(None)
            except (EOFError, OSError):
                pass
        if pool is not None:
            try:
                pool.shutdown(wait=not crashed)
            finally:
                resource.shutdown()
        elif resource is not None:
            resource.shutdown()
