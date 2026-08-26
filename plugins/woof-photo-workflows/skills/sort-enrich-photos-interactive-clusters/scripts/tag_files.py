#!/usr/bin/env python3
"""
Add tags (and optionally a description) to XMP sidecars in place.

No file is moved, created or deleted. Use this for bulk tagging existing
folders, which is a different job from sorting a camera roll.

    # every quarterly folder for one subject. Note the character class [ +]:
    # folder naming often drifts over the years, and a pattern that only
    # matches today's spelling will silently skip whole eras of a library.
    python tag_files.py --lib ~/Photos \
        --folder-regex '^\\d{4}-\\d{2}[ +]\\d{2}[ +]\\d{2}_Kids$' \
        --tags Kids Family

    # one folder, with a description too
    python tag_files.py --lib ~/Photos \
        --folder '2026/2026-06-20_LakeCabin' \
        --tags Cabin Lake --desc "Second visit to the cabin"

    # write for real
    ... --go

Two properties make this safe to run on a thousand files:

  MERGE       a sidecar that already has dc:subject keeps its existing tags;
              only the missing ones are added, inside the same rdf:Bag. There
              is never a second dc:subject block.

  IDEMPOTENT  files already carrying every requested tag are skipped, so a
              second run reports zero changes and an interrupted run can just
              be repeated. Always verify by running twice.

An existing dc:description is never overwritten.
"""

import argparse
import datetime as dt
import os
import re
import sys
import tarfile
import xml.dom.minidom

MEDIA_EXT = (
    ".jpg",
    ".jpeg",
    ".heic",
    ".heif",
    ".png",
    ".dng",
    ".cr2",
    ".cr3",
    ".nef",
    ".arw",
    ".raf",
    ".orf",
    ".rw2",
    ".mp4",
    ".mov",
)
DC_NS = 'xmlns:dc="http://purl.org/dc/elements/1.1/"'


def xml_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def valid(text):
    try:
        xml.dom.minidom.parseString(text.split("?>", 1)[-1].rsplit("<?xpacket", 1)[0])
        return True
    except Exception:
        return False


def sidecar_for(folder, media):
    """Locate a sidecar. Current convention first, legacy fallback second.

        photo.jpg.xmp   full filename + .xmp   <- current, preferred
        photo.xmp       basename + .xmp        <- legacy, still read

    This function only reads; tagging edits a sidecar in place and never
    renames it, so a legacy-named sidecar keeps its name.
    """
    for cand in (media + ".xmp", os.path.splitext(media)[0] + ".xmp"):
        p = os.path.join(folder, cand)
        if os.path.exists(p):
            return p
    return None


def _insert(text, block):
    if "</rdf:Description>" in text:
        return text.replace("</rdf:Description>", block + "</rdf:Description>", 1)
    # a sidecar with no EXIF has a self-closing <rdf:Description/>; expand it,
    # otherwise the write silently goes nowhere
    m = re.search(r"(<rdf:Description\b[^>]*?)\s*/>", text)
    if not m:
        return None
    return text[: m.start()] + m.group(1) + ">" + block + "</rdf:Description>" + text[m.end() :]


def apply(text, tags, desc=None):
    """Return (new_text, note). new_text None means no change needed/possible."""
    notes = []
    out = text

    if desc:
        if "<dc:description" in out:
            notes.append("description already present, kept")
        else:
            blk = (
                f'<dc:description {DC_NS}><rdf:Alt><rdf:li xml:lang="x-default">'
                f"{xml_escape(desc)}</rdf:li></rdf:Alt></dc:description>"
            )
            new = _insert(out, blk)
            if new is None:
                return None, "!! no rdf:Description anchor"
            out = new
            notes.append("description added")

    if tags:
        m = re.search(r"<dc:subject\b.*?</dc:subject>", out, re.S)
        if m:
            block = m.group(0)
            present = re.findall(r"<rdf:li>(.*?)</rdf:li>", block)
            missing = [t for t in tags if t not in present]
            if missing:
                if "</rdf:Bag>" not in block:
                    return None, "!! dc:subject has no rdf:Bag"
                add = "".join(f"<rdf:li>{xml_escape(t)}</rdf:li>" for t in missing)
                out = (
                    out[: m.start()]
                    + block.replace("</rdf:Bag>", add + "</rdf:Bag>", 1)
                    + out[m.end() :]
                )
                notes.append(f"merged {missing} into {present}")
            else:
                notes.append("tags already present")
        else:
            tags_xml = "".join(f"<rdf:li>{xml_escape(t)}</rdf:li>" for t in tags)
            blk = f"<dc:subject {DC_NS}><rdf:Bag>{tags_xml}</rdf:Bag></dc:subject>"
            new = _insert(out, blk)
            if new is None:
                return None, "!! no rdf:Description anchor"
            out = new
            notes.append(f"tags {tags} added")

    if out == text:
        return None, "; ".join(notes) or "nothing to do"
    return out, "; ".join(notes)


def collect_folders(lib, folder_args, regex):
    if folder_args:
        return [os.path.join(lib, *f.split("/")) for f in folder_args]
    if not regex:
        sys.exit("give --folder or --folder-regex")
    pat = re.compile(regex)
    out = []
    for parent, dirs, _files in os.walk(lib):
        for d in dirs:
            if pat.match(d):
                out.append(os.path.join(parent, d))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", required=True)
    ap.add_argument(
        "--folder", action="append", default=[], help="path relative to lib; repeatable"
    )
    ap.add_argument("--folder-regex", help="match folder basenames anywhere under lib")
    ap.add_argument("--tags", nargs="*", default=[])
    ap.add_argument("--desc", default=None)
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--preview", type=int, default=3, help="sample lines per folder")
    args = ap.parse_args()

    if not args.tags and not args.desc:
        sys.exit("give --tags and/or --desc")

    dry = not args.go
    folders = collect_folders(args.lib, args.folder, args.folder_regex)

    print("=" * 78)
    print("DRY RUN - nothing will be written" if dry else "*** LIVE RUN ***")
    print(f"tags   : {args.tags or '-'}")
    print(f"desc   : {args.desc or '-'}")
    print(f"folders: {len(folders)}")
    print("=" * 78)

    todo, skipped, problems, nosidecar = [], 0, [], []

    for d in folders:
        if not os.path.isdir(d):
            problems.append((d, "folder not found"))
            continue
        rows = []
        for media in sorted(os.listdir(d)):
            if not media.lower().endswith(MEDIA_EXT):
                continue
            side = sidecar_for(d, media)
            if side is None:
                nosidecar.append(os.path.join(d, media))
                continue
            with open(side, encoding="utf-8", errors="replace") as fh:
                cur = fh.read()
            new, note = apply(cur, args.tags, args.desc)
            if new is None:
                if note.startswith("!!"):
                    problems.append((side, note))
                else:
                    skipped += 1
                continue
            if not valid(new):
                problems.append((side, "would produce invalid XML"))
                continue
            todo.append((side, new))
            rows.append((media, note))
        if rows:
            print(f"\n{os.path.relpath(d, args.lib)}/   {len(rows)} file(s)")
            for media, note in rows[: args.preview]:
                print(f"   {media:<36} {note}")
            if len(rows) > args.preview:
                print(f"   ... and {len(rows) - args.preview} more")

    if todo and not args.go:
        pass
    elif todo:
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        outdir = os.path.join(args.lib, "_sorting")
        os.makedirs(outdir, exist_ok=True)
        tgz = os.path.join(outdir, f"xmp_backup_tagging_{stamp}.tar.gz")
        with tarfile.open(tgz, "w:gz") as tar:
            for side, _new in todo:
                tar.add(side, arcname=os.path.relpath(side, args.lib))
        print(f"\nbackup of the {len(todo)} sidecars about to change: {tgz}")
        for side, new in todo:
            with open(side, "w", encoding="utf-8") as fh:
                fh.write(new)

    print("\n" + "-" * 78)
    print(f"sidecars to change : {len(todo)}")
    print(f"already complete   : {skipped}")
    print(f"media w/o sidecar  : {len(nosidecar)}")
    for p in nosidecar[:20]:
        print(f"      {os.path.relpath(p, args.lib)}")
    print(f"problems           : {len(problems)}")
    for p, n in problems[:20]:
        print(f"      {p}  {n}")
    print("-" * 78)
    print(
        "DRY RUN - nothing was written"
        if dry
        else "*** WRITTEN *** now re-run this command to confirm it reports 0 changes"
    )


if __name__ == "__main__":
    main()
