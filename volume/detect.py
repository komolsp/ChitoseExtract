"""分卷组格式判定（魔数 + 命名启发）。"""

import os
import re

from volume import magic, parse


def detect_split_format(volumes: list[str]) -> str:
    ordered: list[tuple[int, str]] = []
    for path in volumes:
        basename = os.path.basename(path)
        parsed = (parse.parse_disguised_split(basename)
                  or parse.parse_trailing_numeric(basename)
                  or parse.parse_leading_numeric(basename)
                  or parse.parse_simple_numeric(
            basename,
        ) or parse.parse_rar_oldstyle(basename))
        ordered.append((parsed[1] if parsed else 999, path))
    ordered.sort(key=lambda item: item[0])

    for _, path in ordered:
        if magic.is_rar_file(path):
            return 'rar'
    for _, path in ordered:
        if magic.is_zip_file(path):
            return 'zip'
    for _, path in ordered:
        if magic.is_7z_file(path):
            return '7z'

    for path in volumes:
        basename = os.path.basename(path)
        if re.search(r'\.part\d+', basename, re.IGNORECASE):
            return 'rar'
        if parse.parse_rar_oldstyle(basename):
            return 'rar'
        if re.search(r'rar', basename, re.IGNORECASE):
            return 'rar'
        if re.search(r'\.7z\.', basename, re.IGNORECASE):
            return '7z'
    return 'zip'
