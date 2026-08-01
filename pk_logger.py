import logging
from logging import handlers

default_formatter: logging.Formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
gui = None

_gui_handler = None


class SafeRotatingFileHandler(handlers.RotatingFileHandler):
    """Windows 下多进程/日志被占用时，轮转失败不应导致程序崩溃。"""

    def doRollover(self):
        try:
            super().doRollover()
        except OSError:
            pass

    def emit(self, record):
        try:
            super().emit(record)
        except OSError:
            pass


class LogHandler(logging.Handler):
    def __init__(self, name):
        logging.Handler.__init__(self)
        self.level = logging.INFO
        self.name = name
        self.setFormatter(default_formatter)

    def emit(self, record):
        if gui is None:
            return
        gui.write(self.format(record) + '\n')


def get_gui_handler() -> LogHandler:
    global _gui_handler
    if _gui_handler is None:
        _gui_handler = LogHandler('gui')
    return _gui_handler


def attach_gui_handler(logger: logging.Logger):
    """所有模块共享同一个 GUI Handler，避免重复输出。"""
    handler = get_gui_handler()
    for existing in logger.handlers[:]:
        if isinstance(existing, LogHandler) and existing is not handler:
            logger.removeHandler(existing)
    if handler not in logger.handlers:
        logger.addHandler(handler)


def _has_handler(logger: logging.Logger, handler_type) -> bool:
    return any(isinstance(handler, handler_type) for handler in logger.handlers)


class Pk_logger(object):
    def __init__(self, name: str, file: str = None):
        self.name = name
        self.__logger = logging.getLogger(name=name)
        self.__logger.setLevel(logging.DEBUG)
        self.__logger.propagate = False

        if file and not _has_handler(self.__logger, SafeRotatingFileHandler):
            file_handler = SafeRotatingFileHandler(file, 'a', 1240 * 1240 * 5, 3, encoding='utf-8')
            file_handler.setFormatter(default_formatter)
            self.__logger.addHandler(file_handler)

    def get_logger(self):
        return self.__logger

    def add_log_handler(self):
        attach_gui_handler(self.__logger)
        return self
