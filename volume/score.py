"""分卷组置信度评分。"""

import os

from volume import magic, parse


def _part_index(basename: str) -> int | None:
    for parser in (
        parse.parse_disguised_split,
        parse.parse_trailing_numeric,
        parse.parse_leading_numeric,
        parse.parse_rar_part,
        parse.parse_rar_oldstyle,
        parse.parse_simple_numeric,
    ):
        parsed = parser(basename)
        if parsed:
            return parsed[1]
    parsed_7z = parse.parse_7z_split(basename)
    if parsed_7z:
        return parse.order_7z_part(parsed_7z[1])
    return None


def score_volume_group(volumes: list[str]) -> float:
    """为候选分卷组打分（0~1），用于多策略消歧。"""
    if len(volumes) < 2:
        return 0.0

    score = 0.35
    indices: list[int] = []
    for path in volumes:
        idx = _part_index(os.path.basename(path))
        if idx is not None:
            indices.append(idx)

    if len(indices) == len(volumes):
        score += 0.15
        unique = sorted(set(indices))
        if len(unique) == len(indices):
            score += 0.1
            if unique == list(range(unique[0], unique[0] + len(unique))):
                score += 0.25
            elif max(unique) - min(unique) + 1 == len(unique):
                score += 0.15

    score += min(0.05 * (len(volumes) - 2), 0.15)

    ordered = sorted(
        volumes,
        key=lambda p: _part_index(os.path.basename(p)) or 999,
    )
    if magic.is_rar_file(ordered[0]) or magic.is_zip_file(ordered[0]) or magic.is_7z_file(ordered[0]):
        score += 0.15

    return min(score, 1.0)


def pick_best_candidate(candidates: list[tuple[float, list[str]]]) -> list[str] | None:
    """从 (score, volumes) 列表中选取最优组。"""
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], -len(item[1])))
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return max(candidates, key=lambda item: len(item[1]))[1]
    return candidates[0][1]
