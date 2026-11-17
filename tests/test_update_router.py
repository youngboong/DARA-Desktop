import asyncio
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import HTTPException

from dara.server import presence, update_router as up

SHA = "ab" * 32


def release(version="1.4.0", **asset):
    name = f"DARA-Setup-{version}.exe"
    return {"tag_name": f"v{version}", "body": "<b>Notes</b>", "published_at": "2026-11-17T00:00:00Z",
            "assets": [{"name": name, "size": 10, "browser_download_url": f"https://github.com/x/{name}", **asset}]}


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(up, "_check", {"at": None, "result": None})
    monkeypatch.setattr(up, "_job", {"state": "idle", "progress": None, "error": None})
    monkeypatch.setattr(up, "current_version", lambda: "1.3.1")
    monkeypatch.delenv(up.FEED_ENV, raising=False)
    monkeypatch.setenv(up.DIR_ENV, str(tmp_path / "updates"))


def test_version_comparison():
    assert up.parse_version("v1.3.10") == (1, 3, 10)
    assert up.parse_version("1.3.1-rc1") is None
    assert up.is_newer("1.3.10", "1.3.9")
    assert up.is_newer("v1.4", "1.3.9")
    assert not up.is_newer("1.3.1", "1.3.1")
    assert not up.is_newer("1.3.1.0", "1.3.1")
    assert not up.is_newer("1.3.0", "1.3.1")
    assert not up.is_newer("garbage", "1.3.1")


def test_digest_field_makes_the_update_available():
    result = up.parse_release(release(digest=f"sha256:{SHA.upper()}"), "1.3.1")
    assert result["available"] and result["sha256"] == SHA and result["latest"] == "1.4.0"
    assert result["notes"] == "<b>Notes</b>"  # plain text; the UI never renders it as HTML


def test_sha256_sibling_asset_is_the_fallback():
    data = release()
    data["assets"].append({"name": "DARA-Setup-1.4.0.exe.sha256", "browser_download_url": "https://github.com/x/s"})
    result = up.parse_release(data, "1.3.1", fetch_text=lambda url: f"{SHA}  DARA-Setup-1.4.0.exe\n")
    assert result["available"] and result["sha256"] == SHA


def test_missing_hash_is_not_available():
    result = up.parse_release(release(), "1.3.1")
    assert not result["available"] and result["error"] == up.UNVERIFIABLE
    data = release()
    data["assets"].append({"name": "DARA-Setup-1.4.0.exe.sha256", "browser_download_url": "https://github.com/x/s"})
    assert up.parse_release(data, "1.3.1", fetch_text=lambda url: "not a hash")["error"] == up.UNVERIFIABLE


def test_same_or_older_release_is_not_available():
    result = up.parse_release(release("1.3.1", digest=f"sha256:{SHA}"), "1.3.1")
    assert not result["available"] and result["error"] is None


def test_network_failure_is_silent(monkeypatch):
    def offline():
        raise OSError("no network")

    monkeypatch.setattr(up, "_read_feed", offline)
    result = up.status()
    assert result["available"] is False and result["state"] == "idle" and "internet" in result["error"]


def test_check_is_cached_until_refresh(monkeypatch, tmp_path):
    feed = tmp_path / "feed.json"
    feed.write_text(json.dumps(release(digest=f"sha256:{SHA}")), encoding="utf-8")
    monkeypatch.setenv(up.FEED_ENV, str(feed))
    assert up.status()["latest"] == "1.4.0"
    feed.write_text(json.dumps(release("1.5.0", digest=f"sha256:{SHA}")), encoding="utf-8")
    assert up.status()["latest"] == "1.4.0"
    assert up.status(refresh=True)["latest"] == "1.5.0"


def test_download_host_allow_list(monkeypatch):
    assert up.check_download_url("https://github.com/a/b.exe")
    assert up.check_download_url("https://release-assets.githubusercontent.com/x")
    for url in ("http://github.com/a.exe", "https://example.com/a.exe", "https://github.com.evil.io/a.exe", None):
        with pytest.raises(ValueError):
            up.check_download_url(url)
    with pytest.raises(ValueError):  # redirects are checked too
        up._CheckedRedirects().redirect_request(None, None, 302, "Found", {}, "https://example.com/a.exe")
    monkeypatch.setenv(up.FEED_ENV, "feed.json")
    assert up.check_download_url("http://127.0.0.1:9/a.exe")


@pytest.fixture
def serve():
    payload = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload["body"])))
            self.end_headers()
            self.wfile.write(payload["body"])

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield payload, f"http://127.0.0.1:{server.server_address[1]}/DARA-Setup-1.4.0.exe"
    server.shutdown()


def test_hash_mismatch_deletes_the_download(monkeypatch, tmp_path, serve):
    payload, url = serve
    payload["body"] = b"0123456789"
    monkeypatch.setenv(up.FEED_ENV, "feed.json")
    started = []
    monkeypatch.setattr(up, "_start_installer", lambda path, sha: started.append(path))
    up._install({"latest": "1.4.0", "url": url, "size": 10, "sha256": SHA})
    assert up._job["state"] == "error" and "damaged" in up._job["error"]
    assert not started and not list((tmp_path / "updates").iterdir())

    up._install({"latest": "1.4.0", "url": url, "size": 10, "sha256": hashlib.sha256(b"0123456789").hexdigest()})
    assert up._job["state"] == "installing" and started == [tmp_path / "updates" / "DARA-Setup-1.4.0.exe"]
    assert started[0].read_bytes() == b"0123456789"


def test_install_refused_when_not_packaged(monkeypatch):
    monkeypatch.setattr(up, "is_packaged", lambda: False)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(up.install_update())
    assert refused.value.status_code == 409 and "installer" in refused.value.detail


def test_install_refused_while_analyses_run_unless_forced(monkeypatch):
    monkeypatch.setattr(up, "is_packaged", lambda: True)
    monkeypatch.setattr(presence, "_jobs_active", lambda: True)
    monkeypatch.setattr(up, "check", lambda refresh=False: {**up._empty(), "available": True, "latest": "1.4.0", "size": 10})
    started = []
    monkeypatch.setattr(up, "_install", lambda release: started.append(release))
    with pytest.raises(HTTPException) as refused:
        asyncio.run(up.install_update())
    assert refused.value.status_code == 409 and refused.value.detail["force"]
    assert "Analyses are running" in refused.value.detail["message"]
    asyncio.run(up.install_update(force=True))
    for _ in range(100):
        if started:
            break
        threading.Event().wait(0.01)
    assert started and started[0]["latest"] == "1.4.0"


def test_checkout_is_not_packaged(monkeypatch):
    monkeypatch.setenv(up.WINDOW_ENV, "1234")
    assert up.is_packaged() is False  # this checkout has pyproject.toml next to src
