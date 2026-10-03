#!/usr/bin/env node
// PostToolUse(Write|Edit) monitor and allowlisted formatter for Codex.
//
// Security and permission checks are deterministic. Formatting is the only
// subprocess behavior: exact reviewed roots may dispatch to the fixed deployed
// bounded formatter, which never installs or downloads tools. Reports contain
// only fixed categories and counts; edited text, paths, and permission rule
// strings never flow into systemMessage or additionalContext.

import {
  existsSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { extname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const WATCHED_PROJECTS_FILE = fileURLToPath(new URL("./watched-projects.json", import.meta.url));
const EXPLICIT_FORMATTER = fileURLToPath(new URL("../managed/explicit-format", import.meta.url));
const AUTO_FORMAT_EXTENSIONS = new Set([
  ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json", ".jsonc",
  ".css", ".scss", ".less", ".html", ".vue", ".svelte", ".md", ".mdx",
  ".yaml", ".yml", ".graphql", ".py", ".rs", ".go",
]);
const AUTO_FORMAT_BUDGET_MS = 12_000;
const AUTO_FORMAT_PER_FILE_TIMEOUT_MS = 5_000;
const STATE_DIR = process.env.CODEX_PERMISSION_WATCH_STATE_DIR
  || join(homedir(), ".agent-policy", "permission-watch-state");

function readStdin() {
  return new Promise((resolveInput) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => (data += chunk));
    process.stdin.on("end", () => resolveInput(data));
  });
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function atomicJson(path, value) {
  const temporary = `${path}.tmp-${process.pid}`;
  writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  renameSync(temporary, path);
}

function watchedProjects() {
  const parsed = JSON.parse(readFileSync(WATCHED_PROJECTS_FILE, "utf8"));
  if (!Array.isArray(parsed.projects)) throw new Error("projects must be an array");
  return parsed.projects.map((project) => {
    if (
      !project
      || typeof project.name !== "string"
      || !/^[A-Za-z0-9._ -]{1,64}$/.test(project.name)
      || typeof project.root !== "string"
      || !isAbsolute(project.root)
      || typeof project.autoFormat !== "boolean"
      || typeof project.monitorPermissions !== "boolean"
    ) {
      throw new Error("invalid watched project");
    }
    const root = existsSync(project.root) ? realpathSync(project.root) : resolve(project.root);
    return {
      name: project.name,
      root,
      autoFormat: project.autoFormat,
      monitorPermissions: project.monitorPermissions,
    };
  });
}

function isInside(root, path) {
  const fromRoot = relative(root, path);
  return fromRoot !== ".." && !fromRoot.startsWith(`..${sep}`) && !isAbsolute(fromRoot);
}

function ruleHashes(settings) {
  const allow = settings?.permissions?.allow ?? [];
  if (
    !Array.isArray(allow)
    || allow.length > 1000
    || allow.some((rule) => typeof rule !== "string" || rule.length > 4096)
  ) {
    throw new Error("invalid permissions.allow");
  }
  return [...new Set(allow.map(sha256))].sort();
}

function readPreviousHashes(stateFile) {
  if (!existsSync(stateFile)) return [];
  const parsed = JSON.parse(readFileSync(stateFile, "utf8"));
  if (!Array.isArray(parsed.ruleHashes) || parsed.ruleHashes.some((value) => typeof value !== "string")) {
    throw new Error("invalid permission monitor state");
  }
  return parsed.ruleHashes;
}

function preparePermissionUpdate(filePath, projects) {
  if (!existsSync(filePath) || !statSync(filePath).isFile()) return null;
  const canonicalFile = realpathSync(filePath);
  const watched = projects.find((project) => (
    project.monitorPermissions
    && ["settings.json", "settings.local.json"].some((name) => (
      relative(project.root, canonicalFile) === join(".claude", name)
    ))
  ));
  if (!watched) return null;

  const settings = JSON.parse(readFileSync(canonicalFile, "utf8"));
  const currentHashes = ruleHashes(settings);
  mkdirSync(STATE_DIR, { recursive: true, mode: 0o700 });
  const stateFile = join(STATE_DIR, `${sha256(canonicalFile)}.json`);
  const pendingFile = `${stateFile}.pending`;
  const previousHashes = readPreviousHashes(stateFile);
  const previous = new Set(previousHashes);
  const addedCount = currentHashes.filter((value) => !previous.has(value)).length;
  const nextState = { version: 1, ruleHashes: currentHashes };

  if (addedCount === 0) {
    atomicJson(stateFile, nextState);
    if (existsSync(pendingFile)) unlinkSync(pendingFile);
    return null;
  }

  // A pending marker is durable before output. It is not treated as delivered.
  // If the process dies before stdout completes, the next edit alerts again.
  atomicJson(pendingFile, {
    version: 1,
    addedCount,
    ruleSetDigest: sha256(currentHashes.join("\n")),
  });

  return {
    addedCount,
    projectName: watched.name,
    commit() {
      atomicJson(stateFile, nextState);
      if (existsSync(pendingFile)) unlinkSync(pendingFile);
    },
  };
}

function autoFormat(filePath, projects, formattedFiles, deadline) {
  if (!existsSync(filePath) || !statSync(filePath).isFile()) return null;
  if (!AUTO_FORMAT_EXTENSIONS.has(extname(filePath).toLowerCase())) return null;

  const canonicalFile = realpathSync(filePath);
  const project = projects.find((candidate) => (
    candidate.autoFormat && isInside(candidate.root, canonicalFile)
  ));
  if (!project) return null;

  if (formattedFiles.has(canonicalFile)) return null;
  formattedFiles.add(canonicalFile);

  const remainingMs = deadline - Date.now();
  if (remainingMs <= 0) return { projectName: project.name };

  const result = spawnSync(EXPLICIT_FORMATTER, ["--automatic", canonicalFile], {
    cwd: project.root,
    stdio: "ignore",
    timeout: Math.min(AUTO_FORMAT_PER_FILE_TIMEOUT_MS, remainingMs),
  });
  if (!result.error && result.status === 3) return null;
  if (result.error || result.status !== 0) return { projectName: project.name };
  return null;
}

// This is deliberately local and deterministic. SECURITY_RULES inspect only
// the edit-tool input, never open source files, and never call a network service.
const SECURITY_RULES = [
  ["possible hard-coded credential", /\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|private[_-]?key|client[_-]?secret)\b\s*[:=]\s*["'][^"'\n]{8,}["']/i],
  ["dynamic code execution", /\b(?:eval|exec)\s*\(/],
  ["shell execution with interpolation", /\b(?:exec|execSync)\s*\(\s*`[^`]*\$\{|\bshell\s*=\s*True\b/],
  ["TLS certificate verification disabled", /rejectUnauthorized\s*:\s*false|\bverify\s*=\s*False\b|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*["']?0/],
  ["unsafe HTML injection", /dangerouslySetInnerHTML\s*=|\.innerHTML\s*=/],
  ["world-writable permission", /\bchmod\b[^\n]*(?:0777|777)\b|\bos\.chmod\s*\([^\n]*(?:0o777|511)\b/],
  ["download piped to a shell", /\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b/],
  ["weak JWT verification", /algorithms?\s*[:=]\s*\[[^\]]*["']none["']/i],
];

function addedPatchText(patch) {
  return patch
    .split("\n")
    .filter((line) => line.startsWith("+") && !line.startsWith("+++"))
    .map((line) => line.slice(1))
    .join("\n");
}

function securityFindings(toolInput, patch) {
  const parts = [];
  const addEditText = (edit) => {
    if (!edit || typeof edit !== "object") return;
    if (typeof edit.content === "string") parts.push(edit.content);
    if (typeof edit.new_string === "string") parts.push(edit.new_string);
    if (typeof edit.newString === "string") parts.push(edit.newString);
  };
  addEditText(toolInput);
  if (Array.isArray(toolInput.edits)) {
    for (const edit of toolInput.edits) addEditText(edit);
  }
  if (patch) parts.push(addedPatchText(patch));
  const edited = parts.join("\n");
  if (!edited) return [];
  return [...new Set(
    SECURITY_RULES.filter(([, pattern]) => pattern.test(edited)).map(([name]) => name),
  )];
}

function writeOutput(value) {
  return new Promise((resolveOutput, rejectOutput) => {
    process.stdout.write(JSON.stringify(value), (error) => {
      if (error) rejectOutput(error);
      else resolveOutput();
    });
  });
}

async function main() {
  let payload;
  try {
    payload = JSON.parse(await readStdin());
  } catch {
    return;
  }

  const toolInput = payload?.tool_input || {};
  const toolResponse = payload?.tool_response || {};
  if (toolResponse.isError === true || toolResponse.success === false || toolResponse.error) return;

  const cwd = resolve(payload?.cwd || process.cwd());
  const files = new Set();
  const legacyFile = toolInput.file_path || toolInput.filePath || "";
  if (legacyFile) files.add(legacyFile);

  const patch = typeof toolInput.command === "string" ? toolInput.command : "";
  for (const match of patch.matchAll(/^\*\*\* (?:Add|Update|Delete) File: (.+)$/gm)) {
    files.add(match[1].trim());
  }
  for (const match of patch.matchAll(/^\*\*\* Move to: (.+)$/gm)) {
    files.add(match[1].trim());
  }

  const securityWarnings = securityFindings(toolInput, patch);
  const permissionUpdates = [];
  const formatFailures = [];
  const formattedFiles = new Set();
  const formatDeadline = Date.now() + AUTO_FORMAT_BUDGET_MS;
  let permissionMonitorError = false;
  let projectPolicyError = false;
  let projects;
  try {
    projects = watchedProjects();
  } catch {
    projects = [];
    projectPolicyError = true;
  }

  for (let file of files) {
    if (!isAbsolute(file)) file = resolve(cwd, file);
    else file = resolve(file);

    // Each operation checks the canonical path against reviewed project roots.
    // A session started below the root may still edit root-level settings.
    try {
      const update = preparePermissionUpdate(file, projects);
      if (update) permissionUpdates.push(update);
    } catch {
      permissionMonitorError = true;
    }
    try {
      const failure = autoFormat(file, projects, formattedFiles, formatDeadline);
      if (failure) formatFailures.push(failure);
    } catch {
      formatFailures.push({ projectName: "reviewed project" });
    }
  }

  if (
    permissionUpdates.length === 0
    && formatFailures.length === 0
    && securityWarnings.length === 0
    && !permissionMonitorError
    && !projectPolicyError
  ) return;

  const messages = [];
  const contexts = [];
  for (const update of permissionUpdates) {
    messages.push(`${update.projectName}: permission allowlist grew by ${update.addedCount} rule(s).`);
    contexts.push(
      `[permission-watch hook] ${update.projectName} added ${update.addedCount} permission rule(s). `
      + "Tell the user and review the settings change before continuing.",
    );
  }
  if (permissionMonitorError) {
    messages.push("Permission monitor could not verify one or more edited settings files.");
    contexts.push("[permission-watch hook] Verification failed. Tell the user and inspect the monitor state.");
  }
  if (projectPolicyError) {
    messages.push("Post-edit project policy could not be loaded.");
    contexts.push(
      "[post-edit hook] Project policy verification failed. Tell the user; automatic formatting was skipped.",
    );
  }
  if (formatFailures.length > 0) {
    messages.push(`Automatic formatting failed for ${formatFailures.length} opted-in file(s).`);
    contexts.push(
      `[auto-format hook] Formatting failed for ${formatFailures.length} opted-in file(s). `
      + "Tell the user and run the bounded formatter explicitly to inspect the failure.",
    );
  }
  if (securityWarnings.length > 0) {
    messages.push(`Security guidance: review ${securityWarnings.length} high-risk pattern(s) in the latest edit.`);
    contexts.push(
      `[security-guidance hook] Review the latest edit before continuing. Detected categories: `
      + `${securityWarnings.join(", ")}. Treat these as candidate risks and validate data flow and mitigations.`,
    );
  }

  // Commit permission state only after the alert has been accepted by stdout.
  await writeOutput({
    systemMessage: messages.join(" "),
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      additionalContext: contexts.join("\n"),
    },
  });
  for (const update of permissionUpdates) update.commit();
}

main().catch(() => process.exit(0));

// ─── vendored by LockBox v0.1.0 · canonical sha256:f9b453c2c4b7f414bcb1aec73d13095a4c7da3390f490d6a21b7b793db3c9ba2 ───
// Generated from the canonical source by LockBox/sync.sh — DO NOT EDIT HERE.
// Edit LockBox/claude-post-edit.mjs and re-run ./sync.sh.
