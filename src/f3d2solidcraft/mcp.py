"""Serve the converter over MCP on stdio, so an agent can import a Fusion file by asking.

Hand-written JSON-RPC rather than an SDK: the project's one runtime dependency is `zstandard`
(spec section 11), and the whole protocol this needs is four methods over newline-delimited JSON on
stdin and stdout. Nothing here may write anything else to stdout — a stray print is a protocol
error — so every failure comes back as a tool result with `isError`, and the engine's own output
stays on its own pipes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO

import zstandard

from f3d2solidcraft import __version__
from f3d2solidcraft.cli import convert, inspect
from f3d2solidcraft.engine import EngineError
from f3d2solidcraft.report import summary
from f3d2solidcraft.tools import ToolFailed, ToolMissing, find_tools

PROTOCOL_VERSION = "2025-06-18"

# The errors a bad file, a bad path or a missing tool raise: the same set the CLI turns into
# `error: …` and an exit code, reported here as a failed tool call instead of killing the server.
EXPECTED = (
    ToolMissing,
    ToolFailed,
    FileExistsError,
    FileNotFoundError,
    NotADirectoryError,
    IsADirectoryError,
    PermissionError,
    EngineError,
    ValueError,
    zstandard.ZstdError,
)

_PATH = {"type": "string", "description": "Absolute path, or one relative to the server's directory"}

TOOLS: list[dict] = [
    {
        "name": "import_f3d",
        "title": "Import a Fusion design into SolidCraft",
        "description": (
            "Convert a Fusion .f3d file or .f3z package into an editable .solidcraft design and say "
            "what happened to each feature. Features the file records fully are rebuilt as a "
            "SolidCraft timeline; what cannot be rebuilt comes back as imported B-rep, so the "
            "result is always the whole solid. The answer says how many features were rebuilt "
            "(exact or inferred), how many fell back, whether the result matches the original and "
            "where the design and the report were written."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": _PATH | {"description": "The .f3d or .f3z to convert"},
                "output": _PATH
                | {
                    "description": (
                        "Where to write the .solidcraft design. Defaults to the input's own name "
                        "with a .solidcraft suffix, beside it."
                    )
                },
                "report": _PATH
                | {
                    "description": (
                        "Where to write the JSON report, feature by feature. Defaults to the "
                        "output's name with a .report.json suffix."
                    )
                },
                "force": {
                    "type": "boolean",
                    "description": "Replace the output and the report if they already exist.",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "output": {"type": "string"},
                "report": {"type": "string"},
                "verified": {"type": "boolean"},
                "counts": {"type": "object"},
                "bodies": {"type": "object"},
                "warnings": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["output", "report", "verified", "counts", "bodies"],
        },
    },
    {
        "name": "inspect_f3d",
        "title": "List what a Fusion design holds",
        "description": (
            "Read a .f3d or .f3z and list its parameters, sketches and timeline, saying for each "
            "kind of feature whether this converter maps it or would fall back. Reads the file "
            "only: no engine, no conversion, nothing written."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": _PATH | {"description": "The .f3d or .f3z to read"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
]


def _import_f3d(arguments: dict) -> tuple[str, dict]:
    """Run a conversion; returns the text for the caller and the structured result."""
    source = Path(str(arguments["path"])).expanduser()
    output = Path(str(arguments["output"])).expanduser() if arguments.get("output") else None
    if output is None:
        output = source.with_suffix(".solidcraft")
    report_path = Path(str(arguments["report"])).expanduser() if arguments.get("report") else None
    if report_path is None:
        report_path = output.with_name(output.stem + ".report.json")
    report = convert(source, output, report_path, bool(arguments.get("force")), find_tools())
    structured = {
        "output": str(output.resolve()),
        "report": str(report_path.resolve()),
        "verified": bool(report["verification"]["ok"]),
        "counts": report["counts"],
        "bodies": report["bodies"],
        "warnings": list(report.get("warnings") or []),
    }
    text = f"{summary(report)}\n  design: {structured['output']}\n  report: {structured['report']}"
    return text, structured


def _inspect_f3d(arguments: dict) -> tuple[str, dict | None]:
    return inspect(Path(str(arguments["path"])).expanduser(), find_tools()), None


def _call_tool(params: dict) -> dict:
    """The `tools/call` result, with a failure reported as a result rather than as an error.

    A tool that failed is an answer the caller can act on — the path was wrong, the engine is not
    installed — so it comes back as `isError` with the message, which is what the protocol asks for.
    A bug in this server is not: that stays an exception and becomes a JSON-RPC error.
    """
    name = params.get("name")
    arguments = params.get("arguments") or {}
    runner = {"import_f3d": _import_f3d, "inspect_f3d": _inspect_f3d}.get(str(name))
    if runner is None:
        return {"content": [{"type": "text", "text": f"no such tool: {name}"}], "isError": True}
    if not arguments.get("path"):
        return {"content": [{"type": "text", "text": "path is required"}], "isError": True}
    try:
        text, structured = runner(arguments)
    except EXPECTED as exc:
        return {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True}
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": False}
    if structured is not None:
        result["structuredContent"] = structured
    return result


def handle(message: dict) -> dict | None:
    """The reply to one request, or None for a notification (which takes no reply)."""
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {
            # Answer in the version the client asked for when it names one: it knows which of them
            # it speaks, and every method here is the same in all of them.
            "protocolVersion": asked if isinstance(asked, str) else PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "f3d2solidcraft", "version": __version__},
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return _call_tool(params)
    if method == "ping":
        return {}
    if method is not None and str(method).startswith("notifications/"):
        return None
    raise LookupError(f"unknown method: {method}")


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Read requests until stdin closes. One JSON object per line, in and out."""
    source, sink = stdin or sys.stdin, stdout or sys.stdout
    for line in source:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(sink, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}})
            continue
        ident = message.get("id")
        try:
            result = handle(message)
        except LookupError as exc:
            if ident is not None:
                _write(sink, {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": str(exc)}})
            continue
        except Exception as exc:  # noqa: BLE001 — a server that dies on one bad request is
            # worse than one that answers it with an error: the client keeps its session.
            if ident is not None:
                code = {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}
                _write(sink, {"jsonrpc": "2.0", "id": ident, "error": code})
            continue
        if result is not None and ident is not None:
            _write(sink, {"jsonrpc": "2.0", "id": ident, "result": result})
    return 0


def _write(sink: TextIO, message: dict) -> None:
    sink.write(json.dumps(message) + "\n")
    sink.flush()
