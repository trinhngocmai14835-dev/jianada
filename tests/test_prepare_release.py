import hashlib
import json
import zipfile

import pytest

from tools import prepare_release


def test_prepare_release_creates_zip_and_manifest(tmp_path):
    exe = tmp_path / "input.exe"
    exe.write_bytes(b"test-exe")
    zip_path, manifest_path = prepare_release.prepare_release(exe, tmp_path / "out", "notes")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == prepare_release.APP_VERSION
    assert manifest["url"].endswith("/" + zip_path.name)
    assert manifest["sha256"] == hashlib.sha256(zip_path.read_bytes()).hexdigest()
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.namelist() == [prepare_release.APP_EXE_NAME]


def test_validate_version_rejects_unsafe_value():
    with pytest.raises(ValueError):
        prepare_release.validate_version("../bad")
