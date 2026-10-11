from __future__ import annotations

from pathlib import PurePath, PurePosixPath


CATEGORY_EXTENSIONS: dict[str, frozenset[str]] = {
    "images": frozenset({
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif",
        ".bmp", ".tif", ".tiff", ".dng", ".raw", ".svg",
    }),
    "videos": frozenset({
        ".mp4", ".mkv", ".mov", ".avi", ".webm", ".3gp", ".m4v",
        ".mpeg", ".mpg", ".ts", ".mts", ".m2ts",
    }),
    "audio": frozenset({
        ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
        ".amr", ".wma",
    }),
    "documents": frozenset({
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".txt", ".csv", ".rtf", ".odt", ".ods", ".odp", ".epub",
    }),
    "archives_installers": frozenset({
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",
        ".apk", ".exe", ".msi", ".iso",
    }),
}

CATEGORY_LABELS = {
    "images": "Images",
    "videos": "Videos",
    "audio": "Audio / MP3",
    "documents": "Documents",
    "archives_installers": "Archives / installers",
    "other": "Other files",
}

CATEGORY_DESTINATIONS = {
    "images": "Photos",
    "videos": "Videos",
    "audio": "Recordings",
    "documents": "docs",
    "archives_installers": "Archives",
    "other": "Other",
}

MAJOR_FOLDER_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("WhatsApp", ("whatsapp", "com.whatsapp")),
    ("Telegram", ("telegram", "org.telegram")),
    ("Camera / DCIM", ("dcim", "camera")),
    ("Pictures / screenshots", ("pictures", "screenshot", "screenshots")),
    ("Movies / video", ("movies", "video", "videos")),
    ("Downloads", ("download", "downloads")),
    ("Recordings", ("recording", "recordings", "screen recordings")),
    ("Music / audio", ("music", "audio", "podcasts")),
    ("Documents", ("document", "documents")),
    ("Transfer apps", ("shareit", "sharekarolite", "mobiletrans", "bluetooth")),
)


def normalize_extension(name: str, extension_hint: str = "") -> str:
    hint = extension_hint.strip().lower()
    if hint and not hint.startswith("."):
        hint = f".{hint}"
    suffix = PurePath(name).suffix.lower()
    return hint or suffix


def classify_extension(extension: str) -> str:
    extension = extension.lower()
    for category, extensions in CATEGORY_EXTENSIONS.items():
        if extension in extensions:
            return category
    return "other"


def classify_name(name: str, extension_hint: str = "") -> tuple[str, str]:
    extension = normalize_extension(name, extension_hint)
    return extension, classify_extension(extension)


def major_folder(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/").lower()
    parts = PurePosixPath(relative_path.replace("\\", "/")).parts
    for label, tokens in MAJOR_FOLDER_RULES:
        if any(token in normalized for token in tokens):
            return label
    return parts[0] if len(parts) > 1 else "Root files"
