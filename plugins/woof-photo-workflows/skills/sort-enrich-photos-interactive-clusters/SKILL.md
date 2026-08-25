---
name: sort-enrich-photos-interactive-clusters
description: Sort a camera roll into per-event folders by grouping photos into day clusters, asking the person to identify each one, and writing a description and tags into their XMP sidecars. Use this whenever someone wants to tidy, sort, file, organise or clean up a folder of photos or videos; whenever they mention a camera roll, import folder, staging folder or unsorted photos; whenever they want media moved into year or date folders; whenever they want to bulk-tag or caption a folder; whenever they ask which photos are still unsorted; and whenever they say "process this folder" about a pile of photos. Also use it for photos that no activity log can explain — family, home, holidays.
---

# Sorting a camera roll by day clusters

A classification problem, not a file-moving one. Moving files is trivial;
deciding where each photo belongs is the hard part, and the person you're
working with is the only one who knows what a given Tuesday was.

Do all the mechanical work and bring them in only where their knowledge is
required. Their attention is the scarce resource — spend it on the biggest
groups first.

Files move and metadata is rewritten in place, so: **show the plan, get
approval, execute.**

For photos an activity log can match automatically, use
`sort-enrich-photos-strava`.

## Conventions come from the library

No settings file. Tunable knobs and per-run decisions live in
`scripts/sort_photos.py` — read it before filling it in.

Everything else comes from looking at the library:

- **Copy the folder layout already in use.** Don't assume a shape — it might be
  `<year>/<date>_<place>/`, `Events/<name>/`, or a flat pile. The script takes a
  literal relative path of any depth, so any layout works. Sample a few places;
  conventions drift over years.
- **Read the tag facets** before inventing a tag, to avoid `Holidays` next to an
  established `Holiday`.

**Nothing to copy** — empty library, first sort — means proposing two or three
concrete shapes with examples and confirming once. It's the hardest decision to
undo: a thousand files under a disliked scheme is a thousand files to move again.

**Near-duplicate tags** accumulate: `Oliv` beside `Olivier`, an accented and
unaccented spelling. The tell is a pair differing by case, accent or prefix with
lopsided counts — the rare one is usually the mistake. Ask which is canonical,
then *retag the smaller set* rather than remembering an alias. A correction in a
config file must be re-read forever; one applied to the photos is simply true.

## 1. Index and size up

Index the staging partition, get a count and date range, and **also list the
folder directly** — the index and filesystem disagree in informative ways.

**Indexing is asynchronous.** The tool returns immediately; Woof posts
*"Indexing complete."* into the conversation. Say you're waiting and stop.
Polling can read a half-built index, whose counts look real and are wrong.

**Scoping is not recursive.** A partition is one folder, not its children.
List every folder you touched plus the source, so departed files are dropped
from it. A scope matching nothing looks exactly like a successful run.

Count and report rather than skip silently: files with **no sidecar** (metadata
goes nowhere), **unparseable names**, and **cloud placeholders** — a dehydrated
file has real size and magic bytes but no local content, so the indexer skips it.

**Sidecar naming is fixed**, so don't ask: `photo.jpg.xmp` is current and what
you write; `photo.xmp` is legacy and still read. Current first, legacy as
fallback — both scripts do this. Anything globbing one form sees half the files.

## 2. Group by day, densest first

Present the days with the most photos first. **This ordering is the method** —
a handful of dates usually accounts for most of the backlog, so identifying
three groups can file half the folder. Date order instead spends their attention
on stray singles while the big groups wait.

## 3. Show each cluster before asking

Open it in the gallery — searching and displaying are separate operations, so
make sure you've actually rendered it before referring to it.

People recognise their own photos in seconds. If they can't, that's informative:
the group probably doesn't deserve its own folder.

Two things help when a cluster isn't obvious:

- **GPS** from the sidecar. A centroid plus spread says whether it's one place
  or a day out and about. Many phone photos have none — say so rather than
  inventing a location.
- **Neighbouring dates.** Three consecutive days often means one trip. Ask
  *before* naming them separately; getting it wrong means redoing the name.

Ask for folder name, tags and description together — one exchange per cluster.

**Check whether the folder already exists**, searching loosely enough to catch
older spellings. **Mark derived names as guesses**: an invented name nobody
corrects because they didn't realise you made it up is worse than a question.

## 4. Rules for the common case, overrides for the rest

Real days aren't tidy. Express a mostly-one-thing day as a bucket plus an
exception rather than listing files:

> Put 12 and 13 July in the holiday folder, except the trail run.

`scripts/sort_photos.py` applies `OUTINGS`, then `MANUAL_GROUPS` (whole-day
buckets), then `FILE_OVERRIDES`. That precedence is what makes the above work
without enumerating anything.

**Resist adding configuration for a one-off.** An override visible in a plan is
clearer than a rule they'll have forgotten in a year.

## 5. Dry-run, execute, verify

Leave `DRY_RUN = True`, run, and check three things with them: **file counts per
folder** (far off means a cluster wasn't what they thought), **anything left
over**, and **files with no sidecar** — they'll move but stay uncaptioned.

Then flip `DRY_RUN = False` and run `scripts/verify_sort.py`: counts, XML
validity, orphans, legacy-named sidecars. A sidecar with no EXIF has a
self-closing `<rdf:Description/>`; naive insertion writes nothing while the move
still reports success.

Afterwards the index is stale — sidecar edits stay invisible to search until
reindexed.

**Stopping early is a legitimate outcome.** Filing the five biggest clusters and
leaving forty strays is a good result; those forty were never going to be found
by browsing anyway. Offer to stop rather than grinding to completion.

## Tagging folders already filed

Use `scripts/tag_files.py` — no moves, sidecars edited in place.

**Merge, never replace.** Add only what's missing to the existing `rdf:Bag`;
never write a second `dc:subject` or drop what's there.

**Be idempotent.** Skip files already carrying everything requested, so a second
run is a no-op and an interrupted run can be repeated. Verify by running twice
and confirming zero changes — the cheapest check there is.

**Confirm scope before bulk operations.** "Tag all the holiday folders" can mean
four folders or forty. Enumerate, show the count, offer the narrower and wider
readings before touching a thousand files.

## What gets written

```xml
<dc:description xmlns:dc="http://purl.org/dc/elements/1.1/">
  <rdf:Alt><rdf:li xml:lang="x-default">…</rdf:li></rdf:Alt>
</dc:description>
<dc:subject xmlns:dc="http://purl.org/dc/elements/1.1/">
  <rdf:Bag><rdf:li>Family</rdf:li><rdf:li>Holiday</rdf:li></rdf:Bag>
</dc:subject>
```

- **Tags** reuse what the library already has; descriptions are whatever the
  person says, with no template to apply.
- **Keep accents.** Files are UTF-8.
- The exif prefix varies (`ext0`, `ext1`, `exif`) — detect it from the namespace
  URI.
- **Never overwrite an existing description.** Skip and report.

## Snapshot before anything destructive

Reindexing can regenerate sidecars and discard `dc:` metadata silently while
reporting success.

```
python scripts/snapshot_sidecars.py --lib <library> snapshot
```

To recover: `diff` to see what was lost, then `restore`, which reinserts `dc:`
blocks into the *current* sidecars rather than overwriting files.

**Distinguish lost from never written.** Files with no sidecar at the time never
received anything — no backup will restore it.

## Reporting

Folder, file count, sidecar count — then what needs attention: metadata that
couldn't be written, files left behind, names you invented, duplicates now
separated from their originals. Surprises at the top.

If something changed on disk that you didn't do, say so plainly and stop.
