#!/usr/bin/env node
// PostToolUse(Edit|Write|MultiEdit): tự format + lint nhanh file vừa sửa.
// Không bao giờ làm hỏng phiên: thiếu tool thì bỏ qua. Lỗi lint còn lại -> exit 2 để Claude thấy và tự sửa.
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

let raw = "";
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {
  let file = "";
  try {
    const j = JSON.parse(raw);
    file = (j.tool_input || {}).file_path || "";
  } catch {
    process.exit(0);
  }
  if (!file || !fs.existsSync(file)) process.exit(0);
  const ext = path.extname(file).toLowerCase();
  const problems = [];

  const run = (cmd, args, cwd) => {
    const r = spawnSync(cmd, args, { cwd, encoding: "utf8", shell: process.platform === "win32", timeout: 60000 });
    if (r.error) return null; // tool không có
    return r;
  };
  const findUp = (start, names) => {
    let d = path.dirname(path.resolve(start));
    while (true) {
      for (const n of names) if (fs.existsSync(path.join(d, n))) return d;
      const up = path.dirname(d);
      if (up === d) return null;
      d = up;
    }
  };

  if (ext === ".py") {
    if (run("ruff", ["--version"])?.status === 0) {
      run("ruff", ["format", file]);
      const r = run("ruff", ["check", "--fix", "--quiet", file]);
      if (r && r.status !== 0) problems.push(`ruff check ${file}:\n${(r.stdout || "") + (r.stderr || "")}`.trim());
    }
  } else if ([".ts", ".tsx", ".js", ".jsx", ".mjs", ".css", ".scss", ".json", ".md", ".yaml", ".yml"].includes(ext)) {
    // chỉ dùng prettier đã cài sẵn trong project (không tải qua mạng)
    const root = findUp(file, ["package.json"]);
    const bin = root && path.join(root, "node_modules", ".bin", process.platform === "win32" ? "prettier.cmd" : "prettier");
    if (bin && fs.existsSync(bin) && !/(^|[\\/])momo[\\/]/.test(file)) run(`"${bin}"`, ["--write", `"${file}"`], root);
    const eslint = root && path.join(root, "node_modules", ".bin", process.platform === "win32" ? "eslint.cmd" : "eslint");
    if (eslint && fs.existsSync(eslint) && [".ts", ".tsx", ".js", ".jsx"].includes(ext)) {
      const r = run(`"${eslint}"`, ["--fix", `"${file}"`], root);
      if (r && r.status !== 0) problems.push(`eslint ${file}:\n${(r.stdout || "") + (r.stderr || "")}`.trim());
    }
  } else if (ext === ".kt" || ext === ".kts") {
    if (run("ktlint", ["--version"])?.status === 0) {
      const r = run("ktlint", ["-F", file]);
      if (r && r.status !== 0) problems.push(`ktlint ${file}:\n${(r.stdout || "") + (r.stderr || "")}`.trim());
    }
  }

  if (problems.length) {
    process.stderr.write("Lint còn lỗi sau khi auto-fix — hãy sửa:\n" + problems.join("\n\n").slice(0, 4000));
    process.exit(2);
  }
  process.exit(0);
});
