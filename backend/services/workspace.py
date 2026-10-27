"""Versioned generated projects with confined paths and bounded writes."""

import os
import re
from pathlib import Path, PurePosixPath

ROOT = Path(os.environ.get("GENERATED_PROJECTS_DIR", "generated_projects")).resolve()
MAX_FILES = 100
MAX_BYTES = 2_000_000


def project_path(run_id: str, version: int) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", run_id) or version < 1:
        raise ValueError("Invalid project identifier or version")
    path = ROOT / run_id / f"v{version}"
    if any(parent.is_symlink() for parent in [ROOT, path.parent, path]):
        raise ValueError("Project paths cannot contain symlinks")
    return path


def validate_files(files: list[dict]) -> None:
    if not files or len(files) > MAX_FILES:
        raise ValueError("A project must contain 1–100 files")
    total = 0
    seen = set()
    for file in files:
        name = file.get("path", "")
        content = file.get("content")
        path = PurePosixPath(name)
        if (
            not isinstance(content, str)
            or not name
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or ":" in name
            or "\x00" in name
            or any(part.startswith(".") for part in path.parts)
            or path.as_posix() != name
            or name in seen
        ):
            raise ValueError(f"Unsafe or duplicate generated file path: {name!r}")
        seen.add(name)
        total += len(content.encode())
    if total > MAX_BYTES:
        raise ValueError("Generated project exceeds 2 MB limit")


def write_project(run_id: str, version: int, files: list[dict]) -> Path:
    """Write one immutable version after validating every input path."""
    validate_files(files)
    path = project_path(run_id, version)
    if path.exists():
        existing = {file["path"]: file["content"] for file in read_project(run_id, version)}
        proposed = {file["path"]: file["content"] for file in files}
        if existing != proposed:
            raise FileExistsError("An existing artifact version cannot be modified")
        return path
    path.mkdir(parents=True, exist_ok=False)
    for file in files:
        target = path / file["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file["content"], encoding="utf-8")
    return path


def read_project(run_id: str, version: int) -> list[dict]:
    """Read a project without following symlink files or directories."""
    path = project_path(run_id, version)
    files = []
    for target in sorted(path.rglob("*")):
        if target.is_symlink():
            raise ValueError("Generated projects cannot contain symlinks")
        if target.is_file():
            files.append(
                {"path": target.relative_to(path).as_posix(), "content": target.read_text()}
            )
    validate_files(files)
    return files
