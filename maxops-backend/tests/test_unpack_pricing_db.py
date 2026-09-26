from __future__ import annotations

from pathlib import Path

from staging_pricing.release_utils import compress_xz, sha256_file, write_sha256_file
from staging_pricing.unpack_pricing_db import main as unpack_main


def test_unpack_pricing_db_installs_local_db(tmp_path: Path, monkeypatch) -> None:
    artifact_dir = tmp_path / "pricing_artifacts"
    artifact_dir.mkdir()
    source_db = tmp_path / "source.db"
    source_db.write_bytes(b"sqlite-test-bytes")

    asset = artifact_dir / "maxops_pricing.db.xz"
    sha = artifact_dir / "maxops_pricing.db.xz.sha256"
    compress_xz(source_db, asset, preset=1)
    write_sha256_file(sha, sha256_file(asset), asset.name)

    target_db = tmp_path / "maxops_pricing.db"
    monkeypatch.setattr(
        "sys.argv",
        [
            "unpack_pricing_db.py",
            "--artifact-dir",
            str(artifact_dir),
            "--db-path",
            str(target_db),
        ],
    )

    unpack_main()

    assert target_db.read_bytes() == source_db.read_bytes()
