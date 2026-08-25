# Woof photo workflows

Organise a photo library by talking to Claude: link photos and videos to the
activities that produced them, write captions and tags that travel with your
files, and file a camera roll into per-event folders.

Two skills for [Woof](https://github.com/ouestcharlie/ouestcharlie-woof), the
photo server for Claude Desktop.

## Requirements

| | Needed for | Notes |
|---|---|---|
| **Claude Desktop** | Everything | Woof isn't compatible with the web version |
| **Woof** | Everything | Installed separately — see below |
| **Python 3** | The bundled scripts | Standard library only; nothing to `pip install` |
| **Strava MCP Connector** | Matching photos to outings only | Connected separately — see below |

### Install Woof first

This plugin ships skills, not the server. Install Woof by following
[Step by Step Install of OuEstCharlie Woof in Claude Desktop](https://ouestcharlie.github.io/ouestcharlie/2026/05/13/claude-how-to-step-by-step/)
— the `.mcpb` bundle from the
[Woof releases](https://github.com/ouestcharlie/ouestcharlie-woof/releases),
installed through Claude Desktop's Extensions tab.

Keeping the server out of this plugin means one documented install path rather
than two, and no risk of ending up with Woof connected twice.

Check it worked by asking Claude:

> Is OuEstCharlie Woof loaded?

### About the Strava requirement

Linking photos to a hike, ride or ski tour needs an activity log, and the
supported source is the
[Strava MCP Connector](https://support.strava.com/en-us/articles/15401531-strava-mcp-connector)
— a remote server run by Strava, read-only and scoped to your account.

Connect it yourself:

- **Cowork or claude.ai:** Customize → Connectors → **+** → search "Strava" →
  Connect, and authorise with your Strava account.
- **Claude Code:** `claude mcp add --transport http strava-mcp https://mcp.strava.com/mcp`

**It needs an active Strava subscription** — included with the standard
subscription, the Runna bundle, Family and Student plans, and the military,
educator and medical discounts.

**A subscription doesn't guarantee access yet.** Strava began rolling the
connector out on 1 June 2026 and is still doing so, so eligible subscribers may
simply not have it yet. Check <https://www.strava.com/settings/mcp>, or ask
Claude:

> Am I eligible for the Strava MCP?

Without access you'll see **only** an `eligibility` tool and nothing else —
that's the symptom, and there's no error message.

Revoke any time from Strava → Settings → [My Apps](https://www.strava.com/settings/apps).

**Most of this works without Strava.** Tagging folders, filing a camera roll
into per-event folders, and recovering lost metadata need only Woof. Strava is
required for exactly one thing: working out which photos belong to which
recorded activity.

**If you track activities somewhere else**, you're not stuck. The skills take a
normalised list of activities — start time, end time, name, sport, description,
distance/elevation/duration — and don't care where it came from. A GPX export, a
Garmin or Komoot download, or a hand-written JSON file all work. Say so and
Claude will read your file instead of calling Strava.

## Start here

With Woof installed and a library indexed, ask Claude in ordinary language:

> Sort the photos in my camera roll folder.

Step-by-step walkthroughs live on the
[Ouestcharlie blog](https://ouestcharlie.github.io/ouestcharlie/) — one for
sorting against a Strava activity log, one for grouping by day when there's no
log to match against.

## What's in the box

Two skills, one per way of working. Each does the whole job — group, file,
caption, verify — rather than half of it, so you never need both at once.

### `sort-enrich-photos-strava`

Matches photos to your recorded outings by timestamp, files them into folders
named after each outing, and writes the activity's description and sport type
into the sidecars. Detects and confirms which log entries are routine commutes
rather than outings.

### `sort-enrich-photos-interactive-clusters`

For everything an activity log can't explain. Groups unsorted photos by day,
shows you the biggest group first, and files each one under the name you give
it. Also handles tagging folders that are already filed.

Both carry the same safety net — snapshotting sidecars before anything
destructive, and restoring metadata a reindex discarded.

**There is no settings file.** Tunable knobs and per-run decisions live in each
skill's `scripts/sort_photos.py`, heavily commented. Everything else — folder
naming, which tags are in use, how people are spelled — is read from the library
itself, by looking at how it is already organised and what tags it already uses.

That's deliberate. A settings file records what someone believed the conventions
were the day they wrote it; the folders and tags record what they actually are.

## Design notes

**Nothing is written without a plan you approve first.** Every script defaults
to a dry run. This is the only moment where someone who knows the answer can
catch a wrong assumption, and it costs one message.

**Metadata is the irreplaceable part.** Thumbnails regenerate and indexes
rebuild; the sentence describing which peak you climbed and with whom does not.
Everything here is arranged around not losing that — snapshots before
destructive operations, merging rather than replacing tags, never overwriting a
description someone wrote.

**Operations are idempotent.** Run twice, and the second run reports zero
changes. That makes an interrupted run safe to simply repeat, and it's the
cheapest check that an operation is well-behaved.

**Sidecar naming.** `photo.jpg.xmp` is the current convention and what gets
written; `photo.xmp` is the older form and is still read. Libraries hold a
mixture, which is expected — but anything globbing for one form sees only half
the files.

**Woof's tool descriptions are the source of truth.** The skills deliberately
don't restate the API — that goes stale fast. They cover judgement and habits;
the server documents itself.

## Licence

MIT — see [LICENSE](LICENSE).
