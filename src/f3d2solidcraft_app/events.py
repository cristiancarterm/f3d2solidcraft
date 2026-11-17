"""Reading `convert --progress`: one JSON object per line; anything else on stdout is not an event."""

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    index: int
    total: int
    name: str


@dataclass(frozen=True)
class Done:
    exit_code: int


def parse(line: str) -> Feature | Done | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    # The window calls this per stdout line on the UI thread: a malformed line is not an event,
    # never an exception.
    try:
        payload = json.loads(line)
        if not isinstance(payload, dict):
            return None
        if payload.get("event") == "feature":
            return Feature(int(payload["index"]), int(payload["total"]), str(payload["name"]))
        if payload.get("event") == "done":
            return Done(int(payload["exit"]))
    except (ValueError, TypeError, KeyError):
        return None
    return None
