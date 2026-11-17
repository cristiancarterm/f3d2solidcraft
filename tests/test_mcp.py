"""The MCP server, spoken to as a client speaks to it: a real process over real stdio.

No in-process shortcut and nothing faked: the tests launch `f3d2solidcraft mcp`, write JSON-RPC
lines to its stdin and read its answers, and the import they ask for is a real conversion of a real
`.f3d` by the real engine. That is the only way to catch what matters most here — that nothing but
protocol reaches stdout, which an in-process call would never notice.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from f3d2solidcraft.mcp import TOOLS, handle


class Client:
    """A client for the server process: send a request, read the reply for that id."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self.next_id = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}})
        line = self.proc.stdout.readline()
        assert line, f"the server answered nothing to {method}: {self.proc.stderr.read()}"
        message = json.loads(line)
        assert message["jsonrpc"] == "2.0" and message["id"] == self.next_id, message
        return message

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def _send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()


@pytest.fixture
def client():
    proc = subprocess.Popen(
        [sys.executable, "-m", "f3d2solidcraft", "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    talk = Client(proc)
    hello = talk.request(
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
    )
    assert hello["result"]["serverInfo"]["name"] == "f3d2solidcraft"
    assert hello["result"]["capabilities"]["tools"] == {}
    talk.notify("notifications/initialized")
    try:
        yield talk
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def test_a_client_can_import_a_real_design_through_the_protocol(client, sample_f3d, tools, tmp_path):
    """The whole point of the server: one call converts a file and says what came of it.

    Asked of the sample, whose every body comes back as a fallback import, so the counts are the
    ones `convert` itself reports — this asks whether the protocol carries them, not what they are.
    """
    out = tmp_path / "box.solidcraft"
    answer = client.request(
        "tools/call", {"name": "import_f3d", "arguments": {"path": str(sample_f3d), "output": str(out)}}
    )
    result = answer["result"]
    assert result["isError"] is False, result
    structured = result["structuredContent"]
    assert structured["verified"] is True
    # Two of the three are the replay's own under cadmpeg 0.6.8 (spec section 95).
    assert structured["bodies"] == {"native": 2, "imported": 1}
    assert structured["counts"]["exact"] >= 2
    assert out.is_file() and out.stat().st_size > 0
    # the report lands beside the design when the call does not say where
    report = Path(structured["report"])
    assert report == out.with_name("box.report.json") and report.is_file()
    assert json.loads(report.read_text())["verification"]["ok"] is True
    text = result["content"][0]["text"]
    assert "verified: yes" in text and str(out) in text


def test_a_bad_path_is_a_failed_call_and_the_server_stays_up(client, tmp_path):
    """A wrong path is an answer the caller can act on, not a dead session: `isError`, then more."""
    answer = client.request(
        "tools/call", {"name": "import_f3d", "arguments": {"path": str(tmp_path / "no.f3d")}}
    )
    assert answer["result"]["isError"] is True
    assert "no.f3d" in answer["result"]["content"][0]["text"]
    assert client.request("ping")["result"] == {}  # still talking


def test_the_tools_are_listed_with_what_they_need(client):
    listed = client.request("tools/list")["result"]["tools"]
    assert [t["name"] for t in listed] == ["import_f3d", "inspect_f3d"]
    assert all(t["inputSchema"]["required"] == ["path"] for t in listed)
    assert listed == TOOLS


def test_inspect_reads_the_file_and_writes_nothing(client, sample_f3d, tools, tmp_path):
    """`inspect_f3d` is the read-only half: it names the timeline and leaves no file behind."""
    before = sorted(p.name for p in tmp_path.iterdir())
    answer = client.request("tools/call", {"name": "inspect_f3d", "arguments": {"path": str(sample_f3d)}})
    result = answer["result"]
    assert result["isError"] is False
    assert "parameters" in result["content"][0]["text"]
    assert "structuredContent" not in result  # nothing structured to report, so none is claimed
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_an_unknown_method_is_a_jsonrpc_error_and_an_unknown_tool_is_not(client):
    """The two kinds of "no such thing" the protocol separates, which this server keeps apart."""
    assert client.request("sculpt/everything")["error"]["code"] == -32601
    answer = client.request("tools/call", {"name": "sculpt_everything", "arguments": {"path": "x"}})
    assert answer["result"]["isError"] is True
    assert "no such tool" in answer["result"]["content"][0]["text"]


def test_a_notification_is_answered_with_nothing():
    """Answering a notification would be a protocol error, so `handle` says there is no reply."""
    assert handle({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None
    assert handle({"jsonrpc": "2.0", "id": 1, "method": "ping"}) == {}
