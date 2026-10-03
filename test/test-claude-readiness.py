#!/usr/bin/env python3
"""Exercise readiness failures without a VM, host configuration, or credentials."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]


class DoctorTests(unittest.TestCase):
    def test_provider_reachability_requires_real_http_status(self):
        source = (ROOT / ".devcontainer/bin/doctor").read_text()
        check = source.split("# Allowed provider egress through the proxy?\n", 1)[1]
        check = check.split("# Direct egress should be blocked.", 1)[0]
        for provider in ("claude", "codex"):
            for code, curl_rc, expected in (
                ("000", 7, 1),
                ("", 7, 1),
                ("000000", 7, 1),
                ("200", 0, 0),
                ("401", 0, 0),
                ("500", 0, 0),
            ):
                with self.subTest(provider=provider, code=code):
                    program = (
                        """
AGENT=$1
TEST_CODE=$2
TEST_CURL_RC=$3
failed=0
curl() { printf '%s' "$TEST_CODE"; return "$TEST_CURL_RC"; }
pass() { :; }
fail() { failed=1; }
"""
                        + check
                        + '\nexit "$failed"\n'
                    )
                    result = subprocess.run(
                        [
                            "bash",
                            "-c",
                            program,
                            "doctor-test",
                            provider,
                            code,
                            str(curl_rc),
                        ],
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, expected, result.stderr)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / "synthetic-home"
        self.target = self.root / "target"
        self.target.mkdir()
        self.home.mkdir()
        self.calls = self.root / "calls.jsonl"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.launchers = {}
        for kind, relative in (
            ("lockbox", ".devcontainer/bin/agent"),
            ("generic", "sandbox/.devcontainer/bin/dev"),
        ):
            launcher = self.root / relative
            launcher.parent.mkdir(parents=True, exist_ok=True)
            # Rebind only host path references in the copied fixture. HOME itself
            # is never overridden, and no fixture writes to the user's home.
            launcher.write_text(
                (ROOT / relative).read_text().replace("$HOME", "$LOCKBOX_TEST_HOME")
            )
            self.launchers[kind] = launcher
        (self.root / "scripts").mkdir()
        (self.root / ".devcontainer/agent-config.py").write_text(
            (ROOT / ".devcontainer/agent-config.py")
            .read_text()
            .replace(
                "def stage(provider, destination, source, home, import_native=False):",
                """def stage(provider, destination, source, home, import_native=False):
    with open(os.environ["LOCKBOX_TEST_CALLS"], "a") as stream:
        stream.write(json.dumps({"action": "host-stage"}) + "\\n")
    if os.environ["LOCKBOX_TEST_GATE"] == "stage":
        raise ValueError("synthetic stage failure")""",
            )
        )
        (self.root / ".devcontainer/agent-config-launcher.sh").write_text(
            (ROOT / ".devcontainer/agent-config-launcher.sh").read_text()
        )
        (self.root / ".devcontainer/launch-lock.py").write_text(
            (ROOT / ".devcontainer/launch-lock.py").read_text()
        )
        library = (
            (ROOT / "launcher-common.sh").read_text()
            + """
sandbox_git_ro_mounts() { GIT_RO_MOUNTS=(); }
sandbox_devcontainer_ro_mounts() { DEVCONTAINER_RO_MOUNTS=(); }
sandbox_ro_mount_policy_id() { RO_MOUNT_POLICY_ID=fixture-ro-policy; }
sandbox_warn_stale_allowlist() { :; }
# Cache verification has its own tests. Keep actual native-login and stop-trap
# functions; replace only the container cache probe with a bounded fixture.
sandbox_verify_pins_cached() {
  container test-pin-verification "$1"
}
"""
        )
        for dc in (self.root / ".devcontainer", self.root / "sandbox/.devcontainer"):
            (dc / "launcher-common.sh").write_text(library)
            (dc / "allowlist.txt").write_text("synthetic.example\n")
        self.write(self.home / ".gitconfig", "# synthetic mount source\n")
        self.write(
            self.root / ".devcontainer/claude-guard.mjs",
            (ROOT / ".devcontainer/claude-guard.mjs").read_text(),
        )
        self.project = self.root / "scoped-project"
        shutil.copytree(self.root / ".devcontainer", self.project / ".devcontainer")

        self.executable(
            "container",
            "#!"
            + sys.executable
            + "\n"
            + r"""
import json, os, sys, hashlib, subprocess
from pathlib import Path
args = sys.argv[1:]
gate = os.environ["LOCKBOX_TEST_GATE"]
provider = os.environ["LOCKBOX_TEST_PROVIDER"]
name = os.environ["LOCKBOX_TEST_NAME"]
runtime = Path(os.environ["LOCKBOX_TEST_RUNTIME"])
state = json.loads(runtime.read_text())
def save():
    runtime.write_text(json.dumps(state))
def record(action, **extra):
    with open(os.environ["LOCKBOX_TEST_CALLS"], "a") as stream:
        stream.write(json.dumps({"action": action, "argv": args, **extra}) + "\n")
def finish(action, rc=0, output=None, **extra):
    if action in ("stop", "start"):
        allowlist = Path(os.environ["LOCKBOX_TEST_STAGE"]) / "allowlist.txt"
        extra["allowlist"] = allowlist.read_text() if allowlist.exists() else None
    record(action, **extra)
    if output is not None:
        print(output)
    sys.exit(rc)
command = args[0]
if command in ("start", "run"):
    if gate == command:
        finish(command, 27)
    state.update(status="running", generation=state["generation"] + 1)
    save()
    finish(command)
if command == "stop":
    if gate == "stop":
        finish(command, 26)
    state["status"] = "stopped"
    save()
    finish(command)
if command == "rm":
    state["status"] = "missing"
    save()
    finish(command)
if command in ("system", "build", "builder", "image"):
    finish(command)
if command == "test-pin-verification":
    finish("pins", 19 if gate == "pins" else 0)
if command == "ls":
    if gate == "list" or (gate == "stop-list" and state["status"] == "stopped"):
        finish("ls", 30)
    finish("ls", output=name if state["status"] == "running" or (state["status"] == "stopped" and "-aq" in args) else None)
if command != "exec":
    finish("unexpected", 98)
last = args[-1]
if "sha256sum" in args:
    if args[-1] == "/etc/squid/allowlist.txt":
        mounted = Path(os.environ["LOCKBOX_TEST_STAGE"]) / "allowlist.txt"
        finish("mounted-policy", output="incorrect" if gate == "mounted-policy" else hashlib.sha256(mounted.read_bytes()).hexdigest())
    expected = Path(os.environ["LOCKBOX_TEST_HOME"]).parent / ".devcontainer/claude-guard.mjs"
    finish("baked-policy", output="stale" if gate == "baked-policy" else hashlib.sha256(expected.read_bytes()).hexdigest())
if "--managed-settings" in args:
    finish("managed-settings", output="{}")
if "python3" in args and "-c" in args:
    # The real managed-policy validator reads piped JSON. Drain the fixture's
    # input too, so its producer cannot race an early close and fail with SIGPIPE.
    if "-i" in args:
        sys.stdin.read()
    finish("managed-policy", 21 if gate == "managed-policy" else 0)
if last == "SANDBOX_AGENT":
    finish("provider", output="stale" if gate == "provider" else provider)
if last == "SANDBOX_LAUNCH_POLICY":
    finish("policy", output="stale" if gate == "policy" else "fixture-ro-policy:agent-config-v3" + (":input-v1" if name.startswith("dev-sandbox") else ""))
if last in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
    finish("layout", output="/wrong" if gate == "layout" else "/home/dev/." + provider)
if "/opt/python/bin/python3" in args:
    if "-c" in args:
        finish("generation", 29 if gate == "generation" else 0,
               output="00000000-0000-0000-0000-000000000001:" + str(state["generation"]))
    mode = args[args.index("/opt/python/bin/python3") + 2]
    finish("config-" + mode, 20 if gate == mode else 0,
           output=os.environ["LOCKBOX_TEST_CONFIG_STATE"] if mode == "inspect" else
                  os.environ["LOCKBOX_TEST_MCP_NAMES"] if mode == "environment" else None)
if args[-3:] in (["claude", "auth", "status"], ["codex", "login", "status"]):
    finish("login-status", 0 if os.environ["LOCKBOX_TEST_CACHED_LOGIN"] == "1" else 1, startup_locked="LOCKBOX_STARTUP_LOCK" in os.environ)
if "codex login --with-" in last:
    finish("login-bootstrap", 24 if gate == "bootstrap" else 0)
if args[-3:] == ["claude", "auth", "login"] or last == "--device-auth":
    finish("interactive-login", 99)
if "post-create.sh" in last:
    finish("lifecycle", 17 if gate == "lifecycle" else 0)
if args[-4:] == ["pkill", "-HUP", "-x", "squid"]:
    finish("proxy-reload", 18 if gate == "reload" else 0)
if "/dev/tcp/127.0.0.1/3128" in last:
    # Execute the actual bounded loop. Replace only its network probe and sleep
    # so both the success path and all 150 failed attempts run without a VM.
    program = '''counter=0
probe() { counter=$((counter + 1)); [[ "$LOCKBOX_TEST_GATE" != proxy ]]; }
trap 'printf "__LOCKBOX_PROBES=%s\\n" "$counter" >&2' EXIT
''' + last.replace("(exec 3<>/dev/tcp/127.0.0.1/3128)", "probe").replace("sleep 0.2", ":")
    result = subprocess.run(["bash", "-c", program], text=True, capture_output=True)
    probes = int(result.stderr.rsplit("__LOCKBOX_PROBES=", 1)[1].strip())
    sys.stderr.write(result.stderr.split("__LOCKBOX_PROBES=", 1)[0])
    finish("proxy", result.returncode, probes=probes)
if name in args:
    index = args.index(name)
    session = args[index + 1:]
    if session and session[0] in (provider, "bash"):
        payload = sys.stdin.read() if "-i" in args[:index] else ""
        finish("shell-session" if session[0] == "bash" else "agent-session",
               int(os.environ["LOCKBOX_TEST_AGENT_RC"]), payload=payload)
finish("unexpected", 98)
""",
        )
        self.executable(
            "python3",
            "#!"
            + sys.executable
            + "\n"
            + r"""
import json, os, sys
if sys.argv[1:3] == ["-c", "import tomllib"]:
    sys.exit(1 if os.environ["LOCKBOX_TEST_GATE"] == "host-python" else 0)
os.execv(sys.executable, [sys.executable] + sys.argv[1:])
""",
        )
        # Every candidate remains synthetic, including the fallback from an old
        # python3. The production stage program is never run with real HOME.
        for name in ("python3.14", "python3.13", "python3.12", "python3.11"):
            (self.bin / name).symlink_to(self.bin / "python3")
        self.executable(
            "git", "#!/usr/bin/env bash\nprintf '%s\\n' \"$LOCKBOX_TEST_TARGET\"\n"
        )
        self.executable(
            "fish",
            "#!"
            + sys.executable
            + "\n"
            + r"""
import json, os
with open(os.environ["LOCKBOX_TEST_CALLS"], "a") as stream:
    stream.write(json.dumps({"action": "host-export"}) + "\n")
""",
        )

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def executable(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def launch(
        self,
        kind="lockbox",
        provider="claude",
        gate="success",
        fresh="1",
        stop="1",
        agent_rc="0",
        state="ready",
        refresh="0",
        cached=True,
        auth=None,
        shell=False,
        payload="",
        mcp_names="",
        mcp_set=False,
        confirmed=True,
        preserve_runtime=False,
        confirmation=None,
        existing_allowlist="synthetic.example\n",
    ):
        self.calls.write_text("")
        # Keep unrelated host environment values out of the auth tests. Each
        # credential below is an inert fixture value, never a host lookup.
        excluded = {
            "CLAUDE_CODE_OAUTH_TOKEN",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "OPENAI_API_KEY",
            "CODEX_ACCESS_TOKEN",
            "BASH_ENV",
            "ENV",
            "LOCKBOX_CONFIG_REFRESH",
            "DEV_SANDBOX_CONFIG_REFRESH",
            "LOCKBOX_CLAUDE_CONFIG_REFRESH",
            "DEV_SANDBOX_CLAUDE_CONFIG_REFRESH",
            "DEV_SANDBOX_SHELL",
            "DEV_SANDBOX_PORTS",
            "LOCKBOX_REBUILD",
            "DEV_SANDBOX_REBUILD",
        }
        env = {key: value for key, value in os.environ.items() if key not in excluded}
        digest = hashlib.sha1(str(self.target).encode()).hexdigest()[:12]
        name = (
            ("lockbox-dev" if provider == "claude" else "lockbox-codex")
            if kind == "lockbox"
            else ("dev-sandbox-" if provider == "claude" else "dev-sandbox-codex-")
            + digest
        )
        stage = (
            self.home
            / ".claude-sandbox/stage"
            / ("lockbox-" + provider if kind == "lockbox" else name)
        )
        if state == "ready":
            config_stage = stage / "agent-input" if kind == "generic" else stage
            self.write(
                config_stage / "config/manifest.json",
                json.dumps({"version": 3, "provider": provider}),
            )
        runtime = self.root / (name + ".runtime.json")
        if not preserve_runtime or not runtime.exists():
            runtime.write_text(
                json.dumps(
                    {
                        "status": {
                            "0": "running",
                            "1": "missing",
                            "stopped": "stopped",
                        }[fresh],
                        "generation": 1,
                    }
                )
            )
        if kind == "generic" and fresh == "0" and not preserve_runtime:
            stage.mkdir(parents=True, exist_ok=True)
            allowlist = stage / "allowlist.txt"
            allowlist.write_text(existing_allowlist)
            applied = stage / "allowlist.applied"
            if confirmed:
                applied.write_text(
                    confirmation
                    if confirmation is not None
                    else "lockbox-allowlist-v1 00000000-0000-0000-0000-000000000001:1 "
                    + hashlib.sha256(allowlist.read_bytes()).hexdigest()
                    + "\n"
                )
            else:
                applied.unlink(missing_ok=True)
        env.update(
            {
                "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                "LOCKBOX_PROJECT_ROOT": str(self.project),
                "LOCKBOX_AGENT": provider,
                "DEV_SANDBOX_AGENT": provider,
                "LOCKBOX_CONFIG_SOURCE": str(self.root / "authored"),
                "LOCKBOX_CONFIG_REFRESH": refresh,
                "DEV_SANDBOX_CONFIG_REFRESH": refresh,
                "LOCKBOX_STOP_ON_EXIT": stop,
                "DEV_SANDBOX_STOP_ON_EXIT": stop,
                "LOCKBOX_TEST_HOME": str(self.home),
                "LOCKBOX_TEST_TARGET": str(self.target),
                "LOCKBOX_TEST_CALLS": str(self.calls),
                "LOCKBOX_TEST_NAME": name,
                "LOCKBOX_TEST_PROVIDER": provider,
                "LOCKBOX_TEST_GATE": gate,
                "LOCKBOX_TEST_FRESH": fresh,
                "LOCKBOX_TEST_STAGE": str(stage),
                "LOCKBOX_TEST_RUNTIME": str(runtime),
                "LOCKBOX_TEST_AGENT_RC": agent_rc,
                "LOCKBOX_TEST_CONFIG_STATE": state,
                "LOCKBOX_TEST_CACHED_LOGIN": "1" if cached else "0",
                "LOCKBOX_TEST_MCP_NAMES": mcp_names,
            }
        )
        if auth:
            env[auth] = "synthetic-auth"
        env.pop("SYNTHETIC_MCP_REFERENCE", None)
        if mcp_set:
            env["SYNTHETIC_MCP_REFERENCE"] = "synthetic-private-mcp-value"
        if shell:
            env["DEV_SANDBOX_SHELL"] = "1"
        result = subprocess.run(
            ["bash", str(self.launchers[kind]), "--version"],
            cwd=self.target,
            env=env,
            input=payload,
            capture_output=True,
            text=True,
        )
        records = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertNotIn(
            "unexpected", [record["action"] for record in records], result.stderr
        )
        return result, records

    def test_startup_failures_stop_both_provider_containers(self):
        for kind in ("lockbox", "generic"):
            for provider in ("claude", "codex"):
                for gate in (
                    "provider",
                    "policy",
                    "layout",
                    "lifecycle",
                    "inspect",
                    "init",
                    "report",
                    "environment",
                    "pins",
                ):
                    for fresh in ("0", "1"):
                        with self.subTest(
                            kind=kind, provider=provider, gate=gate, fresh=fresh
                        ):
                            result, records = self.launch(kind, provider, gate, fresh)
                            actions = [r["action"] for r in records]
                            self.assertNotEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(actions.count("stop"), 1)
                            self.assertNotIn("agent-session", actions)
                            self.assertNotIn("host-export", actions)

    def test_stale_baked_claude_policy_stops_before_configuration_or_login(self):
        for kind in ("lockbox", "generic"):
            for gate in ("baked-policy", "managed-policy"):
                with self.subTest(kind=kind, gate=gate):
                    result, records = self.launch(kind, "claude", gate=gate)
                    actions = [record["action"] for record in records]
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(actions.count("stop"), 1)
                    self.assertNotIn("host-stage", actions)
                    self.assertNotIn("login-status", actions)
                    self.assertNotIn("agent-session", actions)

    def test_failed_proxy_readiness_aborts_before_configuration_or_login(self):
        for kind in ("generic", "lockbox"):
            for provider in ("claude", "codex"):
                for stop in ("0", "1"):
                    with self.subTest(kind=kind, provider=provider, stop=stop):
                        result, records = self.launch(
                            kind, provider, gate="proxy", stop=stop
                        )
                        actions = [record["action"] for record in records]
                        self.assertNotEqual(result.returncode, 0)
                        probe = next(
                            record for record in records if record["action"] == "proxy"
                        )
                        self.assertEqual(probe["probes"], 150)
                        self.assertIn("/bin/bash", probe["argv"])
                        self.assertIn("--noprofile", probe["argv"])
                        self.assertIn("--norc", probe["argv"])
                        self.assertNotIn("-lc", probe["argv"])
                        self.assertNotIn("lifecycle", actions)
                        self.assertNotIn("login-status", actions)
                        self.assertNotIn("agent-session", actions)

    def test_staging_workspace_overlap_is_refused_for_both_providers(self):
        for provider in ("claude", "codex"):
            for kind in ("generic", "lockbox"):
                with self.subTest(provider=provider, kind=kind):
                    target = self.home / ".claude-sandbox/projects/example"
                    target.mkdir(parents=True, exist_ok=True)
                    self.target = target
                    # Scoped launcher uses its own project root, so relocate its
                    # copied tree into the target without changing host paths.
                    if kind == "lockbox":
                        tree = target / ".devcontainer"
                        shutil.copytree(
                            self.root / ".devcontainer", tree, dirs_exist_ok=True
                        )
                        self.launchers[kind] = tree / "bin/agent"
                        self.launchers[kind].write_text(
                            self.launchers[kind]
                            .read_text()
                            .replace(
                                'PROJECT_ROOT="$LOCKBOX_PROJECT_ROOT"',
                                'PROJECT_ROOT="$LOCKBOX_TEST_TARGET"',
                            )
                        )
                    result, records = self.launch(kind, provider)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("overlaps private host staging", result.stderr)
                    self.assertFalse(
                        any(
                            r["action"]
                            in ("run", "start", "host-stage", "agent-session")
                            for r in records
                        )
                    )

    def test_unconfirmed_warm_policy_restart_and_retry_fail_closed(self):
        for provider in ("claude", "codex"):
            for gate in ("stop", "start", "proxy", "mounted-policy", "stop-list"):
                with self.subTest(provider=provider, gate=gate):
                    result, records = self.launch(
                        "generic",
                        provider,
                        gate=gate,
                        fresh="0",
                        stop="0",
                        confirmed=False,
                    )
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    actions = [record["action"] for record in records]
                    self.assertIn("stop", actions)
                    self.assertNotIn("login-status", actions)
                    self.assertNotIn("agent-session", actions)
                    # Identical retry must repeat the transition, never treat
                    # staged bytes from a failed attempt as confirmed policy.
                    retry, repeated = self.launch(
                        "generic",
                        provider,
                        gate=gate,
                        fresh="0",
                        stop="0",
                        preserve_runtime=True,
                    )
                    self.assertNotEqual(retry.returncode, 0, retry.stderr)
                    self.assertNotIn("agent-session", [r["action"] for r in repeated])
                    if gate not in ("start", "stop-list"):
                        self.assertIn("stop", [r["action"] for r in repeated])

    def test_warm_confirmed_policy_reuses_same_boot(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                result, records = self.launch("generic", provider, fresh="0", stop="0")
                self.assertEqual(result.returncode, 0, result.stderr)
                actions = [r["action"] for r in records]
                self.assertNotIn("stop", actions)
                self.assertNotIn("start", actions)
                self.assertNotIn("run", actions)
                probe = next(r for r in records if r["action"] == "proxy")
                self.assertEqual(probe["probes"], 1)

    def test_invalid_confirmation_requires_fresh_boot(self):
        for provider in ("claude", "codex"):
            for confirmation in (
                "broken\n",
                "lockbox-allowlist-v1 stale-boot stale-hash\n",
            ):
                with self.subTest(provider=provider, confirmation=confirmation):
                    result, records = self.launch(
                        "generic",
                        provider,
                        fresh="0",
                        stop="0",
                        confirmation=confirmation,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    actions = [r["action"] for r in records]
                    self.assertEqual(actions.count("stop"), 1)
                    self.assertEqual(actions.count("start"), 1)
                    self.assertLess(actions.index("stop"), actions.index("start"))
                    self.assertLess(
                        actions.index("proxy"), actions.index("login-status")
                    )

    def test_headless_overlay_narrowing_stops_before_mounted_bytes_change(self):
        self.write(self.target / ".sandbox-allowlist.txt", "retired-overlay.example\n")
        old = "synthetic.example\nretired-overlay.example\n"
        for provider in ("claude", "codex"):
            for gate in ("success", "stop"):
                with self.subTest(provider=provider, gate=gate):
                    result, records = self.launch(
                        "generic",
                        provider,
                        gate=gate,
                        fresh="0",
                        stop="0",
                        existing_allowlist=old,
                    )
                    self.assertIn("IGNORING the overlay", result.stderr)
                    stopped = next(r for r in records if r["action"] == "stop")
                    self.assertEqual(stopped["allowlist"], old)
                    actions = [r["action"] for r in records]
                    if gate == "stop":
                        self.assertNotEqual(result.returncode, 0)
                        self.assertNotIn("start", actions)
                        self.assertNotIn("agent-session", actions)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        started = next(r for r in records if r["action"] == "start")
                        self.assertEqual(started["allowlist"], "synthetic.example\n")

    def test_startup_lock_is_released_before_native_login(self):
        for kind in ("lockbox", "generic"):
            for provider in ("claude", "codex"):
                with self.subTest(kind=kind, provider=provider):
                    result, records = self.launch(kind, provider)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    login = next(r for r in records if r["action"] == "login-status")
                    self.assertFalse(login["startup_locked"])

    def test_keep_running_policy_is_respected_on_failure(self):
        for kind in ("lockbox", "generic"):
            for provider in ("claude", "codex"):
                with self.subTest(kind=kind, provider=provider):
                    result, records = self.launch(kind, provider, gate="pins", stop="0")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("stop", [r["action"] for r in records])

    def test_success_preserves_exit_status_and_orders_readiness_before_login(self):
        for kind in ("lockbox", "generic"):
            for provider in ("claude", "codex"):
                with self.subTest(kind=kind, provider=provider):
                    result, records = self.launch(kind, provider, agent_rc="23")
                    actions = [r["action"] for r in records]
                    self.assertEqual(result.returncode, 23, result.stderr)
                    self.assertEqual(actions.count("stop"), 1)
                    self.assertLess(
                        actions.index("lifecycle"), actions.index("config-init")
                    )
                    self.assertLess(
                        actions.index("config-report"), actions.index("pins")
                    )
                    self.assertLess(
                        actions.index("pins"), actions.index("login-status")
                    )
                    self.assertLess(
                        actions.index("login-status"), actions.index("agent-session")
                    )
                    self.assertNotIn("host-export", actions)

    def test_initial_import_and_explicit_refresh_stage_only_when_needed(self):
        for kind in ("lockbox", "generic"):
            for provider in ("claude", "codex"):
                for state, refresh, expected in (
                    ("ready", "0", False),
                    ("import", "0", True),
                    ("ready", "1", True),
                ):
                    with self.subTest(
                        kind=kind, provider=provider, state=state, refresh=refresh
                    ):
                        result, records = self.launch(
                            kind, provider, state=state, refresh=refresh
                        )
                        actions = [r["action"] for r in records]
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual("host-stage" in actions, expected)
                        self.assertIn(
                            "config-refresh" if refresh == "1" else "config-init",
                            actions,
                        )
                        if expected:
                            self.assertLess(
                                actions.index("lifecycle"), actions.index("host-stage")
                            )

    def test_import_errors_stop_and_do_not_open_agent(self):
        for kind in ("lockbox", "generic"):
            for provider in ("claude", "codex"):
                for gate in ("host-python", "stage", "refresh"):
                    with self.subTest(kind=kind, provider=provider, gate=gate):
                        result, records = self.launch(
                            kind, provider, gate=gate, state="import", refresh="1"
                        )
                        actions = [r["action"] for r in records]
                        self.assertNotEqual(result.returncode, 0, result.stderr)
                        self.assertEqual("stop" in actions, gate != "host-python")
                        self.assertNotIn("agent-session", actions)

    def test_noninteractive_native_login_requires_cache_or_explicit_environment(self):
        for kind in ("lockbox", "generic"):
            for provider in ("claude", "codex"):
                with self.subTest(kind=kind, provider=provider, auth="absent"):
                    result, records = self.launch(kind, provider, cached=False)
                    actions = [r["action"] for r in records]
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertIn("stop", actions)
                    self.assertNotIn("agent-session", actions)
                    self.assertNotIn("interactive-login", actions)
                variables = (
                    ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
                    if provider == "claude"
                    else ("OPENAI_API_KEY", "CODEX_ACCESS_TOKEN")
                )
                for variable in variables:
                    with self.subTest(kind=kind, provider=provider, auth=variable):
                        result, records = self.launch(
                            kind, provider, cached=False, auth=variable
                        )
                        actions = [r["action"] for r in records]
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertIn("agent-session", actions)
                        self.assertEqual(
                            "login-bootstrap" in actions, provider == "codex"
                        )
                        self.assertNotIn(
                            "synthetic-auth",
                            result.stdout + result.stderr + self.calls.read_text(),
                        )

    def test_failed_codex_environment_bootstrap_stops_without_agent(self):
        for kind in ("lockbox", "generic"):
            with self.subTest(kind=kind):
                result, records = self.launch(
                    kind, "codex", gate="bootstrap", cached=False, auth="OPENAI_API_KEY"
                )
                actions = [r["action"] for r in records]
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("stop", actions)
                self.assertNotIn("agent-session", actions)

    def test_cached_native_login_wins_over_optional_environment_auth(self):
        for kind in ("lockbox", "generic"):
            for provider in ("claude", "codex"):
                variable = (
                    "ANTHROPIC_API_KEY" if provider == "claude" else "OPENAI_API_KEY"
                )
                with self.subTest(kind=kind, provider=provider):
                    result, records = self.launch(
                        kind, provider, cached=True, auth=variable
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    actions = [r["action"] for r in records]
                    self.assertNotIn("login-bootstrap", actions)
                    session = next(r for r in records if r["action"] == "agent-session")
                    self.assertNotIn(variable, session["argv"])

    def test_piped_input_reaches_both_providers_without_pseudo_terminal(self):
        payload = "Synthetic piped prompt\nwith another line\n"
        for kind in ("lockbox", "generic"):
            for provider in ("claude", "codex"):
                with self.subTest(kind=kind, provider=provider):
                    result, records = self.launch(
                        kind, provider, state="import", payload=payload
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    session = next(r for r in records if r["action"] == "agent-session")
                    self.assertEqual(session["payload"], payload)
                    self.assertIn("-i", session["argv"])
                    self.assertNotIn("-t", session["argv"])

    def test_generic_shell_does_not_require_provider_login(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                result, records = self.launch(
                    "generic", provider, cached=False, shell=True
                )
                actions = [r["action"] for r in records]
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("shell-session", actions)
                self.assertIn("pins", actions)
                self.assertNotIn("login-status", actions)
                self.assertNotIn("interactive-login", actions)
                self.assertIn("stop", actions)

    def test_safe_chain_startup_is_scoped_to_dev_not_root_creation(self):
        for kind in ("lockbox", "generic"):
            for provider in ("claude", "codex"):
                with self.subTest(kind=kind, provider=provider):
                    result, records = self.launch(kind, provider, fresh="1")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    startup = next(r for r in records if r["action"] == "run")
                    self.assertFalse(any("BASH_ENV=" in arg for arg in startup["argv"]))
                    session = next(r for r in records if r["action"] == "agent-session")
                    self.assertIn(
                        "BASH_ENV=/home/dev/.safe-chain/scripts/init-posix.sh",
                        session["argv"],
                    )
                    self.assertIn("--user", session["argv"])
                    self.assertIn("dev", session["argv"])

    def test_mcp_environment_forwarding_uses_names_only_and_warns_when_unset(self):
        for kind in ("lockbox", "generic"):
            for provider in ("claude", "codex"):
                for present in (False, True):
                    with self.subTest(kind=kind, provider=provider, present=present):
                        result, records = self.launch(
                            kind,
                            provider,
                            mcp_names="SYNTHETIC_MCP_REFERENCE",
                            mcp_set=present,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        session = next(
                            r for r in records if r["action"] == "agent-session"
                        )
                        self.assertEqual(
                            "SYNTHETIC_MCP_REFERENCE" in session["argv"], present
                        )
                        self.assertEqual(
                            "SYNTHETIC_MCP_REFERENCE is unset" in result.stderr,
                            not present,
                        )
                        self.assertNotIn(
                            "synthetic-private-mcp-value",
                            result.stdout + result.stderr + self.calls.read_text(),
                        )


if __name__ == "__main__":
    unittest.main()
