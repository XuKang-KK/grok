"""Sandbox and tool safety tests."""

from __future__ import annotations

import json

import pytest

from app.tools import (
    fetch_url,
    list_dir,
    read_file,
    run_command,
    write_file,
)


@pytest.fixture
def tmp_ws(tmp_path, monkeypatch):
    monkeypatch.setattr("app.tools.WORKSPACE", tmp_path)
    return tmp_path


def _err(raw: str) -> dict:
    data = json.loads(raw)
    assert isinstance(data, dict)
    return data


def test_read_file_path_escape_blocked(tmp_ws):
    (tmp_ws / "ok.txt").write_text("hello", encoding="utf-8")
    for path in ("../README.md", "../../etc/passwd", "/etc/passwd", "../"):
        data = _err(read_file(path))
        assert "error" in data
        assert "越界" in data["error"] or "不能为空" not in data["error"]


def test_write_file_path_escape_blocked(tmp_ws):
    for path in ("../evil.txt", "/tmp/evil.txt", "../../evil.txt"):
        data = _err(write_file(path, "nope"))
        assert "error" in data
        assert tmp_ws.joinpath("evil.txt").exists() is False


def test_list_dir_path_escape_blocked(tmp_ws):
    data = _err(list_dir(".."))
    assert "error" in data
    data = _err(list_dir("/etc"))
    assert "error" in data


def test_write_read_roundtrip(tmp_ws):
    path = "notes/roundtrip.txt"
    content = "你好，roundtrip\n第二行"
    written = _err(write_file(path, content))
    assert "error" not in written
    assert written.get("ok") is True
    assert written.get("path") == path
    listed = _err(list_dir("notes"))
    assert "error" not in listed
    names = [e["name"] for e in listed["entries"]]
    assert "roundtrip.txt" in names
    read = _err(read_file(path))
    assert "error" not in read
    assert read["content"] == content


def test_write_file_rejects_huge_payload(tmp_ws):
    huge = "x" * (200_000 + 10)
    data = _err(write_file("huge.txt", huge))
    assert "error" in data
    assert not (tmp_ws / "huge.txt").exists()


def test_dangerous_command_blocked(tmp_ws):
    for cmd in ("sudo rm -rf /", "sudo ls", "mkfs.ext4 /dev/sda"):
        data = _err(run_command(cmd))
        assert data.get("blocked") is True or "error" in data
        assert "拦截" in data.get("error", "")


def test_fetch_url_rejects_localhost():
    for url in (
        "http://localhost/",
        "http://localhost:8000/secret",
        "https://127.0.0.1/",
        "http://127.0.0.1:8000/",
        "http://[::1]/",
        "http://192.168.1.1/",
        "http://10.0.0.1/",
        "file:///etc/passwd",
        "ftp://example.com/",
    ):
        data = _err(fetch_url(url))
        assert "error" in data, url
