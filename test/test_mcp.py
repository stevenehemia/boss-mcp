#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# MCP transport helpers
# ---------------------------------------------------------------------------

def read_message(stream) -> Optional[dict]:
    content_length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.rstrip(b"\r\n")
        if not line:
            break  # blank line terminates headers
        parts = line.split(b":", 1)
        if len(parts) != 2:
            continue
        header, value = parts[0].strip().lower(), parts[1].strip()
        if header == b"content-length":
            content_length = int(value)
    if content_length is None:
        return None
    payload = stream.read(content_length)
    return json.loads(payload.decode("utf-8")) if payload else None


def send_request(proc, request: dict) -> None:
    payload = json.dumps(request, separators=(",", ":")).encode("utf-8")
    frame = b"Content-Length: " + str(len(payload)).encode("ascii") + b"\r\n\r\n" + payload
    proc.stdin.write(frame)
    proc.stdin.flush()


def wait_for_response(proc, request_id: int) -> dict:
    while True:
        msg = read_message(proc.stdout)
        if msg is None:
            raise RuntimeError("Server closed stdout unexpectedly")
        if "id" not in msg:
            continue  # notification — skip
        if msg.get("id") == request_id:
            return msg


# ---------------------------------------------------------------------------
# Protocol assertion helpers
# ---------------------------------------------------------------------------

def assert_envelope(msg: dict, expected_id: int) -> None:
    """Every JSON-RPC 2.0 response must have jsonrpc, a matching id, and exactly
    one of result or error."""
    assert msg.get("jsonrpc") == "2.0", \
        f"missing jsonrpc 2.0 field: {msg}"
    assert msg.get("id") == expected_id, \
        f"id mismatch: expected {expected_id}, got {msg.get('id')}"
    has_result = "result" in msg
    has_error  = "error" in msg
    assert has_result or has_error, \
        f"response has neither result nor error: {msg}"
    assert not (has_result and has_error), \
        f"response has both result and error: {msg}"


def assert_error_shape(error: dict, expected_code: int) -> None:
    assert isinstance(error.get("code"), int), \
        f"error.code must be int: {error}"
    assert isinstance(error.get("message"), str), \
        f"error.message must be str: {error}"
    assert error["code"] == expected_code, \
        f"expected error code {expected_code}, got {error['code']}: {error}"


# ---------------------------------------------------------------------------
# MCP session helpers
# ---------------------------------------------------------------------------

def handshake(proc, req_id: int) -> None:
    send_request(proc, {"jsonrpc": "2.0", "id": req_id, "method": "initialize", "params": {}})
    resp = wait_for_response(proc, req_id)
    assert_envelope(resp, req_id)

    result = resp["result"]
    assert isinstance(result.get("protocolVersion"), str), \
        f"protocolVersion must be a string: {result}"
    info = result.get("serverInfo", {})
    assert isinstance(info.get("name"), str) and isinstance(info.get("version"), str), \
        f"serverInfo must have string name and version: {result}"
    caps = result.get("capabilities", {})
    assert "tools" in caps,   f"capabilities missing 'tools': {result}"
    assert "logging" in caps, f"capabilities missing 'logging': {result}"

    send_request(proc, {"jsonrpc": "2.0", "method": "initialized"})


def check_tools_list(proc, req_id: int, format_flag: str) -> None:
    send_request(proc, {"jsonrpc": "2.0", "id": req_id, "method": "tools/list"})
    resp = wait_for_response(proc, req_id)
    assert_envelope(resp, req_id)

    tools = resp["result"].get("tools", [])
    assert isinstance(tools, list) and tools, \
        f"tools must be a non-empty list: {resp}"

    tool = next((t for t in tools if t.get("name") == "boss_evaluate"), None)
    assert tool is not None, "boss_evaluate not found in tools/list"
    assert isinstance(tool.get("description"), str) and tool["description"], \
        f"boss_evaluate must have a non-empty description: {tool}"

    schema = tool.get("inputSchema", {})
    assert schema.get("type") == "object", \
        f"inputSchema.type must be 'object': {schema}"
    props = schema.get("properties", {})
    assert "expression" in props, \
        f"inputSchema.properties must contain 'expression': {schema}"
    assert "expression" in schema.get("required", []), \
        f"'expression' must be in inputSchema.required: {schema}"

    expected_expr_type = "array" if format_flag == "--format=expressionjson" else "object"
    actual_expr_type = props["expression"].get("type")
    assert actual_expr_type == expected_expr_type, \
        f"expression schema type: expected '{expected_expr_type}', got '{actual_expr_type}'"


def check_unknown_method(proc, req_id: int) -> None:
    send_request(proc, {"jsonrpc": "2.0", "id": req_id, "method": "nonexistent/method"})
    resp = wait_for_response(proc, req_id)
    assert_envelope(resp, req_id)
    assert "error" in resp, \
        f"expected error response for unknown method, got: {resp}"
    assert_error_shape(resp["error"], expected_code=-32601)


def check_invalid_expression(proc, req_id: int, format_flag: str) -> None:
    invalid = [] if format_flag == "--format=expressionjson" else {"not": "valid"}
    send_request(proc, {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": "boss_evaluate", "arguments": {"expression": invalid}},
    })
    resp = wait_for_response(proc, req_id)
    assert_envelope(resp, req_id)
    result = resp["result"]
    assert result.get("isError") is True, \
        f"expected isError=true for invalid expression, got: {result}"
    content = result.get("content", [])
    assert content and content[0].get("type") == "text" and content[0].get("text"), \
        f"invalid expression error must return a non-empty text content item: {result}"


def evaluate(proc, expression, req_id: int) -> dict:
    send_request(proc, {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": "boss_evaluate", "arguments": {"expression": expression}},
    })
    resp = wait_for_response(proc, req_id)
    assert_envelope(resp, req_id)

    result = resp["result"]
    assert isinstance(result.get("isError"), bool), \
        f"isError must be a boolean: {result}"
    assert not result["isError"], \
        f"tool returned unexpected error: {result}"
    content = result.get("content", [])
    assert isinstance(content, list) and content, \
        f"content must be a non-empty list: {result}"
    assert content[0].get("type") == "text", \
        f"content[0].type must be 'text': {content[0]}"
    assert "text" in content[0], \
        f"content[0] missing 'text' field: {content[0]}"
    return json.loads(content[0]["text"])


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

EXPRESSION_JSON_CASES = [
    {
        "description": "Filter rows where A > 2",
        "input": ["Filter",
                  ["Table", ["A", ["Integer", 1], ["Integer", 2], ["Integer", 3]]],
                  ["Greater", ["Symbol", "A"], ["Integer", 2]]],
        "expected": ["Table", ["A", ["Integer", 3]]],
    },
    {
        "description": "Filter rows where 1 < A < 3 (And predicate)",
        "input": ["Filter",
                  ["Table", ["A", ["Integer", 1], ["Integer", 2], ["Integer", 3]]],
                  ["And",
                   ["Greater", ["Symbol", "A"], ["Integer", 1]],
                   ["Less",    ["Symbol", "A"], ["Integer", 3]]]],
        "expected": ["Table", ["A", ["Integer", 2]]],
    },
]

REGULAR_JSON_CASES = [
    {
        "description": "Filter rows where A > 2",
        "input": {
            "type": "call", "head": "Filter",
            "args": [
                {"type": "call", "head": "Table", "args": [
                    {"type": "call", "head": "A", "args": [
                        {"type": "long", "value": 1},
                        {"type": "long", "value": 2},
                        {"type": "long", "value": 3},
                    ]},
                ]},
                {"type": "call", "head": "Greater", "args": [
                    {"type": "symbol", "value": "A"},
                    {"type": "long", "value": 2},
                ]},
            ],
        },
        "expected": {
            "type": "call", "head": "Table",
            "args": [
                {"type": "call", "head": "A", "args": [
                    {"type": "long", "value": 3},
                ]},
            ],
        },
    },
    {
        "description": "Filter rows where 1 < A < 3 (And predicate)",
        "input": {
            "type": "call", "head": "Filter",
            "args": [
                {"type": "call", "head": "Table", "args": [
                    {"type": "call", "head": "A", "args": [
                        {"type": "long", "value": 1},
                        {"type": "long", "value": 2},
                        {"type": "long", "value": 3},
                    ]},
                ]},
                {"type": "call", "head": "And", "args": [
                    {"type": "call", "head": "Greater", "args": [
                        {"type": "symbol", "value": "A"},
                        {"type": "long", "value": 1},
                    ]},
                    {"type": "call", "head": "Less", "args": [
                        {"type": "symbol", "value": "A"},
                        {"type": "long", "value": 3},
                    ]},
                ]},
            ],
        },
        "expected": {
            "type": "call", "head": "Table",
            "args": [
                {"type": "call", "head": "A", "args": [
                    {"type": "long", "value": 2},
                ]},
            ],
        },
    },
]


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_format_test(exe: str, format_flag: str, cases: list) -> int:
    print(f"\n=== {format_flag} ===")
    proc = subprocess.Popen(
        [exe, format_flag],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    failed = 0
    try:
        req_id = 1
        protocol_checks = [
            ("initialize response structure",  lambda: handshake(proc, req_id)),
            ("tools/list schema",              lambda: check_tools_list(proc, req_id + 1, format_flag)),
            ("unknown method returns -32601",  lambda: check_unknown_method(proc, req_id + 2)),
            ("invalid expression returns error", lambda: check_invalid_expression(proc, req_id + 3, format_flag)),
        ]
        for description, check in protocol_checks:
            try:
                check()
                print(f"  PASS  {description}")
            except AssertionError as e:
                print(f"  FAIL  {description}: {e}")
                failed += 1

        for i, case in enumerate(cases):
            try:
                result = evaluate(proc, case["input"], req_id=req_id + 4 + i)
                if result == case["expected"]:
                    print(f"  PASS  {case['description']}")
                else:
                    print(f"  FAIL  {case['description']}")
                    print(f"        expected: {json.dumps(case['expected'])}")
                    print(f"        got:      {json.dumps(result)}")
                    failed += 1
            except AssertionError as e:
                print(f"  FAIL  {case['description']}: {e}")
                failed += 1

    except RuntimeError as e:
        print(f"  FAIL  Server error: {e}")
        failed += 1
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=2)

    return failed


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    exe = os.path.abspath(os.path.join(here, "..", "build", "boss_mcp"))

    failed = 0
    failed += run_format_test(exe, "--format=expressionjson", EXPRESSION_JSON_CASES)
    failed += run_format_test(exe, "--format=regular", REGULAR_JSON_CASES)

    protocol_checks_per_format = 4
    total = (protocol_checks_per_format + len(EXPRESSION_JSON_CASES) +
             protocol_checks_per_format + len(REGULAR_JSON_CASES))
    passed = total - failed
    print(f"\n{passed}/{total} tests passed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
