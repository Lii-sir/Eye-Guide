from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


def bundled_path(*parts: str) -> Path:
    return app_root().joinpath(*parts)


def first_existing_path(*candidates: str | Path) -> Path | None:
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def runtime_roots() -> list[Path]:
    root = app_root()
    roots = [root]
    internal_dir = root / "_internal"
    if internal_dir.exists():
        roots.append(internal_dir)
    return roots


def runtime_dll_dirs() -> list[Path]:
    dll_dirs: list[Path] = []
    for root in runtime_roots():
        dll_dirs.extend(
            [
                root,
                root / "torch" / "lib",
                root / "cv2",
            ]
        )
    unique_dirs: list[Path] = []
    seen: set[str] = set()
    for directory in dll_dirs:
        key = str(directory).lower()
        if key in seen:
            continue
        seen.add(key)
        if directory.exists():
            unique_dirs.append(directory)
    return unique_dirs


def configure_runtime_environment() -> None:
    import os

    dll_dirs = runtime_dll_dirs()
    for dll_dir in dll_dirs:
        try:
            os.add_dll_directory(str(dll_dir))
        except (AttributeError, FileNotFoundError, OSError):
            pass

    if dll_dirs:
        existing_path = os.environ.get("PATH", "")
        prepend_dirs = [str(path) for path in dll_dirs]
        os.environ["PATH"] = os.pathsep.join(prepend_dirs + [existing_path])
