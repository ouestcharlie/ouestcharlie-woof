---
name: sort-enrich-photos-strava
description: Sort photos and videos into per-outing folders by matching them against a Strava activity log, and write the outing's description and sport type into their XMP sidecars. Use this whenever someone wants photos linked to a hike, ride, run, ski tour, race or any recorded activity; whenever they mention Strava, GPX, an activity log or a training log alongside photos; whenever they ask which photos were taken during an outing; and whenever they want a camera roll filed into folders named after the outings that produced them. Also use it when they want captions or tags generated from activity data rather than typed by hand.
---

# Sorting photos against an activity log

The log knows what they did and when; photos carry timestamps. That's enough to
file and caption with almost no human input — which is what makes it worth
automating, and what makes it dangerous: a wrong rule moves hundreds of files
confidently.

So: propose a complete plan, get approval, execute, verify.

This covers what the log can explain, typically ~15% of a camera roll. The rest
is the `sort-enrich-photos-interactive-clusters` skill.

## Conventions come from the library

No settings file. Tunable knobs and per-run decisions live in
`scripts/sort_photos.py` — read it before filling it in; the comments explain
each field.
If the skill folder is read-only, copy the whole `scripts/` folder and work
there: the scripts share `media_ext.py` (the media extensions) and fail without it.

Everything else comes from looking at the library:

- **Copy the folder layout already in use.** Don't assume a shape — it might be
  `<year>/<date>_<place>/`, `Events/<name>/`, or a flat pile. The script takes a
  literal relative path of any depth, so any layout works. Sample a few places;
  conventions drift over years.
- **Read the tag facets** before inventing a tag. This is how you avoid
  `Climbing` next to an established `Sport|Climbing|Rock`.

**Tags can be hierarchical**: levels separated by `|`, e.g. `Places|Canada|Ontario`
or `Activities|Hiking` — the darktable and Lightroom convention. A search for
`Places|Canada` finds everything below it, and a search for `Ontario` finds it at
any level. Matching ignores case, and the scripts never add a tag the sidecar
already has in another case: they keep the existing spelling. Facet keys are paths, ancestors included, so the facets show which
hierarchy the library already uses. Put a new tag under an existing root rather
than starting a parallel tree; keep hierarchies short and stable, and don't
invent deep trees nobody asked for. A flat tag is fine when the library has no
hierarchy for it.

**Nothing to copy** — empty library, first sort — means proposing two or three
concrete shapes with examples and confirming once. It's the hardest decision to
undo: a thousand files under a disliked scheme is a thousand files to move again.

**Near-duplicate tags** accumulate: `Oliv` beside `Olivier`, an accented and
unaccented spelling. The tell is a pair differing by accent or prefix with
lopsided counts (case differences are already merged in search and facets) — the rare one is usually the mistake. Ask which is canonical,
then *retag the smaller set* (see Renaming a tag) rather than remembering an alias. A correction in a
config file must be re-read forever; one applied to the photos is simply true.

The library can't go stale. A settings file records what someone believed the
conventions were; the folders and tags record what they are.

## 1. Index and size up

Index the staging partition, get a count and date range, and **also list the
folder directly** — the index and filesystem disagree in informative ways.

**Indexing is asynchronous.** The tool returns immediately; Woof posts
*"Indexing complete."* into the conversation. Say you're waiting and stop.
Polling can read a half-built index, whose counts look real and are wrong.

**Scoping is not recursive.** A partition is one folder, not its children —
`2026` indexes nothing inside `2026/2026-08-15_Crag/`. List every folder you
touched plus the source, so departed files are dropped from it. A scope matching
nothing looks exactly like a successful run.

Three things to count and report rather than skip silently:

- **No sidecar** — metadata for those files goes nowhere. Indexing first
  sometimes creates them.
- **Unparseable names** — messaging-app exports have no timestamp at all.
- **Cloud placeholders** — a dehydrated file has real size and real magic bytes
  but no local content, so it can't be read. Symptom: media present on disk that
  the indexer consistently skips.

**Sidecar naming is fixed**, so don't ask: `photo.jpg.xmp` is current and what
you write; `photo.xmp` is legacy and still read. Current form first, legacy as
fallback — both scripts do this. Libraries hold a mixture; anything globbing one
form sees half the files.

## 2. Correlate

Fetch activities covering the photo date range in month-sized chunks — the
Strava connector is rate-limited per minute and per day, so bound the range to
the dates you actually have photos for rather than sweeping whole years.

A photo matches when its capture time falls in the window widened by a
tolerance:

```
start - tolerance  <=  capture time  <=  end + tolerance
```

**Default ~30 minutes.** People shoot the trailhead before starting the watch
and the car park after stopping it. Take capture time from the sidecar's
`DateTimeOriginal`, not file mtime.

### Routine activities: detect, then confirm

Logs are full of commutes and auto-detected walks. Work them out rather than
asking for patterns — strongest signal first:

- **A repeated name.** A title appearing many times was generated, not typed.
- **Regularity** — same time of day, most weekdays, often twice daily.
- **Tight clustering** in distance and duration.

Distance alone is a bad signal; a real outing can be short. Then ask:

> "Morning ride" (14×, ~3.5 km, weekday mornings) and "Evening ride" (14×)
> repeat in your log — routine trips rather than outings. Leave them out?

Confirm because the boundary is personal: a daily run someone is proud of looks
statistically identical to a commute, and the failure is silent either way.

### Other traps

**Expect a low hit rate.** Don't widen the tolerance to chase it — you'll sweep
in unrelated photos. An unmatched photo is a question, not a defect.

**Overlapping activities** need a rule: shorter window wins, since it was
recorded deliberately. Say which you chose.

**A recording can under-report the day** — watch dies, tracker forgotten. If
photos continue well past the end, ask before trusting the window; a whole-day
bucket may be right.

**Timezones.** If matches look off by a round number of hours, suspect this first.

## 3. Plan and dry-run

Fill in `OUTINGS`, and `FILE_OVERRIDES` for files with no usable timestamp.
Leave `DRY_RUN = True`, run, show the output.

**Check the folder doesn't already exist** — often it does, sometimes empty and
waiting. Search loosely enough to catch older spellings.

**Mark derived names as guesses.** An invented name nobody corrects because they
didn't realise you made it up is worse than a question.

Show the plan and stop. On corrections, edit the config and re-run rather than
patching by hand.

## 4. Execute and verify

Flip `DRY_RUN = False`, then run `scripts/verify_sort.py`: counts per folder,
XML validity, orphaned sidecars, legacy-named sidecars.

Not ceremony — a sidecar with no EXIF has a self-closing `<rdf:Description/>`,
and naive insertion writes nothing while the move still reports success.

Then refresh the index so search sees the new folders, descriptions and tags:
run `index_library` with `partition_scope` set to the source and destination
folders, and no force flag. An incremental run indexes the moved photos and
re-reads every sidecar whose modification time changed, which covers everything
the scripts wrote. It does not rebuild thumbnails.

## What gets written

```xml
<dc:description xmlns:dc="http://purl.org/dc/elements/1.1/">
  <rdf:Alt><rdf:li xml:lang="x-default">…</rdf:li></rdf:Alt>
</dc:description>
<lr:hierarchicalSubject xmlns:lr="http://ns.adobe.com/lightroom/1.0/">
  <rdf:Bag><rdf:li>Sport|Climbing|Rock</rdf:li><rdf:li>Sport|Hike</rdf:li></rdf:Bag>
</lr:hierarchicalSubject>
<dc:subject xmlns:dc="http://purl.org/dc/elements/1.1/">
  <rdf:Bag><rdf:li>Sport</rdf:li><rdf:li>Climbing</rdf:li><rdf:li>Rock</rdf:li>
    <rdf:li>Hike</rdf:li></rdf:Bag>
</dc:subject>
```

Tags are written the way darktable writes them, by the scripts: each tag path in
`lr:hierarchicalSubject` (flat tags too), every level of every path in
`dc:subject`. darktable reads only `lr:hierarchicalSubject` once it exists, so
never edit `dc:subject` alone.

- **Description**: activity title, its own description if any, then stats —
  `Trail run below Eagle Ridge (15.5 km, 482 m D+, 1h45)`. Match the library's
  existing format.
- **Tags**: sport paths plus first names. `"sports"` is always a list.
- **Sports are paths, split from the Strava type.** Strava's types are compound
  names; split them into levels under the library's sport root, general to
  specific, so a search for `Ski` finds every kind of ski day:

  | Strava type | Tag |
  |---|---|
  | `BackcountrySki` | `Sport|Ski|Backcountry` |
  | `NordicSki` | `Sport|Ski|Nordic` |
  | `TrailRun` | `Sport|Run|Trail` |
  | `GravelRide` | `Sport|Ride|Gravel` |
  | `RockClimbing` | `Sport|Climbing|Rock` |
  | `Hike`, `Run` | `Sport|Hike`, `Sport|Run` |

  **The library's facets decide the root and the spelling.** If they show
  `Activities|Ski|…` or lowercase levels, use that, not this table: matching
  ignores case, but the scripts only reuse the spelling already in the same
  sidecar, so a fresh sidecar gets exactly what you write. With no sport
  hierarchy yet, propose `Sport|…` with capitalised levels, confirm it once,
  and keep to it.
- **Flat sport tags already in the library** (`BackcountrySki`, `TrailRun`)
  would start a second tree next to the new paths. Offer to retag them first
  (see Renaming a tag), one Strava type at a time.
- **A day often deserves more than one** — trackers record what they can
  measure, so a climbing day is logged as a Hike. Tag both and either search
  finds it.
- **Nicknames** map to the library's spelling; check the tag counts first.
- **Keep accents.** Files are UTF-8.
- The exif prefix varies (`ext0`, `ext1`, `exif`) — detect it from the namespace
  URI, not the prefix.
- **Never overwrite an existing description.** Skip and report.

## Renaming a tag

Use `scripts/retag_files.py` to move flat sport tags into the hierarchy, merge
a near-duplicate into the canonical tag, or fix a spelling. No moves; each
sidecar is edited in place.

```
python scripts/retag_files.py --lib <library> \
    --old-tag BackcountrySki --new-tag 'Sport|Ski|Backcountry' \
    [--folder 2024 --folder 2025/2025-02_Alps]
```

- **Dry run first**, then add `--go`. The live run backs up every sidecar it
  changes into `_sorting/xmp_backup_retagging_<stamp>.tar.gz`.
- **Scope**: without `--folder`, the whole library. Each `--folder` is relative
  to the library and includes its subfolders. Show the dry-run count and
  confirm before `--go`.
- **One exact path per run.** `--old-tag Sport|Ski` doesn't touch
  `Sport|Ski|Nordic`. Matching ignores case and spaces around `|`.
- The old path's levels leave `dc:subject` unless another path still uses them,
  the new path takes the spelling the sidecar already has, and a sidecar with
  only `dc:subject` gets `lr:hierarchicalSubject` too.
- A second run reports zero to change.

Then run `index_library` incrementally, with no force flag. Scoping isn't
recursive, so list every changed folder, or omit `partition_scope`: unchanged
sidecars are skipped at the cost of a directory listing.

## Snapshot before anything destructive

`index_library` with `force_extract_exif=True` regenerates sidecars from the
media files and can discard descriptions and tags (`dc:` and
`lr:hierarchicalSubject`) silently while reporting success. A plain incremental
run or `force_full_index=True` only reads sidecars; the one exception is a
deleted sidecar, which is re-created without its metadata.

Another tool can also drop them: darktable rewrites the sidecar at every sync,
and if it never imported the description and tags, it writes the sidecar
without them. The next incremental index follows the sidecar, so they vanish
from search too. Restore from the snapshot if that happens.

```
python scripts/snapshot_sidecars.py --lib <library> snapshot
```

Thousands of sidecars compress to a couple of hundred kilobytes — no reason to
skip it before any `force_extract_exif` run or before opening the folders in
darktable, including when you believe the flags are safe.

To recover: `diff` to see what was lost, then `restore`, which reinserts the
description and tag blocks into the *current* sidecars rather than overwriting files. A regeneration
usually improved them, so keep the new sidecar and put the metadata back.
A plain incremental `index_library` afterwards makes the restored metadata
searchable again.

**Distinguish lost from never written.** Files with no sidecar at the time never
received anything — no backup will restore it.

## Reporting

Folder, file count, sidecar count — then what needs attention: metadata that
couldn't be written, files left behind, names you invented. Surprises at the top.

If something changed on disk that you didn't do, say so plainly and stop. It
might be a sync client mid-flight, or the person deleting from another device.
