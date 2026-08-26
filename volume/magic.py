"""分卷文件魔数探测。"""

_RAR_SIGS = (b'Rar!\x1a\x07\x00', b'Rar!\x1a\x07\x01\x00')
_ZIP_SIGS = (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')
_7Z_SIG = b'7z\xbc\xaf\x27\x1c'


def read_header(file_path: str, size: int = 16) -> bytes:
    try:
        with open(file_path, 'rb') as f:
            return f.read(size)
    except OSError:
        return b''


def is_rar_file(file_path: str) -> bool:
    return read_header(file_path).startswith(_RAR_SIGS)


def is_zip_file(file_path: str) -> bool:
    return read_header(file_path).startswith(_ZIP_SIGS)


def is_7z_file(file_path: str) -> bool:
    return read_header(file_path).startswith(_7Z_SIG)
