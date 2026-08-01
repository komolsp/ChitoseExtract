"""分卷压缩包识别：改后缀、插字、混用命名等场景的聚组与规范化。"""

from volume.resolver import (
    VolumeResolver,
    is_complete_volume_group,
    is_standard_volume_group,
    is_volume_archive,
    resolve_volume_archives,
)

__all__ = [
    'VolumeResolver',
    'is_complete_volume_group',
    'is_standard_volume_group',
    'is_volume_archive',
    'resolve_volume_archives',
]
