"""Media file extensions the skill's scripts handle (lowercase, with the dot).

Same list as the toolkit's PHOTO_SUFFIXES | VIDEO_SUFFIXES: the photos and
videos Woof indexes. The scripts run standalone (no toolkit installed), so this
copy is checked against the toolkit by a test, and both skills keep the same file.
"""

MEDIA_EXT = (
    # Standard formats
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
    ".gif",
    ".bmp",
    ".jxl",
    # HEIF container (HEVC or AV1)
    ".heic",
    ".heif",
    ".hif",
    ".avif",
    # RAW
    ".cr2",
    ".cr3",
    ".crw",
    ".nef",
    ".nrw",
    ".arw",
    ".srf",
    ".sr2",
    ".dng",
    ".raf",
    ".orf",
    ".rw2",
    ".rwl",
    ".pef",
    ".srw",
    ".erf",
    ".3fr",
    ".fff",
    ".iiq",
    ".mos",
    # Videos
    ".mp4",
    ".mov",
)
