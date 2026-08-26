"""Lightweight interpreter check used by the Windows launch scripts."""

import importlib
import sys


MIN_VERSION = (3, 9)
REQUIRED_MODULES = (
    "chardet",
    "mutagen",
    "peewee",
    "PIL",
    "pydantic",
    "pyzipper",
    "requests",
    "ruamel.yaml",
    "tkinter",
    "win32api",
    "windnd",
)


def main():
    if sys.version_info < MIN_VERSION:
        return 2
    try:
        for module_name in REQUIRED_MODULES:
            importlib.import_module(module_name)
    except (ImportError, OSError):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
