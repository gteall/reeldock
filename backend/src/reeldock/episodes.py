"""Filename/folder evidence only. Ambiguous multi-episode files are never flattened."""

import re
from pathlib import PurePosixPath

from reeldock.domain import ProviderError

NUM = r"[0-9零〇一二两三四五六七八九十百]+"
SEASON = re.compile(rf"(?i)^(?:season[ ._-]*(\d{{1,2}})|s(\d{{1,2}})|第({NUM})季)$")
SPECIAL = re.compile(r"(?i)^(?:specials?|特别篇|特別篇|番外|特典)$")
PAIR = re.compile(r"(?i)(?<![a-z0-9])s(\d{1,2})e(\d{1,3})(?!\d)|(?<!\d)(\d{1,2})x(\d{1,3})(?!\d)")
MULTI = re.compile(
    rf"(?i)(?:s\d{{1,2}}e\d{{1,3}}(?:[ ._-]*e\d+|[ ._]*-\d+)|"
    rf"\d{{1,2}}x\d{{1,3}}-\d+|第{NUM}\s*[-至到、]\s*{NUM}集)"
)


def number(value):
    if value.isdecimal():
        return int(value)
    digits = dict(
        zip("零〇一二两三四五六七八九", [0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9], strict=True)
    )
    total = current = 0
    for c in value:
        if c in digits:
            current = digits[c]
        elif c in "十百":
            total += (current or 1) * (10 if c == "十" else 100)
            current = 0
        else:
            raise ProviderError("episode_number_unknown")
    return total + current


def season_folder(name):
    if SPECIAL.fullmatch(name):
        return 0
    match = SEASON.fullmatch(name)
    return number(next(g for g in match.groups() if g is not None)) if match else None


def episode_hint(path):
    return bool(PAIR.search(path) or re.search(rf"第{NUM}集", path) or MULTI.search(path))


def parse_episode(path, root):
    relative = PurePosixPath(path).relative_to(root)
    stem = relative.stem
    if MULTI.search(stem) or len(list(PAIR.finditer(stem))) > 1:
        raise ProviderError("multi_episode_file_unsupported")
    folder_seasons = {n for p in relative.parts[:-1] if (n := season_folder(p)) is not None}
    if len(folder_seasons) > 1:
        raise ProviderError("episode_number_conflict")
    match = PAIR.search(stem)
    if match:
        values = [int(g) for g in match.groups() if g is not None]
        season, episode = values
    else:
        s = re.search(rf"第({NUM})季", stem)
        e = re.search(rf"第({NUM})集", stem)
        if not e:
            # E01 is accepted only in an explicit season directory.
            e = re.search(r"(?i)(?<![a-z0-9])e(\d{1,3})(?!\d)", stem)
            if not e or not folder_seasons:
                raise ProviderError("episode_number_unknown")
        season = number(s[1]) if s else next(iter(folder_seasons), 1)
        episode = number(e[1])
    if folder_seasons and season not in folder_seasons:
        raise ProviderError("episode_number_conflict")
    if not 0 <= season <= 99 or not 1 <= episode <= 999:
        raise ProviderError("episode_number_unknown")
    return season, episode
