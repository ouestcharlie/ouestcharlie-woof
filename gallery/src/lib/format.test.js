import { describe, it, expect } from 'vitest';
import {
  roundTrim,
  formatDate,
  formatDimensions,
  formatCamera,
  formatAperture,
  formatExposure,
  formatFocal,
  formatDuration,
  codecLabel,
  codecUnplayable,
  formatGps,
  truncate,
} from './format.js';

describe('roundTrim', () => {
  it('drops trailing zeros (8.0 → 8)', () => {
    expect(roundTrim(8)).toBe('8');
  });
  it('rounds noisy EXIF floats to one decimal by default', () => {
    expect(roundTrim(5.539999961853027)).toBe('5.5');
    expect(roundTrim(1.7999999523162842)).toBe('1.8');
  });
});

describe('formatAperture', () => {
  it('formats with f/ prefix, trimmed', () => {
    expect(formatAperture(8)).toBe('f/8');
    expect(formatAperture(1.7999999523162842)).toBe('f/1.8');
  });
  it('returns null when absent', () => {
    expect(formatAperture(null)).toBeNull();
    expect(formatAperture(undefined)).toBeNull();
  });
});

describe('formatExposure', () => {
  it('formats sub-second exposures as reciprocals', () => {
    expect(formatExposure(0.004)).toBe('1/250 s');
  });
  it('formats one-second-or-more exposures in seconds', () => {
    expect(formatExposure(2)).toBe('2 s');
  });
  it('returns null when absent', () => {
    expect(formatExposure(null)).toBeNull();
  });
});

describe('formatFocal', () => {
  it('omits the 35mm equivalent when only focal length is present', () => {
    expect(formatFocal({ focalLength: 50 })).toBe('50 mm');
  });
  it('appends the 35mm equivalent when present', () => {
    expect(formatFocal({ focalLength: 5.539999961853027, focalLength35mm: 23 }))
      .toBe('5.5 mm (23 mm eq.)');
  });
  it('returns null when focal length is absent', () => {
    expect(formatFocal({})).toBeNull();
  });
});

describe('formatDimensions / formatCamera', () => {
  it('formats dimensions with a × separator', () => {
    expect(formatDimensions({ width: 4000, height: 3000 })).toBe('4000 × 3000');
  });
  it('returns null for partial dimensions', () => {
    expect(formatDimensions({ width: 4000 })).toBeNull();
  });
  it('joins make and model', () => {
    expect(formatCamera({ make: 'Canon', model: 'EOS R5' })).toBe('Canon EOS R5');
  });
  it('returns null when neither make nor model is present', () => {
    expect(formatCamera({})).toBeNull();
  });
});

describe('formatDuration', () => {
  it('formats seconds as mm:ss with zero-padding', () => {
    expect(formatDuration(95)).toBe('1:35');
    expect(formatDuration(5)).toBe('0:05');
  });
  it('returns null when absent', () => {
    expect(formatDuration(null)).toBeNull();
  });
});

describe('codecLabel', () => {
  it('maps H.264 aliases', () => {
    for (const c of ['h264', 'avc', 'avc1']) expect(codecLabel(c)).toBe('H.264');
  });
  it('maps HEVC aliases', () => {
    for (const c of ['hevc', 'h265', 'hvc1', 'hev1']) expect(codecLabel(c)).toBe('H.265 / HEVC');
  });
  it('passes unknown codecs through unchanged', () => {
    expect(codecLabel('vp9')).toBe('vp9');
  });
  it('returns null when absent', () => {
    expect(codecLabel(null)).toBeNull();
  });
});

describe('codecUnplayable', () => {
  it('is false for non-HEVC codecs', () => {
    expect(codecUnplayable('h264')).toBe(false);
    expect(codecUnplayable(null)).toBe(false);
  });
  it('reports HEVC unplayable when canPlayType returns empty for both hvc1/hev1', () => {
    const orig = HTMLMediaElement.prototype.canPlayType;
    HTMLMediaElement.prototype.canPlayType = () => '';
    try {
      expect(codecUnplayable('hevc')).toBe(true);
    } finally {
      HTMLMediaElement.prototype.canPlayType = orig;
    }
  });
  it('reports HEVC playable when the browser advertises support', () => {
    const orig = HTMLMediaElement.prototype.canPlayType;
    HTMLMediaElement.prototype.canPlayType = () => 'probably';
    try {
      expect(codecUnplayable('hvc1')).toBe(false);
    } finally {
      HTMLMediaElement.prototype.canPlayType = orig;
    }
  });
});

describe('formatGps', () => {
  it('renders northern/eastern hemispheres', () => {
    expect(formatGps([48.8566, 2.3522])).toBe('48.85660° N, 2.35220° E');
  });
  it('renders southern/western hemispheres from signed values', () => {
    expect(formatGps([-33.8688, -151.2093])).toBe('33.86880° S, 151.20930° W');
  });
  it('returns null for malformed input', () => {
    expect(formatGps(null)).toBeNull();
    expect(formatGps([1])).toBeNull();
    expect(formatGps([null, 2])).toBeNull();
  });
});

describe('truncate', () => {
  it('truncates with an ellipsis beyond max', () => {
    expect(truncate('x'.repeat(150), 100)).toBe('x'.repeat(100) + '…');
  });
  it('leaves short strings unchanged', () => {
    expect(truncate('short', 100)).toBe('short');
  });
  it('returns null for empty input', () => {
    expect(truncate('', 100)).toBeNull();
    expect(truncate(null, 100)).toBeNull();
  });
});

describe('formatDate', () => {
  it('returns null when absent', () => {
    expect(formatDate(null)).toBeNull();
  });
  it('passes through unparseable strings', () => {
    expect(formatDate('not a date')).toBe('not a date');
  });
  it('formats a valid ISO datetime to a localized string', () => {
    const out = formatDate('2024-07-15T14:32:00');
    expect(out).toMatch(/2024/);
    expect(out).toMatch(/15/);
  });
});
