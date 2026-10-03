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
                "1",
            ],
            workspace,
            env,
        )
        records = [
            json.loads(line)
            for line in result.stdout.splitlines()
            if line.startswith("{")
        ]
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
        require(
            any(
                "lockbox-plugin-fixture:" in name and "plugin-skill" in name
                for name in initial.get("slash_commands", [])
            ),
            "Claude activates an imported local plugin through its native plugin-dir option",
        )
        wire = json.dumps(requests)
        require(
            "LOCKBOX_GLOBAL_INSTRUCTION_FIXTURE" in wire
            and "LOCKBOX_PROJECT_INSTRUCTION_FIXTURE" in wire,
            "Claude loads shared global guidance and project AGENTS.md",
        )
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

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            received.set()
            # Only prompt assembly is needed. End the local simulated request;
            # no model execution, external account or tool call is involved.
            data = b'{"error":{"message":"local instruction fixture complete","type":"invalid_request_error"}}'
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
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
        skills = rpc("skills/list", {"cwds": [str(workspace)], "forceReload": True})
        require(
            any(
                skill["name"] == "lockbox-shared-fixture" and skill["enabled"]
                for entry in skills["data"]
                for skill in entry["skills"]
            ),
            "Codex discovers and enables the imported shared skill",
        )
        thread = rpc("thread/start", {"cwd": str(workspace), "ephemeral": True})
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
        wire = json.dumps(requests)
        require(
            "LOCKBOX_GLOBAL_INSTRUCTION_FIXTURE" in wire
            and "LOCKBOX_PROJECT_INSTRUCTION_FIXTURE" in wire,
            "Codex loads shared global guidance and project AGENTS.md",
        )
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
        (source / "AGENTS.md").write_text("LOCKBOX_GLOBAL_INSTRUCTION_FIXTURE\n")
        skill = source / "skills/lockbox-shared-fixture"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: lockbox-shared-fixture\ndescription: Read-only local LockBox discovery fixture.\n---\nReturn fixture information.\n"
        )
        if provider == "claude":
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
            claude_features(home, workspace, env)
        else:
            codex_features(home, workspace, env)
    # Root ownership and modes, rather than writable host guard mounts.
    guard = Path("/usr/local/share/claude-guard/guard.mjs")
    settings = Path("/etc/claude-code/managed-settings.json")
    expected = json.loads(
        subprocess.check_output(["node", str(guard), "--managed-settings"])
    )
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
