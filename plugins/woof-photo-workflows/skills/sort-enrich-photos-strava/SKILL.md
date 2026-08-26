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

Everything else comes from looking at the library:

- **Copy the folder layout already in use.** Don't assume a shape — it might be
  `<year>/<date>_<place>/`, `Events/<name>/`, or a flat pile. The script takes a
  literal relative path of any depth, so any layout works. Sample a few places;
  conventions drift over years.
- **Read the tag facets** before inventing a tag. This is how you avoid
  `Climbing` next to an established `RockClimbing`.

**Nothing to copy** — empty library, first sort — means proposing two or three
concrete shapes with examples and confirming once. It's the hardest decision to
undo: a thousand files under a disliked scheme is a thousand files to move again.

**Near-duplicate tags** accumulate: `Oliv` beside `Olivier`, an accented and
unaccented spelling. The tell is a pair differing by case, accent or prefix with
lopsided counts — the rare one is usually the mistake. Ask which is canonical,
then *retag the smaller set* rather than remembering an alias. A correction in a
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

Afterwards the index is stale: editing a sidecar doesn't change its media file,
so edits stay invisible to search until reindexed.

## What gets written

```xml
<dc:description xmlns:dc="http://purl.org/dc/elements/1.1/">
  <rdf:Alt><rdf:li xml:lang="x-default">…</rdf:li></rdf:Alt>
</dc:description>
<dc:subject xmlns:dc="http://purl.org/dc/elements/1.1/">
  <rdf:Bag><rdf:li>RockClimbing</rdf:li><rdf:li>Hike</rdf:li></rdf:Bag>
</dc:subject>
```

- **Description**: activity title, its own description if any, then stats —
  `Trail run below Eagle Ridge (15.5 km, 482 m D+, 1h45)`. Match the library's
  existing format.
- **Tags**: sport types in PascalCase plus first names. `"sports"` is always a
  list. **A day often deserves more than one** — trackers record what they can
  measure, so a climbing day is logged as a Hike. Tag both and either search
  finds it.
- **Nicknames** map to the library's spelling; check the tag counts first.
- **Keep accents.** Files are UTF-8.
- The exif prefix varies (`ext0`, `ext1`, `exif`) — detect it from the namespace
  URI, not the prefix.
- **Never overwrite an existing description.** Skip and report.

## Snapshot before anything destructive

Reindexing can regenerate sidecars and discard `dc:` metadata silently while
reporting success. Check the indexer's docs for which flags do that.

```
python scripts/snapshot_sidecars.py --lib <library> snapshot
```

Thousands of sidecars compress to a couple of hundred kilobytes — no reason to
skip it, including when you believe the flags are safe.

To recover: `diff` to see what was lost, then `restore`, which reinserts `dc:`
blocks into the *current* sidecars rather than overwriting files. A regeneration
usually improved them, so keep the new sidecar and put the metadata back.

**Distinguish lost from never written.** Files with no sidecar at the time never
received anything — no backup will restore it.

## Reporting

Folder, file count, sidecar count — then what needs attention: metadata that
couldn't be written, files left behind, names you invented. Surprises at the top.

If something changed on disk that you didn't do, say so plainly and stop. It
might be a sync client mid-flight, or the person deleting from another device.
