"""A minimal MCP server over stdio, for the tests: two read-only tools and
one that changes something. Speaks just enough of the protocol."""

import json
import sys

TOOLS = [
    {"name": "echo", "description": "Say something back.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                     "required": ["text"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "add", "description": "Add two numbers.",
     "inputSchema": {"type": "object", "properties": {"a": {"type": "number"},
                                                      "b": {"type": "number"}},
                     "required": ["a", "b"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "delete_everything", "description": "Deletes things.",
     "inputSchema": {"type": "object", "properties": {}}},
]
deleted = []

print("fake MCP server banner on stdout, to be ignored", flush=True)
for line in sys.stdin:
    message = json.loads(line)
    method, ident = message.get("method"), message.get("id")
    if ident is None:
        continue
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name, args = message["params"]["name"], message["params"].get("arguments", {})
        if name == "echo":
            result = {"content": [{"type": "text", "text": args["text"]}]}
        elif name == "add":
            result = {"content": [{"type": "text", "text": str(args["a"] + args["b"])}]}
        else:
            deleted.append(1)
            result = {"content": [{"type": "text", "text": "deleted"}]}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": ident,
                          "error": {"code": -32601, "message": "unknown"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}), flush=True)
