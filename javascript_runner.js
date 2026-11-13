"use strict";

const fs = require("fs");
const vm = require("vm");

function utcNow() {
  return new Date().toISOString();
}

function emit(payload) {
  process.stdout.write(JSON.stringify(payload) + "\n");
}

function loadStdinLines(path) {
  const raw = fs.readFileSync(path, "utf8");
  const lines = JSON.parse(raw);
  if (!Array.isArray(lines)) {
    throw new Error("stdin_lines must be a JSON array of strings");
  }
  return lines.map(String);
}

function createSandbox(stdinLines) {
  let stdinIdx = 0;
  const sandbox = {
    console: {
      log(...args) {
        emit({
          type: "log",
          stream: "stdout",
          message: args.map((a) => String(a)).join(" "),
          timestamp: utcNow(),
        });
      },
      error(...args) {
        emit({
          type: "log",
          stream: "stderr",
          message: args.map((a) => String(a)).join(" "),
          timestamp: utcNow(),
        });
      },
    },
    input() {
      if (stdinIdx >= stdinLines.length) {
        throw new Error(
          "input() called but no more stdin_lines were provided in the API request"
        );
      }
      return stdinLines[stdinIdx++];
    },
    Math,
    JSON,
    Array,
    Object,
    String,
    Number,
    Boolean,
    Date,
    RegExp,
    Map,
    Set,
    WeakMap,
    WeakSet,
    Symbol,
    BigInt,
    parseInt,
    parseFloat,
    isNaN,
    isFinite,
    decodeURI,
    encodeURI,
    decodeURIComponent,
    encodeURIComponent,
    Intl,
    undefined,
  };
  vm.createContext(sandbox);
  return sandbox;
}

async function run() {
  if (process.argv.length < 4) {
    emit({ type: "error", error: "runner_misconfigured", detail: "code and stdin paths required" });
    process.exit(2);
  }

  const startedAt = utcNow();
  const t0 = Date.now();
  const timeoutMs = parseInt(process.env.CE_RUN_TIMEOUT_MS || "30000", 10);

  let code;
  let stdinLines;
  try {
    code = fs.readFileSync(process.argv[2], "utf8");
    stdinLines = loadStdinLines(process.argv[3]);
  } catch (err) {
    emit({
      type: "error",
      error: "read_failed",
      detail: String(err),
      started_at: startedAt,
      ended_at: utcNow(),
      duration_ms: Date.now() - t0,
    });
    process.exit(1);
  }

  const sandbox = createSandbox(stdinLines);

  try {
    const script = new vm.Script(code, { filename: "user_code.js" });
    script.runInContext(sandbox, { timeout: timeoutMs });
  } catch (err) {
    const isSyntax = err && err.name === "SyntaxError";
    emit({
      type: "error",
      error: isSyntax ? "syntax_error" : "runtime_error",
      detail: (err && err.stack) || String(err),
      started_at: startedAt,
      ended_at: utcNow(),
      duration_ms: Date.now() - t0,
    });
    process.exit(1);
  }

  const hasMain = typeof sandbox.main === "function";
  let resultPresent = false;
  let result;

  if (hasMain) {
    try {
      let value = sandbox.main();
      if (value && typeof value.then === "function") {
        value = await value;
      }
      result = value;
      resultPresent = true;
    } catch (err) {
      emit({
        type: "error",
        error: "main_failed",
        detail: (err && err.stack) || String(err),
        started_at: startedAt,
        ended_at: utcNow(),
        duration_ms: Date.now() - t0,
      });
      process.exit(1);
    }
  }

  const complete = {
    type: "complete",
    has_main: hasMain,
    started_at: startedAt,
    ended_at: utcNow(),
    duration_ms: Date.now() - t0,
  };
  if (resultPresent) {
    complete.result = result;
  }
  emit(complete);
}

run().catch((err) => {
  emit({ type: "error", error: "runner_failed", detail: String(err) });
  process.exit(2);
});
