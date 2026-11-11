"""The golden videos in Supabase Storage, for CI's golden eval (#344).

The videos are private and never committed (#52). They live in the `golden-videos` bucket on the **staging** project,
named by content hash (`sha256/<hex>.mp4`), so a file is the one its label was made from or it isn't used. Only the
videos CI scores are stored there: labeled, rights `full`, not held out. Who can read and write is set by the migration
`supabase/migrations/*_create_golden_videos_bucket.sql`.

On the owner's machine, with staging's service-role key (never put it in CI):

    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python eval/golden/storage.py create-ci-user --email EMAIL
        creates the read-only CI user and prints its password once: store it as the GOLDEN_CI_PASSWORD secret
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python eval/golden/storage.py upload
        uploads every video CI scores from eval/golden/videos/, matched by content hash; lists any it can't find

In CI, as the read-only user:

    SUPABASE_URL=... SUPABASE_ANON_KEY=... GOLDEN_CI_EMAIL=... GOLDEN_CI_PASSWORD=... \\
        python eval/golden/storage.py fetch DEST
        downloads each video CI scores to DEST/<id>.mp4, skipping files already there with the right hash

Nothing printed or raised here holds a URL, a key or a token: errors name the video id and the HTTP status only.
Standard library only, so CI can run it before installing anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import IO, Any

import ci_eval

ROOT = Path(__file__).resolve().parent
BUCKET = "golden-videos"
CHUNK = 1 << 20

Opener = Callable[[urllib.request.Request], IO[bytes]]


class StorageError(RuntimeError):
    """A failed storage call. The message never holds a URL, key or token."""


def object_key(content_hash: str) -> str:
    algo, _, digest = content_hash.partition(":")
    if (
        algo != "sha256"
        or len(digest) != 64
        or not all(c in "0123456789abcdef" for c in digest)
    ):
        raise ValueError(f"not a sha256 content hash: {content_hash!r}")
    return f"sha256/{digest}.mp4"


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def stored_videos(manifest: dict) -> list[dict]:
    """The videos kept in the bucket: the ones CI scores. Refuses one without full rights, since storing it in a cloud
    bucket is more than its rights allow."""
    videos = ci_eval.evaluated_videos(manifest)
    for v in videos:
        if v.get("rights") != "full":
            raise SystemExit(
                f"{v['id']} has rights {v.get('rights')!r}, not 'full': it can't be stored for CI"
            )
    return videos


def _default_open(request: urllib.request.Request) -> IO[bytes]:
    return urllib.request.urlopen(request, timeout=120)


class Client:
    """The few Supabase Auth and Storage calls this needs, over HTTPS."""

    def __init__(self, url: str, apikey: str, opener: Opener = _default_open) -> None:
        if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
            raise StorageError("SUPABASE_URL must be an https URL")
        self.url = url.rstrip("/")
        self.apikey = apikey
        self.token = (
            apikey  # the service-role key, or a user's access token after sign_in()
        )
        self.opener = opener

    def _call(
        self,
        what: str,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict | None = None,
    ) -> IO[bytes]:
        request = urllib.request.Request(
            self.url + path,
            data=body,
            method=method,
            headers={
                "apikey": self.apikey,
                "Authorization": f"Bearer {self.token}",
                **(headers or {}),
            },
        )
        try:
            return self.opener(request)
        except urllib.error.HTTPError as e:
            raise StorageError(f"{what}: HTTP {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            # URLError's reason can name the host; keep only its type.
            raise StorageError(f"{what}: {type(e).__name__}") from None

    def _json(self, what: str, method: str, path: str, payload: dict) -> Any:
        body = json.dumps(payload).encode()
        with self._call(
            what, method, path, body, {"Content-Type": "application/json"}
        ) as r:
            return json.loads(r.read() or b"null")

    def sign_in(self, email: str, password: str) -> None:
        data = self._json(
            "sign in as the CI user",
            "POST",
            "/auth/v1/token?grant_type=password",
            {"email": email, "password": password},
        )
        self.token = data["access_token"]

    def create_ci_user(self, email: str, password: str) -> None:
        # app_metadata is what the bucket's read policy checks; only the service role can set it.
        self._json(
            "create the CI user",
            "POST",
            "/auth/v1/admin/users",
            {
                "email": email,
                "password": password,
                "email_confirm": True,
                "app_metadata": {"golden_eval": "read"},
            },
        )

    def upload(self, vid: str, key: str, path: Path) -> None:
        # The whole file in one request: the bucket caps files at 50 MiB, and the videos are under 30 MB.
        with self._call(
            f"upload {vid}",
            "POST",
            f"/storage/v1/object/{BUCKET}/{key}",
            path.read_bytes(),
            {"Content-Type": "video/mp4", "x-upsert": "true"},
        ):
            pass

    def download(self, vid: str, key: str, dest: Path, content_hash: str) -> None:
        """Streams to a temp file next to `dest`, checks the hash, then moves it into place, so `dest` is never a
        partial or wrong file."""
        digest = hashlib.sha256()
        fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".part")
        try:
            # The file first, so its handle is closed even when the request fails.
            with (
                os.fdopen(fd, "wb") as out,
                self._call(
                    f"download {vid}",
                    "GET",
                    f"/storage/v1/object/authenticated/{BUCKET}/{key}",
                ) as src,
            ):
                while chunk := src.read(CHUNK):
                    digest.update(chunk)
                    out.write(chunk)
            if f"sha256:{digest.hexdigest()}" != content_hash:
                raise StorageError(
                    f"download {vid}: the file doesn't match its content hash"
                )
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


def fetch(
    client: Client,
    videos: Iterable[dict],
    dest: Path,
    log: Callable[[str], None] = print,
) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for v in videos:
        path = dest / f"{v['id']}.mp4"
        if path.exists() and file_hash(path) == v["content_hash"]:
            log(f"{v['id']}: cached")
            continue
        client.download(v["id"], object_key(v["content_hash"]), path, v["content_hash"])
        log(f"{v['id']}: downloaded")


def local_files(videos_dir: Path) -> dict[str, Path]:
    """Content hash → file, for every video in `videos_dir` (so `v05-h264.mp4` vs `v05.mp4` is settled by hash)."""
    return {file_hash(p): p for p in sorted(videos_dir.glob("*.mp4"))}


def upload(
    client: Client,
    videos: Iterable[dict],
    videos_dir: Path,
    log: Callable[[str], None] = print,
) -> list[str]:
    """Uploads each video found locally; returns the ids it couldn't find."""
    found = local_files(videos_dir)
    missing = []
    for v in videos:
        path = found.get(v["content_hash"])
        if path is None:
            missing.append(v["id"])
            continue
        client.upload(v["id"], object_key(v["content_hash"]), path)
        log(f"{v['id']}: uploaded")
    return missing


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} isn't set")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("upload", help="upload the videos CI scores (service role)")
    user = sub.add_parser(
        "create-ci-user", help="create the read-only CI user (service role)"
    )
    user.add_argument("--email", required=True)
    get = sub.add_parser("fetch", help="download the videos CI scores (CI user)")
    get.add_argument("dest", type=Path)
    args = parser.parse_args(argv)

    videos = stored_videos(json.loads((ROOT / "manifest.json").read_text()))
    try:
        if args.command == "fetch":
            client = Client(_env("SUPABASE_URL"), _env("SUPABASE_ANON_KEY"))
            client.sign_in(_env("GOLDEN_CI_EMAIL"), _env("GOLDEN_CI_PASSWORD"))
            fetch(client, videos, args.dest)
            return 0
        client = Client(_env("SUPABASE_URL"), _env("SUPABASE_SERVICE_ROLE_KEY"))
        if args.command == "create-ci-user":
            password = secrets.token_urlsafe(32)
            client.create_ci_user(args.email, password)
            print(
                f"Created {args.email}. Its password, shown once; store it as the GOLDEN_CI_PASSWORD secret:"
            )
            print(password)
            return 0
        missing = upload(client, videos, ROOT / "videos")
        if missing:
            print(
                f"Not on this machine, so not uploaded: {', '.join(missing)}. Upload them from a machine that has "
                "them; CI fails until every video it scores is stored.",
                file=sys.stderr,
            )
            return 1
        return 0
    except StorageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
