// Locale resolution for the gallery.
//
// The active locale is driven programmatically from the MCP host context
// (App.svelte reads HostContext.locale) rather than from a cookie or URL —
// the gallery runs inside an embedded iframe. Paraglide is compiled with the
// `globalVariable` strategy (see vite.config.js), so setLocale(tag, { reload:
// false }) switches the in-memory locale without navigating.

import { baseLocale, locales, setLocale } from '../paraglide/runtime.js';

/**
 * Resolve an arbitrary BCP-47 tag (e.g. "fr-FR", "zh-Hans-CN") to one of the
 * compiled locales: exact match → primary subtag → baseLocale.
 * @param {string | null | undefined} tag
 * @returns {string} a member of `locales`
 */
export function resolveLocale(tag) {
  if (!tag) return baseLocale;
  const lower = String(tag).toLowerCase();
  // Exact match (case-insensitive), e.g. "en-GB".
  const exact = locales.find((l) => l.toLowerCase() === lower);
  if (exact) return exact;
  // Primary subtag, e.g. "fr-FR" → "fr", "zh-Hans-CN" → "zh".
  const primary = lower.split('-')[0];
  const byPrimary = locales.find((l) => l.toLowerCase() === primary);
  if (byPrimary) return byPrimary;
  return baseLocale;
}

/**
 * Resolve `tag` and activate it in-memory (no reload), then reflect it on
 * <html lang> for accessibility. Safe to call with any host/browser tag.
 * @param {string | null | undefined} tag
 * @returns {string} the resolved locale that was activated
 */
export function applyLocale(tag) {
  const resolved = resolveLocale(tag);
  setLocale(resolved, { reload: false });
  if (typeof document !== 'undefined') {
    document.documentElement.lang = resolved;
  }
  return resolved;
}
