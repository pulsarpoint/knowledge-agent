"""Move legacy RAG sources to the mount root while API and worker are stopped."""

import argparse
from pathlib import Path
from uuid import UUID


def flatten(root: Path, project: UUID) -> list[str]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("The source root must be an existing directory, not a symbolic link")
    legacy = root / str(project)
    if legacy.is_symlink():
        raise ValueError("The legacy source directory must not be a symbolic link")
    if not legacy.exists():
        return []
    if not legacy.is_dir():
        raise ValueError("The legacy source path is not a directory")
    entries = sorted(legacy.iterdir())
    # Check the whole move before changing anything; never overwrite a collection.
    for entry in entries:
        target = root / entry.name
        if target.exists() or target.is_symlink():
            raise ValueError(f"A collection already exists at the destination: {entry.name}")
    for entry in entries:
        entry.rename(root / entry.name)
    legacy.rmdir()
    return [entry.name for entry in entries]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--project-id", required=True, type=UUID)
    args = parser.parse_args()
    moved = flatten(args.root, args.project_id)
    print("Moved collections: " + ", ".join(moved) if moved else "Source folders already use the mount root.")
