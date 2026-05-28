#!/usr/bin/env python3
import json
import subprocess
import sys
from typing import Optional


def pretty(obj: dict) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True)


def read_message(stream) -> Optional[dict]:
    content_length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.rstrip(b"\r\n")
        if not line:
            break
        parts = line.split(b":", 1)
        if len(parts) != 2:
            continue
        header = parts[0].strip().lower()
        value = parts[1].strip()
        if header == b"content-length":
            content_length = int(value)
    if content_length is None:
        return None
    payload = stream.read(content_length)
    if not payload:
        return None
    return json.loads(payload.decode("utf-8"))


def send_request(proc, request: dict) -> None:
    print("-->\n" + pretty(request))
    payload = json.dumps(request, separators=(",", ":")).encode("utf-8")
    message = b"Content-Length: " + str(len(payload)).encode("ascii") + b"\r\n\r\n" + payload
    proc.stdin.write(message)
    proc.stdin.flush()


def wait_for_response(proc, request_id: int) -> dict:
    while True:
        msg = read_message(proc.stdout)
        if msg is None:
            raise RuntimeError("Server closed stdout")
        print("<--\n" + pretty(msg))
        if "id" not in msg:
            # Notification; ignore and keep waiting.
            continue
        if msg.get("id") == request_id:
            return msg


def main() -> int:
    exe = "/home/steve/Core/boss-mcp/build/boss_mcp"
    proc = subprocess.Popen(
        [exe],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # initialize
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {}
        }
        send_request(proc, req)
        resp = wait_for_response(proc, 1)
        assert "result" in resp, f"initialize failed: {resp}"

        # initialized (notification, no response expected)
        req = {
            "jsonrpc": "2.0",
            "method": "initialized"
        }
        send_request(proc, req)

        # tools/list
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list"
        }
        send_request(proc, req)
        resp = wait_for_response(proc, 2)
        tools = resp.get("result", {}).get("tools", [])
        assert any(t.get("name") == "boss.evaluate" for t in tools), f"tools/list missing boss.evaluate: {resp}"

        # tools/call
        req = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "boss.evaluate",
                "arguments": {
                    "expression": {
                        "type": "call",
                        "head": "Plus",
                        "args": [
                            {"type": "long", "value": 1},
                            {"type": "long", "value": 2}
                        ]
                    }
                },
            },
        }
        send_request(proc, req)
        resp = wait_for_response(proc, 4)
        content = resp.get("result", {}).get("content", [])
        assert content, f"tools/call returned no content: {resp}"
        result_json = json.loads(content[0].get("text", "{}"))
        expected_expression = {"type": "long", "value": 3}
        assert result_json == expected_expression, f"unexpected result: {result_json}"

        return 0

    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=2)


if __name__ == "__main__":
    sys.exit(main())
