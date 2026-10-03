#!/usr/bin/env node
// PreToolUse(Bash) safety guard for Codex.
//
// Reads the hook payload on stdin, inspects tool_input.command, and emits a
// PreToolUse decision:
//
//   * "deny" — catastrophic, never-legitimate commands (blocked outright).
//   * "deny" — risky commands that cannot safely use a hook-driven prompt.
//
// A non-matching command produces no output, so the normal permission flow
// continues untouched. Because this sees the whole command line (pipes, &&
// chains, flag ordering and all), it catches bypasses that prefix-based
// permissions.deny rules cannot — e.g. `curl … | sh` or `rm --force
// --recursive /`.
//
// Codex does not support permissionDecision="ask" in PreToolUse. It reports the
// hook as failed and continues the call. Risky cases therefore fail closed as
// denials; the user can perform an explicitly approved operation outside Codex.

import path from "node:path";

// Recursive rm is not reliably safe to infer from a shell string. Deny it at
// this deterministic boundary regardless of target. A user can perform an
// intentional recursive deletion outside Codex. `git rm` remains recoverable
// and is handled separately in hasRecursiveRm().
const RM_RECURSIVE = /\brm\b[^|;&\n]*(?:--recursive\b|-\w*[rR]\w*)/i;

// Reading a file that commonly holds credentials, via a content-dumping tool.
// Includes copy/encode/sort/cut/tr — all trivially exfiltrate a secret file.
const PATH_START = String.raw`(?:^|[\s"'=]|/)`;
// Only authored Claude configuration is readable. Unknown files, login state,
// directory-wide reads, globs, and traversal stay denied. This is a command
// heuristic; config.toml supplies the filesystem boundary, including symlinks.
const PATH_END = String.raw`(?=$|[\s"'\x29;,|&])`;
const CLAUDE_COMPONENT = String.raw`(?!\.{1,2}(?:/|${PATH_END}))[\w.-]+`;
const CLAUDE_AUTHORED = String.raw`(?:CLAUDE\.md|settings\.json|keybindings\.json|(?:hooks|agents|skills|commands|rules)(?:/${CLAUDE_COMPONENT})*/?)${PATH_END}`;
const CLAUDE_PRIVATE = String.raw`${PATH_START}\.claude(?:\.json\b|/(?!${CLAUDE_AUTHORED})|${PATH_END})`;
const SECRET_PATH = String.raw`(?:\.env(?!\.(?:example|sample|template|dist|defaults)\b)\b|${PATH_START}\.ssh/|\bid_rsa\b|\bid_dsa\b|\bid_ecdsa\b|\bid_ed25519\b|\.pem\b|\.p12\b|\.pfx\b|\.key\b|${PATH_START}\.aws/|${PATH_START}\.gnupg/|\.netrc\b|${PATH_START}\.npmrc\b|\.git-credentials\b|(?:credentials|secrets|auth|tokens?)\.(?:json|toml)\b|settings\.local\.json\b|permission-watch-state(?:/|\b)|${CLAUDE_PRIVATE}|${PATH_START}\.config/gh/hosts\.yml\b|${PATH_START}\.docker/config\.json\b|${PATH_START}\.hermes/auth\.json\b)`;
const SECRET_READ = String.raw`\b(?:cat|bat|less|more|head|tail|nl|xxd|od|strings|hexdump|grep|egrep|fgrep|rg|ag|awk|gawk|sed|scp|rsync|cp|base64|openssl|sort|cut|tr)\b[^|;&]*?${SECRET_PATH}`;
const INTERPRETER_SECRET_READ = String.raw`\b(?:python3?|node|ruby|perl|php)\b[^|;&]*?${SECRET_PATH}`;
const DOWNLOAD_COMMANDS = new Set(["curl", "wget", "fetch"]);
const INTERPRETERS = new Set([
  "sh", "bash", "zsh", "fish", "dash", "python", "python2", "python3",
  "perl", "ruby", "node",
]);
const SUDO_OPTIONS_WITH_ARGUMENT = new Set([
  "-C", "-D", "-g", "-h", "-p", "-R", "-T", "-u", "-U",
  "--chdir", "--close-from", "--group", "--host", "--prompt", "--role",
  "--type", "--user", "--other-user",
]);
const ENV_OPTIONS_WITH_ARGUMENT = new Set([
  "-C", "-S", "-u", "--argv0", "--chdir", "--split-string", "--unset",
]);
const EXEC_OPTIONS_WITH_ARGUMENT = new Set(["-a"]);

// Ordered [pattern, decision, reason]. First match wins.
const RULES = [
  // ---- deny: catastrophic ----
  [String.raw`:\s*\(\s*\)\s*\{[^}]*\|[^}]*&[^}]*\}\s*;\s*:`, "deny", "fork bomb"],
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
  // ---- deny: risky operations that require an out-of-band user action ----
  [String.raw`\bshred\b`, "deny", "secure-wipe (shred)"],
  [String.raw`\bdd\b`, "deny", "raw dd"],
  [String.raw`\b(?:chmod|chown)\b\s+-R\b[^|]*\s(?:/(?:\s|$)|/(?:bin|boot|dev|etc|home|lib|lib64|opt|private|proc|root|run|sbin|srv|sys|usr|var|System|Library|Users|Applications|Volumes)(?:/?\s|/?$)|~)`,
    "deny", "recursive chmod/chown on /, a system dir, or home"],
  [String.raw`\b(?:shutdown|reboot|halt|poweroff)\b`, "deny", "host power-state change"],
  [String.raw`\binit\b\s+[06]\b`, "deny", "host runlevel halt/reboot"],
  [SECRET_READ, "deny", "reading a file that may contain secrets"],
  [INTERPRETER_SECRET_READ, "deny", "reading a secret file through an interpreter"],
  // Keychain services can return credentials without opening a denied file.
  [String.raw`\bsecurity\b[^|;&]*\b(?:find-generic-password|find-internet-password|dump-keychain|export)\b`,
    "deny", "extracting credentials from macOS Keychain"],
];

const COMPILED = RULES.map(([pattern, decision, reason]) => [new RegExp(pattern, "i"), decision, reason]);

// Exact informational invocations are harmless. Match the whole command so a
// shell operator, redirection, or any real dd operand still reaches the raw-dd
// deny rule below.
const DD_INFO_ONLY = /^(?:\/(?:usr\/)?bin\/)?dd\s+(?:--help|--version)$/i;

function commandBasename(token) {
  const unquoted = token
    .replace(/^["']|["']$/g, "")
    .replace(/^[({]+/, "")
    .replace(/[)};]+$/, "");
  return unquoted.slice(unquoted.lastIndexOf("/") + 1);
}

function pipelineCommandTokens(stage) {
  const tokens = stage.trim().split(/\s+/).filter(Boolean);
  let index = 0;
  while (index < tokens.length) {
    const token = tokens[index];
    const command = commandBasename(token);
    if (command === "") {
      index += 1;
      continue;
    }
    if (/^[A-Za-z_]\w*=/.test(token)) {
      index += 1;
      continue;
    }
    if (command === "sudo") {
      index += 1;
      while (index < tokens.length && tokens[index].startsWith("-")) {
        const option = tokens[index];
        index += 1;
        if (SUDO_OPTIONS_WITH_ARGUMENT.has(option) && index < tokens.length) index += 1;
      }
      continue;
    }
    if (command === "env") {
      index += 1;
      while (index < tokens.length) {
        const option = tokens[index];
        if (/^[A-Za-z_]\w*=/.test(option)) {
          index += 1;
          continue;
        }
        if (!option.startsWith("-")) break;
        index += 1;
        if (ENV_OPTIONS_WITH_ARGUMENT.has(option) && index < tokens.length) index += 1;
      }
      continue;
    }
    if (command === "command" || command === "exec") {
      index += 1;
      while (index < tokens.length && tokens[index].startsWith("-")) {
        const option = tokens[index];
        index += 1;
        if (command === "exec" && EXEC_OPTIONS_WITH_ARGUMENT.has(option) && index < tokens.length) index += 1;
      }
      continue;
    }
    return tokens.slice(index);
  }
  return [];
}

function pipelineCommand(stage) {
  return commandBasename(pipelineCommandTokens(stage)[0] || "");
}

function hasDownloadInterpreterPipeline(command) {
  return command.split(/&&|\|\||[;\n]/).some((listSegment) => {
    const stages = listSegment.split("|");
    if (stages.length < 2) return false;
    let downloadSeen = false;
    return stages.some((stage) => {
      const executable = pipelineCommand(stage);
      if (downloadSeen && INTERPRETERS.has(executable)) return true;
      if (DOWNLOAD_COMMANDS.has(executable)) downloadSeen = true;
      return false;
    });
  });
}

function isGitRm(segment) {
  if (/[$`\\(){}]/.test(segment)) return false;
  const tokens = pipelineCommandTokens(segment);
  if (commandBasename(tokens[0] || "") !== "git") return false;
  let index = 1;
  // Recognize Git's global options, not arbitrary words that happen to precede
  // rm. In particular, an assignment such as git=ignored is not a git command.
  while (index < tokens.length) {
    const token = tokens[index];
    if (["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"].includes(token)) {
      index += 2;
    } else if (/^--(?:git-dir|work-tree|namespace|config-env)=/.test(token)
        || /^-[Cc].+/.test(token)
        || ["--no-pager", "--paginate", "--bare", "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs", "--icase-pathspecs", "--no-optional-locks", "--no-lazy-fetch"].includes(token)) {
      index += 1;
    } else {
      return token === "rm";
    }
  }
  return false;
}

function hasRecursiveRm(command) {
  return command.split(/&&|\|\||[;&|\n]/).some((segment) => {
    if (isGitRm(segment)) return false;
    return RM_RECURSIVE.test(segment);
  });
}

function withoutReviewedPinManifest(command, cwd) {
  const reviewedPaths = new Set([
    "/Users/computer/Code/LockBox/tool-pins.env",
    "/workspaces/LockBox/tool-pins.env",
  ]);
  const simpleReader = /^(?:\/(?:usr\/)?bin\/)?(?:cat|bat|less|more|head|tail|nl|xxd|od|strings|hexdump|grep|egrep|fgrep|rg|ag|awk|gawk|sed|scp|rsync|cp|base64|openssl|sort|cut|tr)\s/.test(command.trim())
    && !/[;&|<>$`\\\n]/.test(command);
  // Only literal standalone paths qualify. Globs, traversal, expansions and
  // interpreter source remain subject to the conservative secret heuristic.
  return command.replace(/"[^"\n]*"|'[^'\n]*'|[^\s"']+/g, (token) => {
    const literal = token.replace(/^["']|["']$/g, "");
    if (!/^(?:\.?\/)?[A-Za-z0-9_./-]*tool-pins\.env$/.test(literal)
        || literal.split("/").includes("..")) return token;
    // The payload cwd does not describe a later cd or a wrapper's --chdir.
    // Relative exceptions therefore require a standalone reader invocation.
    if (!path.isAbsolute(literal) && !simpleReader) return token;
    if (!reviewedPaths.has(path.resolve(cwd, literal))) return token;
    return token.replace(/tool-pins\.env/, "tool-pins.manifest");
  });
}

// Only normalize a standalone filename inventory. Unknown shell syntax, rg
// modes/options, and everything after -- retain the conservative original text.
function withoutInventoryExclusions(command) {
  if (/[;&|<>$`\\\n]/.test(command)) return command;
  const tokens = [...command.matchAll(/"[^"\n]*"|'[^'\n]*'|[^\s"']+/g)];
  if (!tokens.length || !/^(?:rg|\/[^"'\s]+\/rg)$/.test(tokens[0][0])) return command;
  if (tokens[1]?.[0] !== "--files") return command;
  for (let i = 1; i < tokens.length; i += 1) {
    const previous = tokens[i - 1];
    if (!/^\s+$/.test(command.slice(previous.index + previous[0].length, tokens[i].index))) return command;
  }
  const kept = [tokens[0][0], tokens[1][0]];
  let options = true;
  for (let i = 2; i < tokens.length; i += 1) {
    const token = tokens[i][0];
    if (token === "--") options = false;
    if (options && (token === "-g" || token === "--glob")) {
      const value = tokens[++i]?.[0];
      if (!value) return command;
      if (!/^(["'])![^"']+\1$/.test(value)) kept.push(token, value);
    } else {
      if (options && token.startsWith("-") && !["--hidden", "--no-ignore", "--no-ignore-vcs", "--null", "-0"].includes(token)) return command;
      kept.push(token);
    }
  }
  return kept.join(" ");
}

// Return [decision, reason] for the first matching rule, or null.
function decide(command, cwd) {
  const normalized = command.replace(/\s+/g, " ").trim();
  const secretReadCommand = withoutReviewedPinManifest(withoutInventoryExclusions(command), cwd)
    .replace(/\s+/g, " ").trim();
  if (DD_INFO_ONLY.test(normalized)) return null;
  if (hasDownloadInterpreterPipeline(command)) {
    return ["deny", "piping a downloaded script straight into an interpreter"];
  }
  if (hasRecursiveRm(command)) return ["deny", "recursive rm requires an out-of-band user action"];
  if (/\bfind\b[^|;&\n]*\s-delete\b/i.test(command)) {
    return ["deny", "find -delete requires an out-of-band user action"];
  }
  if (/\brsync\b[^|;&\n]*\s--delete(?:\s|=|$)/i.test(command)) {
    return ["deny", "rsync --delete requires an out-of-band user action"];
  }
  if (/\bgit\b[^|;&\n]*\bclean\b[^|;&\n]*(?:--force\b|-\w*f\w*)/i.test(command)) {
    return ["deny", "forced git clean requires an out-of-band user action"];
  }
  for (const [regex, decision, reason] of COMPILED) {
    const candidate = reason === "reading a file that may contain secrets"
        || reason === "reading a secret file through an interpreter"
      ? secretReadCommand : normalized;
    if (regex.test(candidate)) return [decision, reason];
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

  const cwd = typeof payload.cwd === "string" && path.isAbsolute(payload.cwd)
    ? payload.cwd : process.cwd();
  const verdict = decide(command, cwd);
  if (verdict === null) process.exit(0); // nothing matched: normal flow continues

  const [decision, reason] = verdict;
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: decision,
      permissionDecisionReason: `Blocked by local safety guard: ${reason}.`,
    },
  }));
  process.exit(0);
}


// Imported command policy: dotfiles/Other/codex/hooks/guard.mjs.
// Managed settings and exact project opt-ins are the LockBox container adapter.
const PROJECT_POLICIES = {
  "dotfiles": {
    "name": "dotfiles",
    "root": "/Users/computer/Code/dotfiles",
    "autoFormat": true,
    "monitorPermissions": false
  },
  "LockBox": {
    "name": "LockBox",
    "root": "/Users/computer/Code/LockBox",
    "autoFormat": true,
    "monitorPermissions": false
  },
  "VaultLens": {
    "name": "VaultLens",
    "root": "/Users/computer/Code/VaultLens",
    "autoFormat": true,
    "monitorPermissions": false
  },
  "Watchman": {
    "name": "Watchman",
    "root": "/Users/computer/Code/Watchman",
    "autoFormat": true,
    "monitorPermissions": true
  },
  "Vision": {
    "name": "Vision",
    "root": "/Users/computer/Code/Vision",
    "autoFormat": true,
    "monitorPermissions": true
  },
  "git-agent": {
    "name": "git-agent",
    "root": "/Users/computer/Code/git-agent",
    "autoFormat": true,
    "monitorPermissions": false
  },
  "Brain": {
    "name": "Brain",
    "root": "/Users/computer/Library/Mobile Documents/iCloud~md~obsidian/Documents/Brain",
    "autoFormat": false,
    "monitorPermissions": true
  },
  "generic": {
    "name": "generic",
    "root": "",
    "autoFormat": false,
    "monitorPermissions": false
  },
  "Napoleon-relay": {
    "name": "Napoleon-relay",
    "root": "",
    "autoFormat": false,
    "monitorPermissions": false
  },
  "git-agent-publish": {
    "name": "git-agent-publish",
    "root": "",
    "autoFormat": false,
    "monitorPermissions": false
  }
};
if (process.argv[2] === "--profile-for-root" && process.argv.length === 4) {
  const selected = Object.values(PROJECT_POLICIES).find(p => p.root && p.root === process.argv[3]);
  process.stdout.write((selected?.name || "generic") + "\n");
} else if (process.argv[2] === "--watcher-policy" && process.argv.length === 5) {
  const selected = PROJECT_POLICIES[process.argv[3]];
  const root = process.argv[4];
  if (!selected || !/^\/workspaces\/[A-Za-z0-9_.-]+$/.test(root)) {
    process.stderr.write("Unknown reviewed project policy or invalid container root\n");
    process.exit(2);
  }
  process.stdout.write(JSON.stringify({ projects: [{ ...selected, root }] }, null, 2) + "\n");
} else if (process.argv[2] === "--codex-requirements" && process.argv.length === 3) {
  process.stdout.write(`[features]
hooks = true

[hooks]
managed_dir = "/usr/local/share/claude-guard"

[[hooks.PreToolUse]]
matcher = "Bash"
[[hooks.PreToolUse.hooks]]
type = "command"
command = "node /usr/local/share/claude-guard/guard.mjs"
timeout = 10
statusMessage = "Checking command safety"

[[hooks.PostToolUse]]
matcher = "Write|Edit|MultiEdit"
[[hooks.PostToolUse.hooks]]
type = "command"
command = "node /usr/local/share/claude-guard/hooks/post-edit.mjs"
timeout = 15
statusMessage = "Checking edits and reviewed formatting"
`);
} else if (process.argv[2] === "--managed-settings" && process.argv.length === 3) {
  process.stdout.write(JSON.stringify({
    sandbox: { enabled: false, autoAllowBashIfSandboxed: false },
    enabledPlugins: { "agents-md@builtin": true },
    pluginConfigs: { "agents-md@builtin": { options: { instructionFiles: "claude-md-and-agents-md" } } },
    hooks: {
      PreToolUse: [{ matcher: "Bash", hooks: [{ type: "command",
        command: "node /usr/local/share/claude-guard/guard.mjs", timeout: 10,
        statusMessage: "Checking command safety" }] }],
      PostToolUse: [{ matcher: "Write|Edit|MultiEdit", hooks: [{ type: "command",
        command: "node /usr/local/share/claude-guard/hooks/post-edit.mjs", timeout: 15,
        statusMessage: "Checking edits and reviewed formatting" }] }],
    },
  }, null, 2) + "\n");
} else if (process.argv.length > 2) {
  process.stderr.write("Unknown policy adapter arguments\n");
  process.exit(2);
} else {
  main();
}

// ─── vendored by LockBox v0.1.0 · canonical sha256:dac6d59a751fea689de074301ebafe63444d992da66fac9b4a64a9929f2ff224 ───
// Generated from the canonical source by LockBox/sync.sh — DO NOT EDIT HERE.
// Edit LockBox/claude-guard.mjs and re-run ./sync.sh.
