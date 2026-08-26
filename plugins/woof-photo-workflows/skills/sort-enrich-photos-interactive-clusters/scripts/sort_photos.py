#!/usr/bin/env python3
"""
Sort photos from a staging folder into dated per-event folders and enrich their
XMP sidecars with a description and tags.

HOW TO USE
    1. Copy this file next to the library (e.g. <LIB>/_sorting/sort_photos.py)
       or run it with --lib / --src.
    2. Fill in the CONFIG block below.
    3. Run it. DRY_RUN is True, so it only prints what it would do.
    4. Show the output to the person. Let them correct the config.
    5. Set DRY_RUN = False (or pass --go) and run again.
    6. Run verify_sort.py afterwards.

MATCHING PRECEDENCE
    OUTINGS         time-window match against an activity (Strava etc.)
    MANUAL_GROUPS   whole-day buckets, for everything the activity log can't explain
    FILE_OVERRIDES  single named files placed by hand

    Outings win. That is what lets you say "12-14 July go to the holiday folder,
    except the trail run on the 13th" without listing files one by one: the
    outing claims its photos and the bucket picks up the remainder.

WHAT IT WRITES
    dc:description and dc:subject inside <rdf:Description>, and optionally
    ext1:DateTimeOriginal for files whose EXIF was stripped. Files that already
    carry a description or subject are left alone and reported.
"""

import argparse
import datetime as dt
import os
import re
import shutil
import sys
import tarfile

# ============================================================================
# CONFIG
# ============================================================================

DRY_RUN = True

LIB = None  # None -> parent of this script's directory
SRC = "CameraRoll"

TOLERANCE_MIN = 30  # slack before an activity starts / after it ends
MOVE_VIDEOS = True  # .mp4 travels with the photos of the same day
BACKUP_SIDECARS = True  # tar.gz every sidecar before editing it
# "double" -> photo.jpg.xmp (the current convention, and the default)
# "single" -> photo.xmp     (legacy; still read, but not written for new files)
# "keep"   -> leave each sidecar named as it was found
SIDECAR_STYLE = "double"

# Time-window matches. start/end are local ISO timestamps; end = start + elapsed.
#   folder  path relative to the library root
#   sports  list of sport tags, always a list even for one. A day often
#           deserves more than one: what the tracker recorded, and what you
#           were actually doing. A climbing day logged as a Hike wants both,
#           and searching either finds it.
#   people  first names appended to the folder name and added as tags
OUTINGS = [
    # {
    #     "start":  "2026-07-15T07:46:41",
    #     "end":    "2026-07-15T09:37:05",
    #     "sports": ["TrailRun"],
    #     "folder": "2026/2026-07-15_EagleRidge",
    #     "people": [],
    #     "desc":   "Trail run below Eagle Ridge (15.5 km, 482 m D+, 1h45)",
    # },
    # {
    #     # recorded as a Hike, but the day was climbing — tag both
    #     "start":  "2026-08-15T08:10:35",
    #     "end":    "2026-08-15T16:14:36",
    #     "sports": ["RockClimbing", "Hike"],
    #     "folder": "2026/2026-08-15_NorthCrag_Alice",
    #     "people": ["Alice"],
    #     "desc":   "Climbing at North Crag with Alice",
    # },
]

# Whole-day buckets. desc may be None, in which case only tags are written.
MANUAL_GROUPS = [
    # {
    #     "dates":  ["2026-07-12", "2026-07-13", "2026-07-14"],
    #     "folder": "2026/2026-07-12+13+14_WillowLake",
    #     "tags":   ["Family", "Holiday"],
    #     "desc":   "Camping holiday at Willow Lake",
    # },
]

# Files that can't be matched by timestamp (stripped EXIF, odd filename).
# date_taken is optional and stamps ext1:DateTimeOriginal when the file has none.
FILE_OVERRIDES = {
    # "IMG-20250727-WA0008.jpg": {
    #     "folder":     "2025/2025-07-27_RidgeTrail_John",
    #     "tags":       ["TrailRun", "John"],
    #     "desc":       "Fox Hill trail with John (15.0 km, 1031 m D+, 2h17)",
    #     "date_taken": "2025-07-27T08:54:11+02:00",
    # },
}

# ============================================================================

# Accepts an edit-variant suffix after the timestamp: photo(0).jpg from
# Google Photos, photo~2.jpg, photo_001.jpg. Those carry the SAME capture
# time as the original, so they belong wherever the original goes —
# rejecting them silently separates a crop from the shot it came from.
NAME_RE = re.compile(r"^(\d{8})_(\d{6})[^.]*\.(jpg|jpeg|heic|mp4|mov)$", re.I)
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
EXIF_NS = "http://ns.adobe.com/exif/1.0/"


def xml_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def photo_time(fname):
    m = NAME_RE.match(fname)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def find_sidecar(src, f):
    """Locate a sidecar. Current convention first, legacy fallback second.

        photo.jpg.xmp   full filename + .xmp   <- current, preferred
        photo.xmp       basename + .xmp        <- legacy, still read

    Order matters: if a file somehow has both, the current-convention one is
    authoritative and the legacy one is a leftover.
    """
    base = os.path.splitext(f)[0]
    for cand in (os.path.join(src, f + ".xmp"), os.path.join(src, base + ".xmp")):
        if os.path.exists(cand):
            return cand
    return None


def sidecar_target_name(f, side):
    """What the sidecar should be called in the destination."""
    if SIDECAR_STYLE == "double":
        return f + ".xmp"
    if SIDECAR_STYLE == "single":
        return os.path.splitext(f)[0] + ".xmp"
    return os.path.basename(side)


def match(ts, fname):
    """Return {folder, desc, tags, date_taken} or None. Outings beat buckets."""
    if fname in FILE_OVERRIDES:
        o = dict(FILE_OVERRIDES[fname])
        o.setdefault("tags", [])
        o.setdefault("desc", None)
        return o
    if ts is None:
        return None

    slack = dt.timedelta(minutes=TOLERANCE_MIN)
    for o in OUTINGS:
        start = dt.datetime.fromisoformat(o["start"])
        end = dt.datetime.fromisoformat(o["end"])
        if start - slack <= ts <= end + slack:
            return {
                "folder": o["folder"],
                "desc": o["desc"],
                "tags": list(o.get("sports", [])) + list(o.get("people", [])),
                "date_taken": None,
            }

    day = ts.strftime("%Y-%m-%d")
    for g in MANUAL_GROUPS:
        if day in g["dates"]:
            return {
                "folder": g["folder"],
                "desc": g.get("desc"),
                "tags": list(g.get("tags", [])),
                "date_taken": None,
            }
    return None


def set_date_taken(xml, iso):
    """Add ext1:DateTimeOriginal, declaring the exif namespace if absent.

    Needed for WhatsApp images and anything else that arrived with its EXIF
    stripped: without a capture time the photo has no date at all in the index.
    """
    if "DateTimeOriginal" in xml:
        return xml, "dateTaken already present, left alone"
    m = re.search(rf'xmlns:(\w+)="{re.escape(EXIF_NS)}"', xml)
    prefix = m.group(1) if m else "ext1"
    if not m:
        xml = xml.replace("<x:xmpmeta ", f'<x:xmpmeta xmlns:{prefix}="{EXIF_NS}" ', 1)
    d = re.search(r"<rdf:Description\b", xml)
    if not d:
        return xml, "!! no rdf:Description, dateTaken NOT set"
    return (
        xml[: d.end()] + f' {prefix}:DateTimeOriginal="{iso}"' + xml[d.end() :],
        f"dateTaken set to {iso}",
    )


def enrich_sidecar(path, o, dry):
    """Insert dc:description / dc:subject. Never overwrite what's already there."""
    with open(path, encoding="utf-8") as fh:
        xml = fh.read()

    if "<dc:description" in xml or "<dc:subject" in xml:
        return "already enriched, left alone"

    date_note = ""
    if o.get("date_taken"):
        xml, date_note = set_date_taken(xml, o["date_taken"])

    block = ""
    if o.get("desc"):
        block += (
            f'<dc:description {DC_NS}><rdf:Alt><rdf:li xml:lang="x-default">'
            f"{xml_escape(o['desc'])}</rdf:li></rdf:Alt></dc:description>"
        )
    if o.get("tags"):
        tags_xml = "".join(f"<rdf:li>{xml_escape(t)}</rdf:li>" for t in o["tags"])
        block += f"<dc:subject {DC_NS}><rdf:Bag>{tags_xml}</rdf:Bag></dc:subject>"

    if block:
        if "</rdf:Description>" in xml:
            xml = xml.replace("</rdf:Description>", block + "</rdf:Description>", 1)
        else:
            # Sidecars with no EXIF at all use a self-closing <rdf:Description/>.
            # Expand it, otherwise the enrichment silently writes nothing while
            # the move still reports success.
            m = re.search(r"(<rdf:Description\b[^>]*?)\s*/>", xml)
            if not m:
                return "!! no rdf:Description anchor, NOTHING WRITTEN"
            xml = (
                xml[: m.start()] + m.group(1) + ">" + block + "</rdf:Description>" + xml[m.end() :]
            )
    elif not date_note:
        return "nothing to write"

    if not dry:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(xml)

    note = []
    if o.get("desc"):
        note.append("description")
    if o.get("tags"):
        note.append(f"subject {o['tags']}")
    if date_note:
        note.append(date_note)
    return " + ".join(note) if note else "nothing to write"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", default=LIB)
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--go", action="store_true", help="actually write (overrides DRY_RUN)")
    args = ap.parse_args()

    lib = args.lib or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = args.src if os.path.isabs(args.src) else os.path.join(lib, args.src)
    dry = DRY_RUN and not args.go

    if not os.path.isdir(src):
        sys.exit(f"source folder not found: {src}")

    mode = "DRY RUN - nothing will be written" if dry else "*** LIVE RUN ***"
    print("=" * 78)
    print(mode)
    print(f"library   : {lib}")
    print(f"source    : {src}")
    print(
        f"tolerance : +/- {TOLERANCE_MIN} min | videos: {MOVE_VIDEOS} | sidecars: {SIDECAR_STYLE}"
    )
    print("=" * 78)

    files = sorted(f for f in os.listdir(src) if f.lower().endswith(MEDIA_EXT))
    plan, unmatched, unparseable, no_sidecar = [], [], [], []

    for f in files:
        if f.lower().endswith((".mp4", ".mov")) and not MOVE_VIDEOS:
            continue
        ts = photo_time(f)
        if ts is None and f not in FILE_OVERRIDES:
            unparseable.append(f)
            continue
        o = match(ts, f)
        if not o:
            unmatched.append(f)
            continue
        side = find_sidecar(src, f)
        if side is None:
            no_sidecar.append(f)
        plan.append((f, side, o))

    if BACKUP_SIDECARS and not dry and any(s for _f, s, _o in plan):
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        outdir = os.path.join(lib, "_sorting")
        os.makedirs(outdir, exist_ok=True)
        tgz = os.path.join(outdir, f"xmp_backup_{stamp}.tar.gz")
        with tarfile.open(tgz, "w:gz") as tar:
            for _f, side, _o in plan:
                if side:
                    tar.add(side, arcname=os.path.basename(side))
        print(f"sidecar backup: {tgz}\n")

    current = None
    moved = 0
    for f, side, o in sorted(plan, key=lambda p: (p[2]["folder"], p[0])):
        d = os.path.join(lib, *o["folder"].split("/"))
        if d != current:
            current = d
            new_note = "" if os.path.isdir(d) else "   [NEW FOLDER]"
            print(f"\n{o['folder']}/{new_note}")
            if not os.path.isdir(d) and not dry:
                os.makedirs(d)
        dst = os.path.join(d, f)
        if os.path.exists(dst):
            print(f"   !! TARGET EXISTS, skipped : {f}")
            continue
        print(f"   {f}")
        if side:
            note = enrich_sidecar(side, o, dry)
            tgt = sidecar_target_name(f, side)
            ren = (
                ""
                if os.path.basename(side) == tgt
                else f"  (renamed {os.path.basename(side)} -> {tgt})"
            )
            print(f"      sidecar: {note}{ren}")
        else:
            print("      sidecar: NONE - moves without description or tags")
        if not dry:
            shutil.move(os.path.join(src, f), dst)
            if side:
                shutil.move(side, os.path.join(d, sidecar_target_name(f, side)))
        moved += 1

    print("\n" + "-" * 78)
    print(f"to move            : {moved}")
    if no_sidecar:
        print(f"no sidecar         : {len(no_sidecar)}  (metadata cannot be written for these)")
        for f in no_sidecar:
            print(f"      {f}")
    if unparseable:
        print(
            f"unparseable names  : {len(unparseable)}  (no YYYYMMDD_HHMMSS, not in FILE_OVERRIDES)"
        )
        for f in unparseable:
            print(f"      {f}")
    print(f"left in source     : {len(unmatched)}")
    for f in unmatched:
        print(f"      {f}")
    print("-" * 78)
    print(mode)


if __name__ == "__main__":
    main()
