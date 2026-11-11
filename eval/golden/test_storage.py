"""storage.py (#344): naming by content hash, downloads that are checked before they're used, and errors that never
carry a URL, key or token. A fake opener stands in for Supabase, so no network or videos are needed."""

from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import storage

URL = "https://secretref.supabase.co"
KEY = "anon-key-value"


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class FakeSupabase:
    """Serves `objects` (key → bytes) to a signed-in user, and records every request."""

    def __init__(
        self, objects: dict[str, bytes] | None = None, fail: int | None = None
    ) -> None:
        self.objects = objects or {}
        self.fail = fail
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request) -> io.BytesIO:
        self.requests.append(request)
        if self.fail:
            raise urllib.error.HTTPError(request.full_url, self.fail, "nope", {}, None)  # type: ignore[arg-type]
        path = request.full_url.removeprefix(URL)
        if path.startswith("/auth/v1/token"):
            return io.BytesIO(json.dumps({"access_token": "user-jwt"}).encode())
        prefix = f"/storage/v1/object/authenticated/{storage.BUCKET}/"
        if path.startswith(prefix):
            key = path.removeprefix(prefix)
            if key not in self.objects:
                raise urllib.error.HTTPError(
                    request.full_url, 404, "not found", {}, None
                )  # type: ignore[arg-type]
            return io.BytesIO(self.objects[key])
        if path.startswith(f"/storage/v1/object/{storage.BUCKET}/"):
            self.objects[path.rsplit("/", 2)[-2] + "/" + path.rsplit("/", 1)[-1]] = (
                request.data
            )  # type: ignore[assignment]
            return io.BytesIO(b"{}")
        if path == "/auth/v1/admin/users":
            return io.BytesIO(b"{}")
        raise AssertionError(f"unexpected request {path}")


def test_objects_are_named_by_content_hash() -> None:
    h = sha(b"x")
    assert storage.object_key(h) == f"sha256/{h.removeprefix('sha256:')}.mp4"
    for bad in ("md5:abc", "sha256:../../etc", "sha256:" + "A" * 64):
        with pytest.raises(ValueError):
            storage.object_key(bad)


def test_fetch_downloads_checks_and_skips_what_is_cached(tmp_path: Path) -> None:
    v1, v2 = b"video one", b"video two"
    server = FakeSupabase(
        {storage.object_key(sha(v1)): v1, storage.object_key(sha(v2)): v2}
    )
    client = storage.Client(URL, KEY, server)
    client.sign_in("ci@example.test", "pw")
    videos = [
        {"id": "v01", "content_hash": sha(v1)},
        {"id": "v02", "content_hash": sha(v2)},
    ]
    (tmp_path / "v01.mp4").write_bytes(v1)  # already cached from an earlier run
    log: list[str] = []
    storage.fetch(client, videos, tmp_path, log.append)
    assert log == ["v01: cached", "v02: downloaded"]
    assert (tmp_path / "v02.mp4").read_bytes() == v2
    # Downloads go out with the CI user's token, not the anon key.
    assert server.requests[-1].get_header("Authorization") == "Bearer user-jwt"


def test_a_stale_cached_file_is_replaced(tmp_path: Path) -> None:
    v1 = b"video one"
    client = storage.Client(URL, KEY, FakeSupabase({storage.object_key(sha(v1)): v1}))
    (tmp_path / "v01.mp4").write_bytes(b"an older version")
    storage.fetch(
        client, [{"id": "v01", "content_hash": sha(v1)}], tmp_path, lambda _: None
    )
    assert (tmp_path / "v01.mp4").read_bytes() == v1


def test_a_download_that_doesnt_match_its_hash_is_never_kept(tmp_path: Path) -> None:
    wanted = sha(b"the labeled video")
    client = storage.Client(
        URL, KEY, FakeSupabase({storage.object_key(wanted): b"something else"})
    )
    with pytest.raises(storage.StorageError, match="v01: the file doesn't match"):
        storage.fetch(
            client, [{"id": "v01", "content_hash": wanted}], tmp_path, lambda _: None
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("status", [400, 403, 404])
def test_errors_name_the_video_and_status_but_no_url_or_secret(
    tmp_path: Path, status: int
) -> None:
    client = storage.Client(URL, KEY, FakeSupabase(fail=status))
    with pytest.raises(storage.StorageError) as error:
        client.sign_in("ci@example.test", "the-password")
    assert str(error.value) == f"sign in as the CI user: HTTP {status}"
    with pytest.raises(storage.StorageError) as error:
        storage.fetch(
            client, [{"id": "v01", "content_hash": sha(b"x")}], tmp_path, lambda _: None
        )
    message = str(error.value)
    assert message == f"download v01: HTTP {status}"
    for secret in ("secretref", "://", KEY, "the-password", "sha256"):
        assert secret not in message
    assert error.value.__cause__ is None and error.value.__suppress_context__


def test_a_non_https_url_is_refused() -> None:
    with pytest.raises(storage.StorageError, match="https"):
        storage.Client("http://example.com", KEY)


def test_upload_matches_files_by_hash_and_lists_whats_missing(tmp_path: Path) -> None:
    (tmp_path / "v05-h264.mp4").write_bytes(
        b"five"
    )  # named differently from its id, found by hash
    (tmp_path / "v01.mp4").write_bytes(b"one")
    server = FakeSupabase()
    log: list[str] = []
    videos = [
        {"id": "v01", "content_hash": sha(b"one")},
        {"id": "v05", "content_hash": sha(b"five")},
        {"id": "v11", "content_hash": sha(b"eleven")},
    ]
    missing = storage.upload(
        storage.Client(URL, "service-key", server), videos, tmp_path, log.append
    )
    assert missing == ["v11"]
    assert log == ["v01: uploaded", "v05: uploaded"]
    assert server.objects == {
        storage.object_key(sha(b"one")): b"one",
        storage.object_key(sha(b"five")): b"five",
    }


def test_only_videos_with_full_rights_are_stored() -> None:
    manifest = {
        "videos": [
            {"id": "v01", "labeled": True, "rights": "full"},
            {"id": "v02", "labeled": True, "rights": "full", "held_out": True},
            {"id": "v03", "labeled": False},
        ]
    }
    assert [v["id"] for v in storage.stored_videos(manifest)] == ["v01"]
    manifest["videos"].append({"id": "v04", "labeled": True, "rights": "internal"})
    with pytest.raises(SystemExit, match="v04"):
        storage.stored_videos(manifest)


def test_the_ci_user_gets_read_access_through_app_metadata() -> None:
    server = FakeSupabase()
    storage.Client(URL, "service-key", server).create_ci_user("ci@example.test", "pw")
    body = json.loads(server.requests[0].data)  # type: ignore[arg-type]
    assert (
        body["app_metadata"] == {"golden_eval": "read"}
        and body["email_confirm"] is True
    )
