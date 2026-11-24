#!/usr/bin/env node
// Chạy test / lint / typecheck theo stack có trong repo. Phần nào chưa tồn tại thì SKIP.
// Dùng: node .claude/scripts/run-checks.js <test|lint|typecheck|all> [backend|web|android|all]
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const root = process.cwd();
const kind = process.argv[2] || "all";
const target = process.argv[3] || "all";
const isWin = process.platform === "win32";
const has = (p) => fs.existsSync(path.join(root, p));
const pkg = (dir) => {
  try {
    return JSON.parse(fs.readFileSync(path.join(root, dir, "package.json"), "utf8")).scripts || {};
  } catch {
    return {};
  }
};

const stacks = {
  backend: {
    present: () => has("backend") && (has("backend/pyproject.toml") || has("backend/requirements.txt")),
    dir: "backend",
    test: [["pytest", ["-q"]]],
    lint: [["ruff", ["check", "."]], ["ruff", ["format", "--check", "."]]],
    typecheck: [["mypy", ["."]]],
  },
  web: {
    present: () => has("web-portal/package.json"),
    dir: "web-portal",
    test: () => (pkg("web-portal").test ? [["npm", ["test", "--", "--run"]]] : []),
    lint: () => (pkg("web-portal").lint ? [["npm", ["run", "lint"]]] : []),
    typecheck: () => (pkg("web-portal").typecheck ? [["npm", ["run", "typecheck"]]] : [["npx", ["tsc", "--noEmit"]]]),
  },
  android: {
    present: () => has("android-app/gradlew") || has("android-app/gradlew.bat"),
    dir: "android-app",
    test: [[isWin ? "gradlew.bat" : "./gradlew", ["testDebugUnitTest"]]],
    lint: [[isWin ? "gradlew.bat" : "./gradlew", ["lint"]]],
    typecheck: [[isWin ? "gradlew.bat" : "./gradlew", ["compileDebugKotlin"]]],
  },
};

const kinds = kind === "all" ? ["lint", "typecheck", "test"] : [kind];
const names = target === "all" ? Object.keys(stacks) : [target];
const results = [];

for (const name of names) {
  const s = stacks[name];
  if (!s) {
    console.error(`Không biết stack "${name}". Dùng: backend | web | android | all`);
    process.exit(1);
  }
  if (!s.present()) {
    results.push([name, "-", "SKIP (chưa có code)"]);
    continue;
  }
  for (const k of kinds) {
    const cmds = typeof s[k] === "function" ? s[k]() : s[k];
    if (!cmds.length) {
      results.push([name, k, "SKIP (chưa cấu hình script)"]);
      continue;
    }
    for (const [cmd, args] of cmds) {
      console.log(`\n▶ [${name}] ${cmd} ${args.join(" ")}`);
      const r = spawnSync(cmd, args, { cwd: path.join(root, s.dir), stdio: "inherit", shell: isWin });
      const status = r.error ? `ERROR (${r.error.code})` : r.status === 0 ? "PASS" : `FAIL (exit ${r.status})`;
      results.push([name, `${k}: ${cmd} ${args.join(" ")}`, status]);
    }
  }
}

console.log("\n=== TÓM TẮT ===");
for (const [n, k, st] of results) console.log(`${st.padEnd(28)} ${n.padEnd(8)} ${k}`);
process.exit(results.some(([, , st]) => st.startsWith("FAIL") || st.startsWith("ERROR")) ? 1 : 0);
