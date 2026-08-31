import hashlib
import json

import pytest

from tools import publish_release


def test_publish_requires_confirmation_and_uploads_manifest_last(tmp_path):
    archive = tmp_path / "release.zip"
    archive.write_bytes(b"zip")
    manifest_path = tmp_path / "latest.json"
    manifest_path.write_text(json.dumps({
        "version": "2026.08.25.3",
        "url": "https://downloads.example/releases/release.zip",
        "sha256": hashlib.sha256(b"zip").hexdigest(),
    }), encoding="utf-8")
    calls = []

    def runner(command, **kwargs):
        calls.append(command)

    with pytest.raises(ValueError):
        publish_release.publish_release(manifest_path, "bucket", "downloads.example", "NO", runner)
    publish_release.publish_release(manifest_path, "bucket", "downloads.example", "PUBLISH", runner)
    assert "release.zip" in " ".join(calls[0])
    assert "latest.json" in " ".join(calls[1])
