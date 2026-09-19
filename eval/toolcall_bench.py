"""Phase 0 go/no-go: is a Q4 14B-class model reliable enough at tool calling?

Sends 20 prompts against 4 fictional tools and scores four distinct failure
modes. The gate exists because a model can be fluent in conversation and still
be useless agentically — and finding that out in Phase 4, with the whole
product built around it, is the expensive path.

Usage: python eval/toolcall_bench.py --url http://localhost:8080 --name qwen3-14b
"""

import argparse
import json
import re
import urllib.error
import urllib.request

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": "Start a countdown timer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_seconds": {"type": "integer"},
                    "label": {"type": "string"},
                },
                "required": ["duration_seconds"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_notes",
            "description": "Search the user's personal notes and return matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "Send a text message to a contact. This is destructive "
            "and cannot be undone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["recipient", "body"],
            },
        },
    },
]

# expect=None means: the model should answer directly and call nothing.
# expect=EITHER means: calling or declining are both acceptable.
# check is given the parsed arguments dict and returns True if they're sane.
EITHER = "either"

CASES = [
    # --- single obvious call: can it call a tool at all -------------------
    ("What's the weather in Tokyo?", "get_weather",
     lambda a: a.get("city", "").strip().lower() == "tokyo"),
    ("Set a timer for 5 minutes.", "set_timer",
     lambda a: a.get("duration_seconds") == 300),
    ("Look through my notes for anything about the dentist.", "search_notes",
     lambda a: "dentist" in a.get("query", "").lower()),
    ("Text Marie and tell her I'm running late.", "send_message",
     lambda a: a.get("recipient", "").lower().startswith("mar") and bool(a.get("body"))),
    ("How cold is it in Reykjavik right now?", "get_weather",
     lambda a: "reykjav" in a.get("city", "").strip().lower()),

    # --- argument fidelity: units, enums, integer coercion ---------------
    ("What's the temperature in Miami in fahrenheit?", "get_weather",
     lambda a: a.get("unit") == "fahrenheit"),
    ("Give me the weather in Oslo in celsius.", "get_weather",
     lambda a: a.get("unit") == "celsius"),
    ("Set a timer for an hour and a half.", "set_timer",
     lambda a: a.get("duration_seconds") == 5400),
    ("Timer, 90 seconds, call it pasta.", "set_timer",
     lambda a: a.get("duration_seconds") == 90 and "pasta" in a.get("label", "").lower()),
    ("Find the three most recent notes mentioning taxes.", "search_notes",
     lambda a: a.get("limit") == 3 and "tax" in a.get("query", "").lower()),

    # --- must NOT call a tool: false positives are their own failure -----
    ("What's the capital of Portugal?", None, None),
    ("Explain what a GGUF file is in one sentence.", None, None),
    ("Thanks, that's all for now.", None, None),
    ("What can you help me with?", None, None),

    # --- chaining: result of step 1 is required for step 2 ---------------
    ("Check my notes for Marie's address, then text it to Paul.", "search_notes",
     lambda a: "marie" in a.get("query", "").lower()),
    ("Find out the weather in Lyon and message Sophie about it.", "get_weather",
     lambda a: "lyon" in a.get("city", "").strip().lower()),
    ("Look up my note about the recipe and set a timer for the baking time in it.",
     "search_notes", lambda a: "recipe" in a.get("query", "").lower()),

    # --- ambiguity: a required arg is missing. Asking the user and calling
    # with a placeholder are both defensible; only malformed output fails. ---
    ("Set a timer.", EITHER, None),
    ("Send a message.", EITHER, None),
    ("What's the weather like?", EITHER, None),
]


def call(url, messages, timeout, no_think=False):
    body = {
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0.0,
        "max_tokens": 512,
    }
    if no_think:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)["choices"][0]["message"]


def extract(message):
    """Return (tool_name, args_dict, malformed_reason).

    Also catches the common Q4 failure of emitting a tool call as prose/JSON in
    the content field instead of using the tool_calls channel.
    """
    calls = message.get("tool_calls") or []
    if calls:
        fn = calls[0].get("function", {})
        name = fn.get("name")
        raw = fn.get("arguments", "")
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError as e:
            return name, None, f"unparseable arguments: {e}"
        if not isinstance(args, dict):
            return name, None, f"arguments not an object: {type(args).__name__}"
        return name, args, None

    content = message.get("content") or ""
    if re.search(r'"?(name|function|tool_call)"?\s*[:=]', content):
        return None, None, "tool call leaked into content instead of tool_calls"
    return None, None, None


def validate(name, args):
    """Schema check the model's call against the advertised tool definition."""
    spec = next((t["function"] for t in TOOLS if t["function"]["name"] == name), None)
    if spec is None:
        return f"called unknown tool {name!r}"
    props = spec["parameters"]["properties"]
    for key in spec["parameters"].get("required", []):
        if key not in args:
            return f"missing required arg {key!r}"
    for key, value in args.items():
        if key not in props:
            return f"hallucinated arg {key!r}"
        expected = props[key].get("type")
        if expected == "integer" and not isinstance(value, int):
            return f"{key!r} should be integer, got {type(value).__name__}"
        if expected == "string" and not isinstance(value, str):
            return f"{key!r} should be string, got {type(value).__name__}"
        if "enum" in props[key] and value not in props[key]["enum"]:
            return f"{key!r}={value!r} outside enum {props[key]['enum']}"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--name", default="model")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--no-think", action="store_true")
    args = ap.parse_args()

    tally = {"ok": 0, "malformed": 0, "wrong_tool": 0, "bad_args": 0, "false_positive": 0}
    failures = []

    for prompt, expected, check in CASES:
        messages = [{"role": "user", "content": prompt}]
        try:
            message = call(args.url, messages, args.timeout, args.no_think)
        except (urllib.error.URLError, TimeoutError, KeyError) as e:
            tally["malformed"] += 1
            failures.append((prompt, f"request failed: {e}"))
            continue

        name, parsed, malformed = extract(message)

        if malformed:
            tally["malformed"] += 1
            failures.append((prompt, malformed))
            continue

        if expected is None:
            if name is None:
                tally["ok"] += 1
            else:
                tally["false_positive"] += 1
                failures.append((prompt, f"called {name} when no tool was needed"))
            continue

        if expected is EITHER:
            # Declining is fine. If it did call, the args still have to be
            # well-formed — underspecified prompts are where a model is most
            # likely to invent one.
            if name is not None:
                schema_error = validate(name, parsed)
                if schema_error:
                    tally["malformed"] += 1
                    failures.append((prompt, schema_error))
                    continue
            tally["ok"] += 1
            continue

        if name is None:
            tally["wrong_tool"] += 1
            failures.append((prompt, f"no tool call; expected {expected}"))
            continue

        schema_error = validate(name, parsed)
        if schema_error:
            tally["malformed"] += 1
            failures.append((prompt, schema_error))
            continue

        if name != expected:
            tally["wrong_tool"] += 1
            failures.append((prompt, f"called {name}, expected {expected}"))
            continue

        if check and not check(parsed):
            tally["bad_args"] += 1
            failures.append((prompt, f"args off: {json.dumps(parsed)}"))
            continue

        tally["ok"] += 1

    total = len(CASES)
    label = f"{args.name}{' (thinking off)' if args.no_think else ''}"
    print(f"\n=== {label} — {tally['ok']}/{total} passed ===")
    for key in ("malformed", "wrong_tool", "bad_args", "false_positive"):
        print(f"  {key:15s} {tally[key]}")
    if failures:
        print("\n--- failures ---")
        for prompt, reason in failures:
            print(f"  {prompt[:55]:<57s} {reason}")


if __name__ == "__main__":
    main()
