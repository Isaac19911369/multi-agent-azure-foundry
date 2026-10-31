"""Tool definitions: JSON schema from type hints, per-agent allow-lists, approval gate."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

_JSON = {str: "string", int: "integer", float: "number", bool: "boolean"}


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., Any]
    requires_approval: bool = False

    @classmethod
    def from_function(
        cls, fn: Callable, requires_approval: bool = False, name: str | None = None
    ) -> Tool:
        hints = get_type_hints(fn)
        doc = inspect.getdoc(fn) or fn.__name__
        arg_docs = dict(line.strip().split(":", 1) for line in doc.splitlines()[1:] if ":" in line)
        props, required = {}, []
        for pname, p in inspect.signature(fn).parameters.items():
            props[pname] = {"type": _JSON.get(hints.get(pname, str), "string")}
            if pname in arg_docs:
                props[pname]["description"] = arg_docs[pname].strip()
            if p.default is inspect.Parameter.empty:
                required.append(pname)
        return cls(
            name or fn.__name__,
            doc.splitlines()[0],
            {"type": "object", "properties": props, "required": required},
            fn,
            requires_approval,
        )

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def invoke(self, arguments: dict) -> str:
        unexpected = set(arguments) - set(self.parameters["properties"])
        missing = set(self.parameters["required"]) - set(arguments)
        if unexpected or missing:
            return json.dumps(
                {
                    "error": f"bad arguments; unexpected={sorted(unexpected)} "
                    f"missing={sorted(missing)}"
                }
            )
        try:
            return json.dumps(self.fn(**arguments), default=str)
        except Exception as exc:  # a failing tool must not crash the agent loop
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
