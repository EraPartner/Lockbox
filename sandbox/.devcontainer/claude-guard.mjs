#!/usr/bin/env node
// PreToolUse(Bash) safety guard — a shellfirm-style gate for Claude Code.
//
// Reads the hook payload on stdin, inspects tool_input.command, and emits a
// PreToolUse decision:
//
//   * "deny" — catastrophic, never-legitimate commands (blocked outright).
//   * "ask"  — risky-but-sometimes-valid commands (forces a confirmation
//              prompt even if a broader allow rule would auto-approve).
//
// A non-matching command produces no output, so the normal permission flow
// continues untouched. Because this sees the whole command line (pipes, &&
// chains, flag ordering and all), it catches bypasses that prefix-based
// permissions.deny rules cannot — e.g. `curl … | sh` or `rm --force
// --recursive /`.
//
// Reviewed LockBox policy snapshot. Both images bake this root-owned guard.
// The VM, read-only mounts and firewall enforce isolation; this guard is a
// safety check for Claude shell commands. Host Claude configuration is unused.

// Recursive AND forced delete (flags in any order/spelling) aimed at a root,
// home, system-dir root, or the cwd/glob. Deep paths (rm -rf ./build,
// rm -rf ~/project, rm -rf /usr/local/x) are intentionally NOT matched.
// `(?<!git )` excludes the recoverable `git rm`. `[^|;&]*` (not `[^|]*`) keeps
// the rm flags and target inside ONE command, so `rm -rf ./build && cp x /etc`
// no longer trips on the cp's target. `["']?` after each anchor catches quoted
// forms (`rm -rf "/"`, `rm -rf "$HOME"`, `rm -rf '/etc'`) — the natural way a
// shell user writes these. Deep paths (rm -rf ./build, ~/project) stay unmatched.
const RM_CATASTROPHIC = String.raw`(?<!git )\brm\b(?=[^|;&]*(?:-\w*[rR]|--recursive))(?=[^|;&]*(?:-\w*f|--force))[^|;&]*(?:\s["']?/(?:\s|$|\*|["']|/)|\s["']?/(?:bin|boot|dev|etc|home|lib|lib64|opt|private|proc|root|run|sbin|srv|sys|tmp|usr|var|System|Library|Users|Applications|Volumes)(?:/?\s|/?$|/\*|/?["'])|\s["']?~(?:/?\s|/?$|/\*|/?["'])|["']?\$\{?HOME\}?(?:/?\s|/?$|/\*|/?["'])|\s["']?\*(?:\s|$)|\s["']?\.(?:\s|$))`;

// Reading a file that commonly holds credentials, via a content-dumping tool.
// Includes copy/encode/sort/cut/tr — all trivially exfiltrate a secret file.
const SECRET_READ = String.raw`\b(?:cat|bat|less|more|head|tail|nl|xxd|od|strings|hexdump|grep|egrep|fgrep|rg|ag|awk|gawk|sed|scp|rsync|cp|base64|openssl|sort|cut|tr)\b[^|;&]*?(?:\.env(?!\.(?:example|sample|template|dist|defaults)\b)\b|/\.ssh/|\bid_rsa\b|\bid_dsa\b|\bid_ecdsa\b|\bid_ed25519\b|\.pem\b|\.p12\b|\.pfx\b|\.key\b|/\.aws/|/\.gnupg/|\.netrc\b|/\.npmrc\b|\.git-credentials\b|credentials\.json\b|/\.claude(?:/|\.json\b))`;

// Ordered [pattern, decision, reason]. First match wins, so deny patterns that
// overlap an ask pattern (e.g. "dd of=/dev/" vs plain "dd") come first.
const RULES = [
  // ---- deny: catastrophic ----
  [RM_CATASTROPHIC, "deny", "recursive force-delete of a root, home, or system path"],
  [String.raw`:\s*\(\s*\)\s*\{[^}]*\|[^}]*&[^}]*\}\s*;\s*:`, "deny", "fork bomb"],
  [String.raw`\b(?:curl|wget|fetch)\b[^|]*\|\s*(?:sudo\s+(?:-\S+\s+)*)?(?:env\s+)?(?:sh|bash|zsh|fish|dash|python3?|perl|ruby|node)\b`,
    "deny", "piping a downloaded script straight into an interpreter"],
  [String.raw`\b(?:sh|bash|zsh|fish|dash|python3?|perl|ruby|node)\b[^|;&]*<\(\s*(?:curl|wget|fetch)\b`,
    "deny", "process-substituting a download into an interpreter"],
  [String.raw`\b(?:sh|bash|zsh|fish|dash|python3?|perl|ruby|node)\b[^|;&]*\$\(\s*(?:sudo\s+)?(?:curl|wget|fetch)\b`,
    "deny", "command-substituting a download into an interpreter"],
  [String.raw`\bdd\b[^|]*\bof=/dev/`, "deny", "dd writing directly to a block device"],
  [String.raw`\b(?:mkfs(?:\.\w+)?|fdisk(?!\s+-l\b)|parted(?!\s+(?:-l|--list)\b)|wipefs)\b`, "deny", "filesystem format / partition tool"],
  [String.raw`\bdiskutil\b\s+(?:eraseDisk|eraseVolume|reformat|partitionDisk|secureErase)\b`,
    "deny", "diskutil destructive disk operation"],
  [String.raw`(?:>>?|\btee\b\s+(?:-a\s+)?)\s*/dev/(?:disk|rdisk|sd|hd|nvme|mmcblk)`,
    "deny", "overwriting a raw block device"],
  [String.raw`\bchmod\b\s+(?:-R\s+)?0?777\s+/(?:\s|$)`, "deny", "world-writable chmod on /"],
  // ---- ask: risky, sometimes legitimate ----
  [String.raw`\bshred\b`, "ask", "secure-wipe (shred)"],
  [String.raw`\bdd\b`, "ask", "raw dd"],
  [String.raw`\b(?:chmod|chown)\b\s+-R\b[^|]*\s(?:/(?:\s|$)|/(?:bin|boot|dev|etc|home|lib|lib64|opt|private|proc|root|run|sbin|srv|sys|usr|var|System|Library|Users|Applications|Volumes)(?:/?\s|/?$)|~)`,
    "ask", "recursive chmod/chown on /, a system dir, or home"],
  [String.raw`\b(?:shutdown|reboot|halt|poweroff)\b`, "ask", "host power-state change"],
  [String.raw`\binit\b\s+[06]\b`, "ask", "host runlevel halt/reboot"],
  [SECRET_READ, "ask", "reading a file that may contain secrets"],
];

const COMPILED = RULES.map(([pattern, decision, reason]) => [new RegExp(pattern, "i"), decision, reason]);

// Return [decision, reason] for the first matching rule, or null.
function decide(command) {
  const normalized = command.replace(/\s+/g, " ").trim();
  for (const [regex, decision, reason] of COMPILED) {
    if (regex.test(normalized)) return [decision, reason];
  }
  return null;
}

async function readStdin() {
  let data = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) data += chunk;
  return data;
}

async function main() {
  let payload;
  try {
    payload = JSON.parse(await readStdin());
  } catch {
    process.exit(0); // malformed payload: stay out of the way
  }

  const command = (payload && payload.tool_input && payload.tool_input.command) || "";
  if (!command) process.exit(0);

  const verdict = decide(command);
  if (verdict === null) process.exit(0); // nothing matched: normal flow continues

  const [decision, reason] = verdict;
  const note = decision === "deny" ? "Blocked" : "Confirm needed";
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: decision,
      permissionDecisionReason: `${note} by local safety guard: ${reason}.`,
    },
  }));
  process.exit(0);
}

if (process.argv.includes("--managed-settings")) {
  process.stdout.write(JSON.stringify({
    sandbox: { enabled: false, autoAllowBashIfSandboxed: false },
    hooks: { PreToolUse: [{ matcher: "Bash", hooks: [{ type: "command",
      command: "node /usr/local/share/claude-guard/guard.mjs", timeout: 10,
      statusMessage: "Checking command safety" }] }] }
  }, null, 2) + "\n");
} else {
  main();
}

// ─── vendored by LockBox v0.1.0 · canonical sha256:76ad9cc11b0ca4b6713d8ec48fbba44ae914f3f8c75a2eb563846b99be2a56d5 ───
// Generated from the canonical source by LockBox/sync.sh — DO NOT EDIT HERE.
// Edit LockBox/claude-guard.mjs and re-run ./sync.sh.
