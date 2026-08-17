"""Minimal MCP stdio JSON-RPC client (list_tools + call_tool)."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MCP_FILE = DATA_DIR / "mcp.json"
EXAMPLE_FILE = PROJECT_ROOT / "mcp.example.json"

PROTOCOL_VERSION = "2024-11-05"
RPC_TIMEOUT = 25.0


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    note: str = ""


def _sanitize_name(name: str) -> str:
    out = []
    for ch in (name or "").strip():
        if ch.isalnum() or ch in ("_", "-"):
            out.append(ch if ch != "-" else "_")
    return "".join(out) or "server"


def load_mcp_config(path: Path | None = None) -> list[MCPServerConfig]:
    """Parse an MCP config file. Supports {servers:[...]} or {mcpServers:{...}}."""
    target = path or MCP_FILE
    if not target.exists():
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法解析 MCP 配置 {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("MCP 配置必须是 JSON 对象")
    servers: list[MCPServerConfig] = []
    if isinstance(raw.get("servers"), list):
        for row in raw["servers"]:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            command = str(row.get("command") or "").strip()
            if not name or not command:
                continue
            args = row.get("args") or []
            if not isinstance(args, list):
                args = []
            env = row.get("env") or {}
            if not isinstance(env, dict):
                env = {}
            servers.append(
                MCPServerConfig(
                    name=_sanitize_name(name),
                    command=command,
                    args=[str(a) for a in args],
                    env={str(k): str(v) for k, v in env.items()},
                    enabled=bool(row.get("enabled", True)),
                    note=str(row.get("note") or ""),
                )
            )
    mcp_servers = raw.get("mcpServers")
    if isinstance(mcp_servers, dict):
        for name, row in mcp_servers.items():
            if not isinstance(row, dict):
                continue
            command = str(row.get("command") or "").strip()
            if not command:
                continue
            args = row.get("args") or []
            if not isinstance(args, list):
                args = []
            env = row.get("env") or {}
            if not isinstance(env, dict):
                env = {}
            servers.append(
                MCPServerConfig(
                    name=_sanitize_name(str(name)),
                    command=command,
                    args=[str(a) for a in args],
                    env={str(k): str(v) for k, v in env.items()},
                    enabled=bool(row.get("enabled", True)),
                    note=str(row.get("note") or ""),
                )
            )
    return servers


class _StdioSession:
    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self.proc = proc
        self._id = 0
        self._pending: dict[int, tuple[threading.Event, dict[str, Any] | None]] = {}
        self._lock = threading.Lock()
        self._alive = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._alive = False
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except Exception:
            pass

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = RPC_TIMEOUT) -> Any:
        with self._lock:
            self._id += 1
            msg_id = self._id
            ev = threading.Event()
            self._pending[msg_id] = (ev, None)
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        self._write(payload)
        if not ev.wait(timeout):
            with self._lock:
                self._pending.pop(msg_id, None)
            raise TimeoutError(f"MCP 调用超时: {method}")
        with self._lock:
            _, result = self._pending.pop(msg_id, (None, None))
        if isinstance(result, dict) and "error" in result:
            err = result["error"]
            raise RuntimeError(str(err))
        if isinstance(result, dict) and "result" in result:
            return result["result"]
        return result

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)

    def _write(self, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii")
        assert self.proc.stdin is not None
        self.proc.stdin.write(header + raw)
        self.proc.stdin.flush()

    def _read_loop(self) -> None:
        stdout = self.proc.stdout
        if stdout is None:
            return
        buf = b""
        while self._alive and self.proc.poll() is None:
            try:
                chunk = stdout.read(1)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            msg, buf = _try_parse_frame(buf)
            if msg is None:
                continue
            self._dispatch(msg)
        # drain leftover
        if buf:
            msg, _ = _try_parse_frame(buf + b"\n")
            if msg:
                self._dispatch(msg)

    def _dispatch(self, msg: dict[str, Any]) -> None:
        mid = msg.get("id")
        if mid is None:
            return
        try:
            mid_i = int(mid)
        except (TypeError, ValueError):
            return
        with self._lock:
            item = self._pending.get(mid_i)
            if item is None:
                return
            ev, _ = item
            self._pending[mid_i] = (ev, msg)
            ev.set()


def _try_parse_frame(buf: bytes) -> tuple[dict[str, Any] | None, bytes]:
    """Parse LSP-style Content-Length frames or a single NDJSON line."""
    if b"\r\n\r\n" in buf or b"\n\n" in buf:
        sep = b"\r\n\r\n" if b"\r\n\r\n" in buf else b"\n\n"
        header, rest = buf.split(sep, 1)
        length = None
        for line in header.replace(b"\r\n", b"\n").split(b"\n"):
            if line.lower().startswith(b"content-length:"):
                try:
                    length = int(line.split(b":", 1)[1].strip())
                except ValueError:
                    length = None
        if length is None:
            return None, rest
        if len(rest) < length:
            return None, buf
        body, leftover = rest[:length], rest[length:]
        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return None, leftover
        if isinstance(data, dict):
            return data, leftover
        return None, leftover
    # NDJSON fallback (complete line)
    if b"\n" in buf and not buf.lower().startswith(b"content-length"):
        line, leftover = buf.split(b"\n", 1)
        line = line.strip()
        if not line:
            return None, leftover
        try:
            data = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            return None, leftover
        if isinstance(data, dict) and ("jsonrpc" in data or "method" in data or "id" in data):
            return data, leftover
    return None, buf


@dataclass
class MCPRuntime:
    config: MCPServerConfig
    tools: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    session: _StdioSession | None = None

    def tool_prefix(self) -> str:
        return f"mcp_{self.config.name}_"


class MCPManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runtimes: dict[str, MCPRuntime] = {}
        self._mtime: float | None = None
        self._config_error: str = ""

    def reload_if_needed(self) -> None:
        path = MCP_FILE
        mtime = path.stat().st_mtime if path.exists() else None
        with self._lock:
            if mtime == self._mtime and self._runtimes:
                return
            self._mtime = mtime
            self._config_error = ""
            self._shutdown_unlocked()
            if not path.exists():
                return
            try:
                configs = load_mcp_config(path)
            except ValueError as exc:
                self._config_error = str(exc)
                return
            for cfg in configs:
                rt = MCPRuntime(config=cfg)
                if not cfg.enabled:
                    self._runtimes[cfg.name] = rt
                    continue
                try:
                    self._start(rt)
                except Exception as exc:  # noqa: BLE001
                    rt.error = f"{type(exc).__name__}: {exc}"
                self._runtimes[cfg.name] = rt

    def _start(self, rt: MCPRuntime) -> None:
        env = {**os.environ, **rt.config.env}
        proc = subprocess.Popen(
            [rt.config.command, *rt.config.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        # If the process dies immediately, surface stderr.
        time.sleep(0.05)
        if proc.poll() is not None:
            err = b""
            if proc.stderr:
                try:
                    err = proc.stderr.read() or b""
                except Exception:
                    err = b""
            raise RuntimeError(
                f"进程立即退出 (code={proc.returncode}): {err.decode('utf-8', errors='replace')[:300]}"
            )
        session = _StdioSession(proc)
        rt.session = session
        session.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "grok-assistant", "version": "1.0.0"},
            },
        )
        session.notify("notifications/initialized")
        result = session.request("tools/list", {})
        tools = []
        if isinstance(result, dict):
            tools = list(result.get("tools") or [])
        rt.tools = [t for t in tools if isinstance(t, dict) and t.get("name")]

    def _shutdown_unlocked(self) -> None:
        for rt in self._runtimes.values():
            if rt.session:
                try:
                    rt.session.close()
                except Exception:
                    pass
        self._runtimes = {}

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown_unlocked()

    def status(self) -> dict[str, Any]:
        self.reload_if_needed()
        with self._lock:
            servers = []
            for rt in self._runtimes.values():
                servers.append(
                    {
                        "name": rt.config.name,
                        "enabled": rt.config.enabled,
                        "ok": rt.config.enabled and not rt.error,
                        "error": rt.error,
                        "note": rt.config.note,
                        "tools": [str(t.get("name") or "") for t in rt.tools],
                    }
                )
            return {
                "config_path": str(MCP_FILE),
                "example_path": str(EXAMPLE_FILE),
                "config_error": self._config_error,
                "servers": servers,
            }

    def tool_schemas(self) -> list[dict[str, Any]]:
        self.reload_if_needed()
        schemas: list[dict[str, Any]] = []
        with self._lock:
            for rt in self._runtimes.values():
                if not rt.config.enabled or rt.error:
                    continue
                prefix = rt.tool_prefix()
                for tool in rt.tools:
                    name = str(tool.get("name") or "")
                    if not name:
                        continue
                    params = tool.get("inputSchema") or tool.get("input_schema") or {
                        "type": "object",
                        "properties": {},
                    }
                    schemas.append(
                        {
                            "type": "function",
                            "function": {
                                "name": prefix + _sanitize_name(name),
                                "description": (
                                    f"MCP/{rt.config.name}: "
                                    + str(tool.get("description") or name)
                                )[:500],
                                "parameters": params if isinstance(params, dict) else {
                                    "type": "object",
                                    "properties": {},
                                },
                            },
                        }
                    )
        return schemas

    def call(self, tool_name: str, arguments: dict[str, Any]) -> str:
        self.reload_if_needed()
        with self._lock:
            for rt in self._runtimes.values():
                prefix = rt.tool_prefix()
                if not tool_name.startswith(prefix):
                    continue
                if not rt.session:
                    return json.dumps(
                        {"error": rt.error or f"MCP 服务器 {rt.config.name} 未连接"},
                        ensure_ascii=False,
                    )
                original = tool_name[len(prefix) :]
                # map sanitized name back to original if needed
                real = original
                for t in rt.tools:
                    if _sanitize_name(str(t.get("name") or "")) == original:
                        real = str(t.get("name"))
                        break
                try:
                    result = rt.session.request(
                        "tools/call",
                        {"name": real, "arguments": arguments or {}},
                    )
                except Exception as exc:  # noqa: BLE001
                    return json.dumps(
                        {"error": f"{type(exc).__name__}: {exc}"},
                        ensure_ascii=False,
                    )
                return json.dumps({"ok": True, "result": result}, ensure_ascii=False)
        return json.dumps({"error": f"未知 MCP 工具: {tool_name}"}, ensure_ascii=False)


manager = MCPManager()


def shutdown_mcp() -> None:
    manager.shutdown()


def mcp_status() -> dict[str, Any]:
    return manager.status()


def mcp_tool_schemas() -> list[dict[str, Any]]:
    try:
        return manager.tool_schemas()
    except Exception:
        return []


def mcp_call(name: str, arguments: dict[str, Any]) -> str:
    return manager.call(name, arguments)
