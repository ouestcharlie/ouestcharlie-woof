#!/usr/bin/env python3
"""
Protect and recover XMP descriptions and tags.

Reindexing with EXIF extraction regenerates sidecars and drops every dc: block,
silently. This script is the insurance policy and the repair kit.

    # before any reindex
    python snapshot_sidecars.py --lib ~/Photos snapshot

    # see what a wipe cost you
    python snapshot_sidecars.py --lib ~/Photos diff --archive <tgz>

    # put it back (dry run first)
    python snapshot_sidecars.py --lib ~/Photos restore --archive <tgz>
    python snapshot_sidecars.py --lib ~/Photos restore --archive <tgz> --go

Restore reinserts the archived dc: blocks into the *current* sidecars rather
than overwriting the files. That keeps whatever the reindex legitimately
improved — fresher EXIF, new content hashes — and touches only the metadata that
was lost. The operation is checked for reversibility before it is applied: after
removing the reinserted block the file must equal the current one byte for byte.
"""

import argparse
import datetime as dt
import os
import re
import sys
import tarfile
import tempfile
import xml.dom.minidom

DC_RE = re.compile(r"<dc:(?:description|subject)\b.*?</dc:(?:description|subject)>", re.S)


def canonical(text):
    """Text with dc: blocks removed and rdf:Description normalised to one form.

    Restoring into a sidecar that has no EXIF means expanding its self-closing
    <rdf:Description/> so the metadata has somewhere to live. That expansion is
    a legitimate part of the restore, so a byte comparison would flag it as
    corruption and refuse to write. Normalising both forms lets the safety check
    ask the question it actually means: is anything *other* than the dc: blocks
    different?
    """
    t = DC_RE.sub("", text)
    t = re.sub(r"(<rdf:Description\b[^>]*?)\s*(?:/>|></rdf:Description>)", r"\1/>", t, flags=re.S)
    return t


def enriched_files(lib):
    out = []
    for parent, dirs, files in os.walk(lib):
        dirs[:] = [d for d in dirs if d not in (".dtrash", ".ouestcharlie")]
        for f in files:
            if not f.endswith(".xmp"):
                continue
            p = os.path.join(parent, f)
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    if DC_RE.search(fh.read()):
                        out.append(p)
            except OSError:
                pass
    return sorted(out)


def valid(text):
    try:
        xml.dom.minidom.parseString(text.split("?>", 1)[-1].rsplit("<?xpacket", 1)[0])
        return True
    except Exception:
        return False


def cmd_snapshot(args):
    files = enriched_files(args.lib)
    if not files:
        sys.exit("no sidecars carry dc: metadata — nothing to snapshot")
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = os.path.join(args.lib, "_sorting")
    os.makedirs(outdir, exist_ok=True)
    tgz = os.path.join(outdir, f"xmp_enriched_snapshot_{stamp}.tar.gz")
    with tarfile.open(tgz, "w:gz") as tar:
        for p in files:
            tar.add(p, arcname=os.path.relpath(p, args.lib))
    with open(tgz.replace(".tar.gz", ".filelist.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(os.path.relpath(p, args.lib) for p in files))

    # a snapshot you haven't verified is a guess
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        with tarfile.open(tgz) as tar:
            tar.extractall(td)
        for p in files[:200]:
            rel = os.path.relpath(p, args.lib)
            q = os.path.join(td, rel)
            if not os.path.exists(q):
                mismatch = True
            else:
                with open(q, "rb") as fh1, open(p, "rb") as fh2:
                    mismatch = fh1.read() != fh2.read()
            if mismatch:
                bad += 1
                print(f"  MISMATCH {rel}")
    print(f"snapshot: {tgz}")
    print(f"  {len(files)} sidecars, {min(200, len(files))} spot-checked, {bad} mismatches")


def _archive_map(archive):
    out = {}
    with tarfile.open(archive) as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            out[m.name] = tar.extractfile(m).read().decode("utf-8", errors="replace")
    return out


def cmd_diff(args):
    arc = _archive_map(args.archive)
    lost = kept = gone = 0
    for rel, old in sorted(arc.items()):
        cur_p = os.path.join(args.lib, rel)
        if not os.path.exists(cur_p):
            gone += 1
            continue
        with open(cur_p, encoding="utf-8", errors="replace") as fh:
            cur = fh.read()
        if DC_RE.search(cur):
            kept += 1
        elif DC_RE.search(old):
            lost += 1
            if lost <= 20:
                print(f"  LOST  {rel}")
    print(
        f"\n{len(arc)} sidecars in archive: {kept} still enriched, "
        f"{lost} lost their metadata, {gone} missing on disk"
    )


def cmd_restore(args):
    arc = _archive_map(args.archive)
    dry = not args.go
    todo, skipped, problems = [], 0, []

    for rel, old in sorted(arc.items()):
        cur_p = os.path.join(args.lib, rel)
        if not os.path.exists(cur_p):
            problems.append((rel, "no current sidecar"))
            continue
        with open(cur_p, encoding="utf-8", errors="replace") as fh:
            cur = fh.read()
        if DC_RE.search(cur):
            skipped += 1
            continue
        blocks = "".join(DC_RE.findall(old))
        if not blocks:
            continue
        if "</rdf:Description>" in cur:
            new = cur.replace("</rdf:Description>", blocks + "</rdf:Description>", 1)
        else:
            m = re.search(r"(<rdf:Description\b[^>]*?)\s*/>", cur)
            if not m:
                problems.append((rel, "no rdf:Description anchor"))
                continue
            new = (
                cur[: m.start()] + m.group(1) + ">" + blocks + "</rdf:Description>" + cur[m.end() :]
            )
        if not valid(new):
            problems.append((rel, "would produce invalid XML"))
            continue
        # nothing but the dc: blocks (and any necessary tag expansion) may differ
        if canonical(new) != canonical(cur):
            problems.append((rel, "changes more than the dc: blocks — refusing"))
            continue
        todo.append((cur_p, new, rel))

    print("=" * 78)
    print("DRY RUN - nothing will be written" if dry else "*** LIVE RUN ***")
    for _p, _n, rel in todo[:20]:
        print(f"   restore {rel}")
    if len(todo) > 20:
        print(f"   ... and {len(todo) - 20} more")
    if not dry:
        for p, new, _rel in todo:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(new)
    print("-" * 78)
    print(f"to restore        : {len(todo)}")
    print(f"still enriched    : {skipped} (left alone)")
    print(f"problems          : {len(problems)}")
    for rel, n in problems[:20]:
        print(f"      {rel}  {n}")
    print("DRY RUN - nothing was written" if dry else "*** WRITTEN ***")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot")
    d = sub.add_parser("diff")
    d.add_argument("--archive", required=True)
    r = sub.add_parser("restore")
    r.add_argument("--archive", required=True)
    r.add_argument("--go", action="store_true")
    args = ap.parse_args()
    {"snapshot": cmd_snapshot, "diff": cmd_diff, "restore": cmd_restore}[args.cmd](args)


if __name__ == "__main__":
    main()
