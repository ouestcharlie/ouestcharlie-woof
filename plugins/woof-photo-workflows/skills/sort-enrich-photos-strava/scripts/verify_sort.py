#!/usr/bin/env python3
"""
Check a sort run actually did what it claimed.

    python verify_sort.py --lib <library> --src CameraRoll \
        --folders 2026/2026-07-15_EagleRidge 2026/2026-07-13_BeaconHill

Checks, in order of how likely they are to catch a real problem:

  1. every sidecar touched recently re-parses as valid XML
  2. sidecars that were supposed to get dc:subject actually have it
     (catches the self-closing <rdf:Description/> case, where the move
      succeeds and the metadata silently goes nowhere)
  3. file and sidecar counts per destination folder
  4. orphaned sidecars left in the source with no photo beside them
  5. mixed sidecar conventions inside a single destination folder

Exit code is non-zero if anything failed, so it can gate a workflow.
"""

import argparse
import glob
import os
import re
import sys
import time
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


def recent(path, window_s):
    try:
        return os.path.getmtime(path) >= time.time() - window_s
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", required=True)
    ap.add_argument("--src", default="")
    ap.add_argument("--folders", nargs="*", default=[])
    ap.add_argument(
        "--window",
        type=int,
        default=3600,
        help="only inspect sidecars modified in the last N seconds",
    )
    args = ap.parse_args()

    problems = []  # fail the run
    notes = []  # worth saying, not worth failing over

    # 1 + 2: validity and metadata actually present
    checked = 0
    for folder in args.folders:
        d = os.path.join(args.lib, *folder.split("/"))
        for side in glob.glob(os.path.join(d, "*.xmp")):
            if not recent(side, args.window):
                continue
            checked += 1
            try:
                with open(side, encoding="utf-8") as fh:
                    raw = fh.read()
                body = raw.split("?>", 1)[-1].rsplit("<?xpacket", 1)[0]
                xml.dom.minidom.parseString(body)
            except Exception as e:
                problems.append(f"INVALID XML  {side}  ({e})")
                continue
            if "<dc:subject" not in raw and "<dc:description" not in raw:
                problems.append(f"NO METADATA  {side}  (moved but nothing written)")

    print(f"sidecars checked for validity : {checked}")

    # 3: counts
    print("\nper-folder counts")
    for folder in args.folders:
        d = os.path.join(args.lib, *folder.split("/"))
        if not os.path.isdir(d):
            problems.append(f"MISSING FOLDER  {folder}")
            continue
        names = os.listdir(d)
        media = [f for f in names if f.lower().endswith(MEDIA_EXT)]
        cars = [f for f in names if f.lower().endswith(".xmp")]
        print(f"   {folder:<52} {len(media):3} media  {len(cars):3} sidecars")

        # 5: legacy-named sidecars. Not a fault — photo.xmp is the older
        # convention and is still read — but worth surfacing, since a folder
        # holding both means anything globbing for one form sees half the files.
        dbl = sum(1 for f in cars if re.search(r"\.(jpg|jpeg|heic|mp4|mov)\.xmp$", f, re.I))
        sgl = len(cars) - dbl
        if sgl:
            dbl_note = f", alongside {dbl} current (photo.ext.xmp)" if dbl else ""
            notes.append(f"{folder}: {sgl} legacy-named sidecar(s) (photo.xmp){dbl_note}")

    # 4: orphans in the source
    if args.src:
        s = args.src if os.path.isabs(args.src) else os.path.join(args.lib, args.src)
        if os.path.isdir(s):
            names = set(os.listdir(s))
            orphans = []
            for x in (n for n in names if n.lower().endswith(".xmp")):
                stem = x[:-4]  # photo.jpg.xmp -> photo.jpg
                if stem in names:
                    continue
                if any(stem + e in names or stem + e.upper() in names for e in MEDIA_EXT):
                    continue  # photo.xmp -> photo.jpg
                orphans.append(x)
            print(f"\norphaned sidecars in source   : {len(orphans)}")
            for o in orphans:
                problems.append(f"ORPHAN SIDECAR  {os.path.join(args.src, o)}")

    if notes:
        print("\nnotes")
        for n in notes:
            print("   " + n)

    print("\n" + "=" * 70)
    if problems:
        print(f"{len(problems)} PROBLEM(S)")
        for p in problems:
            print("   " + p)
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
