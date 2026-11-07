#!/usr/bin/env python3
"""Replace one hierarchical tag path in media sidecars, preserving other tags.

    python retag_files.py --lib ~/Photos \
        --old-tag BackcountrySki --new-tag 'sport|ski|backcountry'

    # only some folders (relative to --lib, subfolders included; repeatable)
    python retag_files.py --lib ~/Photos --folder 2024 --folder 2025/2025-02_Alps \
        --old-tag BackcountrySki --new-tag 'sport|ski|backcountry'

Without --folder, the whole library is scanned.

Review the dry run before adding --go. A live run backs up every sidecar it
will change into the library's _sorting directory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import tarfile

from media_ext import MEDIA_EXT
from tag_files import (
    DC_NS,
    LI_RE,
    LR_NS,
    _bag_items,
    _find_block,
    _merge_bag,
    adopt_spelling,
    flatten,
    fold,
    normalize_tags,
    sidecar_for,
    valid,
    xml_unescape,
)


def _tag_key(value: str) -> str:
    """Comparison key of a tag path: levels trimmed, empty levels dropped, case folded."""
    return fold("|".join(level.strip() for level in value.split("|") if level.strip()))


def _remove_items(text: str, element: str, keys: set[str]) -> str:
    """Remove the rdf:li items of *element* whose tag key is in *keys*, keeping other markup."""
    match = _find_block(text, element)
    if match is None or not keys:
        return text

    def keep_or_remove(li_match: re.Match[str]) -> str:
        if _tag_key(xml_unescape(li_match.group(1))) in keys:
            return ""
        return li_match.group(0)

    updated_block = LI_RE.sub(keep_or_remove, match.group(0))
    return text[: match.start()] + updated_block + text[match.end() :]


def _levels(paths: list[str]) -> set[str]:
    """Folded keys of every level of every path."""
    return {fold(level) for path in paths for level in path.split("|")}


def retag(text: str, old_tag: str, new_tag: str) -> tuple[str | None, str]:
    """Return the updated sidecar text and a status note.

    The sidecar's tags are read the way OuEstCharlie and darktable read them:
    lr:hierarchicalSubject when present, plus the dc:subject terms that are not
    a level of any of its paths (tags written by a dc:subject-only tool).
    Without lr:hierarchicalSubject, every dc:subject term is a tag.
    """
    old_key = _tag_key(old_tag)
    hierarchical = _bag_items(text, "lr:hierarchicalSubject")
    dc_tags = normalize_tags(_bag_items(text, "dc:subject") or [])

    if hierarchical is None:
        paths = dc_tags
        if old_key not in {fold(path) for path in paths}:
            return None, "old tag not present"
        kept = [path for path in paths if fold(path) != old_key]
        new_path = adopt_spelling([new_tag], kept)[0]
        # the new tag takes the old one's place, darktable-style paths for all
        updated = normalize_tags([new_path if fold(path) == old_key else path for path in paths])
        merged = _merge_bag(text, "lr:hierarchicalSubject", LR_NS, updated)
        if isinstance(merged, str):
            return None, merged
        text = merged[0]
        note = "hierarchical tag added"
    else:
        paths = normalize_tags(hierarchical)
        has_old_path = old_key in {fold(path) for path in paths}
        known_levels = _levels(paths)
        has_old_flat = any(
            fold(tag) == old_key and fold(tag) not in known_levels for tag in dc_tags
        )
        if not has_old_path and not has_old_flat:
            return None, "old tag not present"

        text = _remove_items(text, "lr:hierarchicalSubject", {old_key})
        kept = [path for path in paths if fold(path) != old_key]
        merged = _merge_bag(text, "lr:hierarchicalSubject", LR_NS, adopt_spelling([new_tag], kept))
        if isinstance(merged, str):
            return None, merged
        text = merged[0]
        note = "hierarchical tag replaced" if has_old_path else "legacy dc:subject tag migrated"

    # dc:subject holds every level of every path: drop the old path's levels that
    # no remaining path uses, otherwise the indexer reads them as flat tags.
    remaining = normalize_tags(_bag_items(text, "lr:hierarchicalSubject") or [])
    old_levels = {_tag_key(level) for level in old_tag.split("|") if level.strip()}
    text = _remove_items(text, "dc:subject", old_levels - _levels(remaining))

    merged_dc = _merge_bag(text, "dc:subject", DC_NS, flatten(remaining))
    if isinstance(merged_dc, str):
        return None, merged_dc
    text = merged_dc[0]

    if not valid(text):
        return None, "would produce invalid XML"
    return text, note


_SKIPPED_DIRS = {".dtrash", ".ouestcharlie", "_sorting"}


class Scan:
    """What a retag would change, found without writing anything."""

    def __init__(self) -> None:
        self.todo: list[tuple[str, str, str]] = []  # (sidecar, new text, note)
        self.skipped = 0  # sidecars without the old tag
        self.problems: list[tuple[str, str]] = []
        self.missing_sidecars = 0


def scan(roots: list[str], old_tag: str, new_tag: str) -> Scan:
    """Walk *roots* (subfolders included) and compute the retag of every sidecar."""
    result = Scan()
    seen_sidecars: set[str] = set()
    for root in roots:
        for parent, directories, filenames in os.walk(root):
            directories[:] = [
                name
                for name in directories
                if name not in _SKIPPED_DIRS and not name.startswith(".")
            ]
            for filename in sorted(filenames):
                if not filename.lower().endswith(MEDIA_EXT):
                    continue
                sidecar = sidecar_for(parent, filename)
                if sidecar is None:
                    result.missing_sidecars += 1
                    continue
                sidecar = os.path.abspath(sidecar)
                # overlapping --folder values, or a sidecar shared by two media files
                if sidecar in seen_sidecars:
                    continue
                seen_sidecars.add(sidecar)
                try:
                    with open(sidecar, encoding="utf-8", errors="replace") as stream:
                        current = stream.read()
                except OSError as exc:
                    result.problems.append((sidecar, str(exc)))
                    continue
                updated, note = retag(current, old_tag, new_tag)
                if updated is None:
                    if note.startswith("!!") or note == "would produce invalid XML":
                        result.problems.append((sidecar, note))
                    else:
                        result.skipped += 1
                    continue
                result.todo.append((sidecar, updated, note))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lib", required=True)
    parser.add_argument("--old-tag", required=True)
    parser.add_argument("--new-tag", required=True)
    parser.add_argument(
        "--folder",
        action="append",
        default=[],
        help="path relative to --lib, subfolders included; repeatable (default: whole library)",
    )
    parser.add_argument("--go", action="store_true")
    parser.add_argument("--preview", type=int, default=5)
    args = parser.parse_args()

    old_tag = "|".join(part.strip() for part in args.old_tag.split("|") if part.strip())
    new_tag = "|".join(part.strip() for part in args.new_tag.split("|") if part.strip())
    if not old_tag or not new_tag or fold(old_tag) == fold(new_tag):
        parser.error("old and new tags must be non-empty and different")

    roots = [os.path.join(args.lib, *folder.split("/")) for folder in args.folder]
    absent = [root for root in roots if not os.path.isdir(root)]
    if absent:
        parser.error(f"not a folder: {', '.join(absent)}")
    found = scan(roots or [args.lib], old_tag, new_tag)
    todo, skipped, problems = found.todo, found.skipped, found.problems

    print("*** LIVE RUN ***" if args.go else "DRY RUN - nothing will be written")
    print(f"tag     : {old_tag} -> {new_tag}")
    print(f"scope   : {', '.join(args.folder) or 'whole library'}")
    print(f"sidecars: {len(todo)} to change, {skipped} without old tag")
    print(f"media without sidecar: {found.missing_sidecars}")
    for sidecar, _updated, note in todo[: args.preview]:
        print(f"  {os.path.relpath(sidecar, args.lib)}  {note}")
    if len(todo) > args.preview:
        print(f"  ... and {len(todo) - args.preview} more")
    print(f"problems: {len(problems)}")
    for sidecar, note in problems[:20]:
        print(f"  {sidecar}: {note}")

    if not args.go or not todo:
        return

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(args.lib, "_sorting")
    os.makedirs(backup_dir, exist_ok=True)
    archive_path = os.path.join(backup_dir, f"xmp_backup_retagging_{stamp}.tar.gz")
    with tarfile.open(archive_path, "w:gz") as archive:
        for sidecar, _updated, _note in todo:
            archive.add(sidecar, arcname=os.path.relpath(sidecar, args.lib))
    print(f"backup: {archive_path}")

    for sidecar, updated, _note in todo:
        with open(sidecar, "w", encoding="utf-8") as stream:
            stream.write(updated)
    print(f"written: {len(todo)} sidecars")


if __name__ == "__main__":
    main()
