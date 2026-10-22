"""Where ytconv keeps files it makes for itself (the OCR helper, speech models).

    run from the project folder   <project>/.cache and <project>/models, as always
    installed with pip / uv       a per-user cache folder, because the install
                                  folder (site-packages) is not for writing:
                                    macOS    ~/Library/Caches/ytconv
                                    Windows  %LOCALAPPDATA%\\ytconv
                                    Linux    $XDG_CACHE_HOME/ytconv or ~/.cache/ytconv

Set YTCONV_CACHE_DIR to put them somewhere else.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def from_project_folder():
    """True when running from a checkout of the project, not from an install."""
    return (ROOT / "pyproject.toml").is_file() and (ROOT / "ytconv").is_dir()


def user_cache_dir():
    override = os.environ.get("YTCONV_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ytconv"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "ytconv"
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "ytconv"


def cache_dir():
    """Folder for the compiled OCR helper."""
    if os.environ.get("YTCONV_CACHE_DIR") or not from_project_folder():
        return user_cache_dir()
    return ROOT / ".cache"


def models_dir():
    """Folder for the speech-to-text models (~466 MB for the small one)."""
    if os.environ.get("YTCONV_CACHE_DIR") or not from_project_folder():
        return user_cache_dir() / "models"
    return ROOT / "models"
