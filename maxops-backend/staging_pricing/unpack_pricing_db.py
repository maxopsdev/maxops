"""Unpack the repo-bundled pricing database artifact into maxops_pricing.db."""
from __future__ import annotations

import argparse
from pathlib import Path

try:
    from staging_pricing.release_utils import (
        DEFAULT_ASSET_NAME,
        DEFAULT_SHA256_NAME,
        atomic_replace,
        decompress_xz,
        parse_sha256_text,
        sha256_file,
        temporary_file_path,
    )
except ModuleNotFoundError:
    from release_utils import (
        DEFAULT_ASSET_NAME,
        DEFAULT_SHA256_NAME,
        atomic_replace,
        decompress_xz,
        parse_sha256_text,
        sha256_file,
        temporary_file_path,
    )


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_db_path() -> Path:
    return _backend_root() / "maxops_pricing.db"


def _default_artifact_dir() -> Path:
    return _backend_root() / "pricing_artifacts"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unpack the bundled maxops_pricing.db.xz artifact.")
    parser.add_argument(
        "--artifact-dir",
        default=str(_default_artifact_dir()),
        help="Directory containing the bundled compressed DB artifact and sha256 file.",
    )
    parser.add_argument(
        "--asset-name",
        default=DEFAULT_ASSET_NAME,
        help="Compressed artifact file name.",
    )
    parser.add_argument(
        "--sha256-name",
        default=DEFAULT_SHA256_NAME,
        help="Checksum file name.",
    )
    parser.add_argument(
        "--db-path",
        default=str(_default_db_path()),
        help="Destination path for the unpacked SQLite database.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing maxops_pricing.db file.",
    )
    return parser.parse_args()


def unpack_bundled_pricing_db(
    artifact_dir: Path,
    asset_name: str = DEFAULT_ASSET_NAME,
    sha256_name: str = DEFAULT_SHA256_NAME,
    db_path: Path | None = None,
    force: bool = False,
) -> dict[str, object]:
    artifact_dir = artifact_dir.resolve()
    asset_path = artifact_dir / asset_name
    sha256_path = artifact_dir / sha256_name
    db_path = (db_path or _default_db_path()).resolve()

    if db_path.exists() and not force:
        raise RuntimeError(f"Pricing DB already exists: {db_path}. Use force=True to replace it.")
    if not asset_path.exists():
        raise RuntimeError(f"Compressed pricing artifact not found: {asset_path}")
    if not sha256_path.exists():
        raise RuntimeError(f"Pricing checksum file not found: {sha256_path}")

    expected_sha = parse_sha256_text(sha256_path.read_text())
    actual_sha = sha256_file(asset_path)
    if actual_sha.lower() != expected_sha.lower():
        raise RuntimeError(
            f"SHA256 mismatch for bundled pricing artifact: expected {expected_sha}, got {actual_sha}"
        )

    temp_db = temporary_file_path(db_path.parent, ".db")
    try:
        decompress_xz(asset_path, temp_db)
        atomic_replace(temp_db, db_path)
    finally:
        if temp_db.exists():
            temp_db.unlink()

    return {
        "artifact_path": str(asset_path),
        "sha256": actual_sha,
        "db_path": str(db_path),
        "size_bytes": db_path.stat().st_size,
    }


def main() -> None:
    args = parse_args()
    result = unpack_bundled_pricing_db(
        artifact_dir=Path(args.artifact_dir),
        asset_name=args.asset_name,
        sha256_name=args.sha256_name,
        db_path=Path(args.db_path),
        force=args.force,
    )
    db_path = Path(result["db_path"])
    asset_path = result["artifact_path"]
    actual_sha = result["sha256"]

    print("Unpacked pricing DB:")
    print(f"  artifact:   {asset_path}")
    print(f"  sha256:     {actual_sha}")
    print(f"  db_path:    {db_path}")
    print(f"  size_bytes: {db_path.stat().st_size}")


if __name__ == "__main__":
    main()
