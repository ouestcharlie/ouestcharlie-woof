// Pure formatting helpers for media metadata display.
// All value formatters return null when the value is absent, so callers can
// use `{#if}` to omit empty rows.

import { getLocale } from '../paraglide/runtime.js';
import * as m from '../paraglide/messages.js';

// Item count as a string (photos + videos), pluralized in the active locale.
export function itemCountLabel(n) {
  return n === 1 ? m.status_items_one({ count: n }) : m.status_items_other({ count: n });
}

/**
 * Format an ISO datetime string to a locale-aware human-readable form.
 * e.g. "2024-07-15T14:32:00" → "July 15, 2024 at 2:32 PM"
 * Returns the raw string unchanged if it is not a valid date, or null if absent.
 */
export function formatDate(raw) {
  if (!raw) return null;
  const d = new Date(raw);
  if (isNaN(d)) return raw;
  return d.toLocaleString(getLocale(), {
    year: 'numeric', month: 'long', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

/** Compact locale datetime (e.g. "12 Mar 2026, 14:32"); raw string if unparseable, null if absent. */
export function formatShortDate(raw) {
  if (!raw) return null;
  const d = new Date(raw);
  if (isNaN(d)) return raw;
  return d.toLocaleString(getLocale(), { dateStyle: 'medium', timeStyle: 'short' });
}

export function formatDimensions(m) {
  return m?.width && m?.height ? `${m.width} × ${m.height}` : null;
}

export function formatCamera(m) {
  const parts = [m?.make, m?.model].filter(Boolean);
  return parts.length ? parts.join(' ') : null;
}

// EXIF values arrive as rationals decoded to floats, so they carry noise
// (e.g. 1.7999999523 or 5.5399999). Round to `decimals` and drop any
// trailing zeros so "8.0" → "8" and "5.50" → "5.5".
export function roundTrim(v, decimals = 1) {
  return parseFloat(v.toFixed(decimals)).toString();
}

export function formatAperture(v) {
  return v != null ? `f/${roundTrim(v)}` : null;
}

// Exposure time in seconds → "1/250 s" for sub-second, "2 s" otherwise.
export function formatExposure(v) {
  if (v == null) return null;
  if (v >= 1) return `${roundTrim(v)} s`;
  return `1/${Math.round(1 / v)} s`;
}

export function formatFocal(m) {
  if (m?.focalLength == null) return null;
  let s = `${roundTrim(m.focalLength)} mm`;
  if (m.focalLength35mm != null) s += ` (${Math.round(m.focalLength35mm)} mm eq.)`;
  return s;
}

// Video duration in seconds → "mm:ss".
export function formatDuration(sec) {
  if (sec == null) return null;
  const total = Math.round(sec);
  const mm = Math.floor(total / 60);
  const ss = String(total % 60).padStart(2, '0');
  return `${mm}:${ss}`;
}

// Raw ffmpeg codec name → human-friendly label.
export function codecLabel(codec) {
  if (!codec) return null;
  const c = codec.toLowerCase();
  if (c === 'h264' || c === 'avc' || c === 'avc1') return 'H.264';
  if (c === 'hevc' || c === 'h265' || c === 'hvc1' || c === 'hev1') return 'H.265 / HEVC';
  return codec;
}

// True when the current browser can't decode the source codec, so a <video>
// would silently fail to play (the HEVC-in-a-non-Safari-browser case).
export function codecUnplayable(codec) {
  if (!codec) return false;
  const c = codec.toLowerCase();
  if (c === 'hevc' || c === 'h265' || c === 'hvc1' || c === 'hev1') {
    const v = document.createElement('video');
    return v.canPlayType('video/mp4; codecs="hvc1"') === ''
      && v.canPlayType('video/mp4; codecs="hev1"') === '';
  }
  return false;
}

// GPS arrives as [lat, lon]; render with a fixed precision and hemisphere.
export function formatGps(gps) {
  if (!Array.isArray(gps) || gps.length !== 2) return null;
  const [lat, lon] = gps;
  if (lat == null || lon == null) return null;
  const ns = lat >= 0 ? 'N' : 'S';
  const ew = lon >= 0 ? 'E' : 'W';
  return `${Math.abs(lat).toFixed(5)}° ${ns}, ${Math.abs(lon).toFixed(5)}° ${ew}`;
}

export function truncate(text, max) {
  if (!text) return null;
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

// Hierarchical tags are "|"-separated paths ("Places|Europe|France"), the
// darktable/Lightroom convention; a flat tag is a one-level path.

/** Levels of a tag path: "Places|Europe" → ["Places", "Europe"]; empty levels dropped. */
export function tagLevels(path) {
  if (!path) return [];
  return String(path).split('|').map((l) => l.trim()).filter(Boolean);
}

/** Last level of a tag path ("Places|Europe|Paris" → "Paris"), or null if empty. */
export function tagLeaf(path) {
  const levels = tagLevels(path);
  return levels.length ? levels[levels.length - 1] : null;
}

/**
 * Group tag paths sharing the same parent, in first-seen order, so siblings
 * render as one pill: "Places|France|Paris" + "Places|France|Lyon" →
 * { parent: ["Places", "France"], leaves: ["Paris", "Lyon"], paths: [...] }.
 * Flat (one-level) tags stay one group each.
 */
export function groupTagPaths(paths) {
  const groups = [];
  const byParent = new Map();
  for (const path of paths ?? []) {
    const levels = tagLevels(path);
    if (!levels.length) continue;
    const parent = levels.slice(0, -1);
    const leaf = levels[levels.length - 1];
    const key = parent.join('|');
    const existing = parent.length ? byParent.get(key) : undefined;
    if (existing) {
      if (!existing.leaves.includes(leaf)) {
        existing.leaves.push(leaf);
        existing.paths.push(levels.join('|'));
      }
      continue;
    }
    const group = { parent, leaves: [leaf], paths: [levels.join('|')] };
    groups.push(group);
    if (parent.length) byParent.set(key, group);
  }
  return groups;
}
