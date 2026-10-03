#!/usr/bin/env python3
"""Native feature checks inside a disposable container, without account login.

The production importer builds fresh profiles. A local MCP server and simulated
Anthropic endpoint exercise discovery without external requests or credentials.
"""

import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print("PASS: " + message, flush=True)


def mcp_server():
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        method = request["method"]
        if method == "initialize":
            result = {
                "protocolVersion": request["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "lockbox-fixture", "version": "1.0"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "lockbox_probe",
                        "description": "Local fixture",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            }
        else:
            result = {"resources": [], "resourceTemplates": [], "prompts": []}
        print(
            json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}),
            flush=True,
        )


def environment(home, provider):
    # Fresh disposable state and a synthetic key; no inherited authentication.
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(
            part in key.upper()
            for part in (
                "TOKEN",
                "API_KEY",
                "AUTH",
                "CODEX",
                "CLAUDE",
                "ANTHROPIC",
                "OPENAI",
            )
        )
    }
    env.update(
        HOME=str(home),
        BASH_ENV="",
        DISABLE_TELEMETRY="1",
        DISABLE_ERROR_REPORTING="1",
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
    )
    env["CLAUDE_CONFIG_DIR" if provider == "claude" else "CODEX_HOME"] = str(
        home / ("." + provider)
    )
    return env


def command(args, cwd, env):
    return subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, timeout=45
    )


def claude_features(home, workspace, env):
    result = command(["claude", "mcp", "list"], workspace, env)
    require(
        result.returncode == 0
        and "lockbox-fixture" in result.stdout
        and "Connected" in result.stdout,
        "Claude connects the imported local MCP server",
    )
    requests = []
    marker = workspace / "guard-must-not-execute.txt"
    edited = workspace / "post-edit-fixture.txt"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if self.path.startswith("/v1/messages/count_tokens"):
                payload = {"input_tokens": 20}
            else:
                payload = {
                    "id": "msg_lockbox_fixture",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "content": [{"type": "text", "text": "fixture response"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 20, "output_tokens": 2},
                }
                # Claude may send a speculative request before the actual turn.
                # Repeat tools until their results arrive, rather than counting requests.
                completed_tools = any(
                    isinstance(message.get("content"), list)
                    and any(block.get("type") == "tool_result" for block in message["content"])
                    for message in body.get("messages", [])
                )
                if not completed_tools and any(t.get("name") == "Bash" for t in body.get("tools", [])):
                    payload["stop_reason"] = "tool_use"
                    payload["content"] = [
                        {
                            "type": "tool_use", "id": "tool_guard_fixture", "name": "Bash",
                            # Safe even if the guard regresses: help plus a temporary marker.
                            "input": {"command": f"dd --help; printf unexpected > '{marker}'"},
                        },
                        {
                            "type": "tool_use", "id": "tool_edit_fixture", "name": "Write",
                            "input": {"file_path": str(edited), "content": "chmod 777 /tmp/fixture\n"},
                        },
                    ]
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env.update(
            ANTHROPIC_API_KEY="lockbox-synthetic-key",
            ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_port}",
        )
        result = command(
            [
                "claude",
                "--plugin-dir",
                str(home / ".claude/plugins/local-fixture"),
                "-p",
                "Return fixture response",
                "--output-format",
                "stream-json",
                "--verbose",
                "--max-turns",
                "3",
                "--allowedTools",
                "Bash,Write",
            ],
            workspace,
            env,
        )
        records = [
            json.loads(line)
            for line in result.stdout.splitlines()
            if line.startswith("{")
        ]
        require(result.returncode == 0, "Claude finishes the synthetic local tool session")
        initial = next(
            (
                record
                for record in records
                if record.get("type") == "system" and record.get("subtype") == "init"
            ),
            {},
        )
        require(
            "lockbox-shared-fixture" in initial.get("slash_commands", []),
            "Claude discovers the imported shared skill",
        )
        require("lockbox-readonly-fixture" in initial.get("agents", []),
                "Claude discovers the imported role with an inherited model and read-only tools")
        require(
            any(
                "lockbox-plugin-fixture:" in name and "plugin-skill" in name
                for name in initial.get("slash_commands", [])
            ),
            "Claude activates an imported local plugin through its native plugin-dir option",
        )
        wire = json.dumps(requests)
        if marker.exists() or "Blocked by local safety guard" not in wire:
            print("Synthetic Claude hook diagnostics: " + result.stdout[-6000:], flush=True)
            print("Synthetic local API diagnostics: " + wire[-6000:], flush=True)
        require(not marker.exists() and "Blocked by local safety guard" in wire,
                "Claude applies the managed PreToolUse denial before executing Bash")
        require(edited.is_file() and "[security-guidance hook]" in wire,
                "Claude runs the baked PostToolUse monitor after an actual Write")
        require(
            "LOCKBOX_GLOBAL_INSTRUCTION_FIXTURE" in wire
            and "LOCKBOX_PROJECT_INSTRUCTION_FIXTURE" in wire,
            "Claude loads shared global guidance and project AGENTS.md",
        )
        require(any(json.dumps(request).count("LOCKBOX_PROJECT_INSTRUCTION_FIXTURE") == 1
                    and "LOCKBOX_NESTED_INSTRUCTION_FIXTURE" in json.dumps(request) for request in requests),
                "Claude loads nested guidance and deduplicates the CLAUDE.md AGENTS import")
    finally:
        server.shutdown()
        server.server_close()


def codex_features(home, workspace, env):
    result = command(["codex", "mcp", "list", "--json"], workspace, env)
    require(
        result.returncode == 0
        and any(
            server["name"] == "lockbox-fixture" for server in json.loads(result.stdout)
        ),
        "Codex recognizes the imported MCP configuration",
    )
    requests = []
    received = threading.Event()
    marker = workspace / "guard-must-not-execute.txt"
    edited = workspace / "post-edit-fixture.txt"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            received.set()
            body = requests[-1]
            completed_tools = any(item.get("type") in ("function_call_output", "custom_tool_call_output") for item in body.get("input", []))
            output = [{"type": "message", "id": "msg_fixture", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "fixture response", "annotations": []}]}] if completed_tools else [
                {"type": "function_call", "id": "fc_guard", "call_id": "call_guard", "name": "exec_command", "arguments": json.dumps({"cmd": f"dd --help; printf unexpected > '{marker}'"})},
                {"type": "custom_tool_call", "id": "ct_edit", "call_id": "call_edit", "name": "apply_patch", "input": "*** Begin Patch\n*** Add File: " + str(edited) + "\n+chmod 777 /tmp/fixture\n*** End Patch"},
            ]
            response = {"id": "resp_fixture", "object": "response", "model": body.get("model"), "status": "completed", "output": output, "usage": {"input_tokens": 20, "output_tokens": 2, "total_tokens": 22}}
            events = [("response.created", {"response": {**response, "status": "in_progress", "output": []}})]
            for index, item in enumerate(output):
                events.extend([("response.output_item.added", {"output_index": index, "item": item}), ("response.output_item.done", {"output_index": index, "item": item})])
            events.append(("response.completed", {"response": response}))
            data = "".join("event: " + event + "\ndata: " + json.dumps({"type": event, **value}) + "\n\n" for event, value in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    process = subprocess.Popen(
        [
            "codex",
            "app-server",
            "-c",
            'model_provider="lockbox-fixture"',
            "-c",
            'model_providers.lockbox-fixture.name="LockBox fixture"',
            "-c",
            f'model_providers.lockbox-fixture.base_url="http://127.0.0.1:{server.server_port}/v1"',
            "-c",
            'model_providers.lockbox-fixture.wire_api="responses"',
        ],
        cwd=workspace,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    lines = queue.Queue()

    def reader():
        for line in process.stdout:
            lines.put(json.loads(line))

    threading.Thread(target=reader, daemon=True).start()
    counter = 0

    def rpc(method, params):
        nonlocal counter
        counter += 1
        process.stdin.write(
            json.dumps({"id": counter, "method": method, "params": params}) + "\n"
        )
        process.stdin.flush()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = lines.get(timeout=max(0.1, deadline - time.monotonic()))
            if response.get("id") == counter:
                require(
                    "error" not in response, "Codex native request succeeds: " + method
                )
                return response["result"]
        raise AssertionError("Codex native request timed out: " + method)

    try:
        rpc(
            "initialize",
            {
                "clientInfo": {"name": "lockbox_fixture", "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        process.stdin.write('{"method":"initialized","params":{}}\n')
        process.stdin.flush()
        hooks = rpc("hooks/list", {"cwds": [str(workspace)]})
        configured = [hook for entry in hooks["data"] for hook in entry["hooks"]]
        require(
            not any(entry["errors"] for entry in hooks["data"])
            and all(any(hook["eventName"] == event and hook["enabled"] and hook["isManaged"]
                        and hook["trustStatus"] == "managed" for hook in configured)
                    for event in ("preToolUse", "postToolUse")),
            "Codex loads the enforced shared pre-tool and post-edit hooks as managed policy",
        )
        skills = rpc("skills/list", {"cwds": [str(workspace)], "forceReload": True})
        require(
            any(
                skill["name"] == "lockbox-shared-fixture" and skill["enabled"]
                for entry in skills["data"]
                for skill in entry["skills"]
            ),
            "Codex discovers and enables the imported shared skill",
        )
        thread = rpc("thread/start", {"cwd": str(workspace), "ephemeral": True, "sandbox": "danger-full-access", "approvalPolicy": "never"})
        status = rpc("mcpServerStatus/list", {"limit": 100})
        require(
            any(
                server["name"] == "lockbox-fixture"
                and "lockbox_probe" in server.get("tools", {})
                for server in status["data"]
            ),
            "Codex connects the imported local MCP server and discovers its tool",
        )
        rpc(
            "turn/start",
            {
                "threadId": thread["thread"]["id"],
                "input": [{"type": "text", "text": "Return fixture response"}],
            },
        )
        require(
            received.wait(timeout=20),
            "Codex sends the synthetic request only to the local endpoint",
        )
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            notice = lines.get(timeout=max(0.1, deadline - time.monotonic()))
            if notice.get("method") == "turn/completed":
                break
        else:
            raise AssertionError("Codex local fixture turn did not complete")
        wire = json.dumps(requests)
        if marker.exists() or "Blocked by local safety guard" not in wire or not edited.exists():
            print("Synthetic Codex tool diagnostics: " + wire[-5000:], flush=True)
        require(not marker.exists() and "Blocked by local safety guard" in wire,
                "Codex applies the managed PreToolUse denial before executing Bash")
        require(edited.is_file() and "[security-guidance hook]" in wire,
                "Codex runs the baked PostToolUse monitor after an actual patch")
        require(
            "LOCKBOX_GLOBAL_INSTRUCTION_FIXTURE" in wire
            and "LOCKBOX_PROJECT_INSTRUCTION_FIXTURE" in wire,
            "Codex loads shared global guidance and project AGENTS.md",
        )
        require("LOCKBOX_NESTED_INSTRUCTION_FIXTURE" in wire,
                "Codex loads nested project guidance")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        server.shutdown()
        server.server_close()


def main():
    provider, tool_path = sys.argv[1:]
    require(os.getuid() == 1000, "native feature fixture runs as dev")
    spec = importlib.util.spec_from_file_location("agent_config", tool_path)
    importer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importer)
    with tempfile.TemporaryDirectory(prefix="lockbox-features-") as temporary:
        root = Path(temporary)
        home = root / "home"
        source = root / "source"
        workspace = root / "project"
        stage = root / "stage"
        config = home / ("." + provider)
        for directory in (home, source, workspace, config):
            directory.mkdir(parents=True)
        (workspace / "AGENTS.md").write_text("LOCKBOX_PROJECT_INSTRUCTION_FIXTURE\n")
        (workspace / "CLAUDE.md").write_text("@AGENTS.md\n")
        # Codex walks ancestor guidance within a repository boundary. This is
        # synthetic metadata only; no Git command or index write is performed.
        metadata = workspace / ".git"
        (metadata / "objects").mkdir(parents=True)
        (metadata / "refs/heads").mkdir(parents=True)
        (metadata / "HEAD").write_text("ref: refs/heads/fixture\n")
        (metadata / "config").write_text("[core]\nrepositoryformatversion = 0\nbare = false\n")
        nested = workspace / "nested"
        nested.mkdir()
        (nested / "AGENTS.md").write_text("LOCKBOX_NESTED_INSTRUCTION_FIXTURE\n")
        (source / "AGENTS.md").write_text("LOCKBOX_GLOBAL_INSTRUCTION_FIXTURE\n")
        skill = source / "skills/lockbox-shared-fixture"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: lockbox-shared-fixture\ndescription: Read-only local LockBox discovery fixture.\n---\nReturn fixture information.\n"
        )
        if provider == "claude":
            role = source / "claude/agents/lockbox-readonly-fixture.md"
            role.parent.mkdir(parents=True)
            role.write_text("---\nname: lockbox-readonly-fixture\ndescription: Read-only native discovery fixture.\nmodel: inherit\ntools: Read, Grep, Glob\n---\nInspect the requested fixture only. Do not delegate.\n")
            plugin = source / "claude/plugins/local-fixture"
            (plugin / ".claude-plugin").mkdir(parents=True)
            (plugin / ".claude-plugin/plugin.json").write_text(
                json.dumps(
                    {
                        "name": "lockbox-plugin-fixture",
                        "version": "1.0.0",
                        "description": "Local native plugin fixture",
                    }
                )
            )
            (plugin / "skills/plugin-skill").mkdir(parents=True)
            (plugin / "skills/plugin-skill/SKILL.md").write_text(
                "---\nname: plugin-skill\ndescription: Imported local plugin discovery fixture.\n---\nReturn fixture information.\n"
            )
        (source / "mcp.json").write_text(
            json.dumps(
                {
                    "lockbox-fixture": {
                        "command": sys.executable,
                        "args": [str(Path(__file__).resolve()), "--mcp-server"],
                    }
                }
            )
        )
        importer.stage(provider, stage, source, home)
        importer.prepare(provider, config, stage, False)
        if provider == "codex":
            (home / ".agents").mkdir()
            (home / ".agents/skills").symlink_to(
                config / "skills", target_is_directory=True
            )
        env = environment(home, provider)
        if provider == "claude":
            claude_features(home, nested, env)
        else:
            codex_features(home, nested, env)
    # Root ownership and modes, rather than writable host guard mounts.
    guard = Path("/usr/local/share/claude-guard/guard.mjs")
    settings = Path("/etc/claude-code/managed-settings.json")
    generator = guard.parent / "settings-generator.mjs"
    if not generator.is_file():
        generator = guard
    expected = json.loads(subprocess.check_output(["node", str(generator), "--managed-settings"]))
    require(
        expected == json.loads(settings.read_text()),
        "baked Claude managed policy matches the canonical guard",
    )
    require(
        all(
            path.stat().st_uid == 0
            and not path.stat().st_mode & 0o022
            and not os.access(path, os.W_OK)
            for file in (guard, settings)
            for path in (file, *file.parents)
        ),
        "dev cannot modify the baked guard or managed policy",
    )


if __name__ == "__main__":
    if sys.argv[1:] == ["--mcp-server"]:
        mcp_server()
    else:
        main()
