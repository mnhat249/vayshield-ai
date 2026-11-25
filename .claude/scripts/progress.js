#!/usr/bin/env node
// Tính % tiến độ từ .claude/plans/<slug>/PROGRESS.md.
// Dùng: node .claude/scripts/progress.js <slug> [--write]
//   --write: ghi lại khối tóm tắt giữa <!-- progress:start --> và <!-- progress:end -->
const fs = require("fs");
const path = require("path");

const slug = process.argv[2];
const write = process.argv.includes("--write");
if (!slug) {
  console.error("Dùng: node .claude/scripts/progress.js <slug> [--write]");
  process.exit(1);
}
const file = path.join(process.cwd(), ".claude", "plans", slug, "PROGRESS.md");
if (!fs.existsSync(file)) {
  console.error("Không thấy " + file);
  process.exit(1);
}
const md = fs.readFileSync(file, "utf8");

const SCORE = { pending: 0, running: 0.2, implemented: 0.5, verified: 0.7, reviewed: 0.85, merged: 1 };
const lines = md.split(/\r?\n/);
const header = lines.find((l) => /^\|\s*ID\s*\|/.test(l));
if (!header) {
  console.error("Không thấy bảng task (dòng tiêu đề bắt đầu bằng | ID |)");
  process.exit(1);
}
const cols = header.split("|").slice(1, -1).map((c) => c.trim().toLowerCase());
const ix = (name) => cols.findIndex((c) => c.startsWith(name));
const iId = ix("id"), iWave = ix("wave"), iMs = ix("mốc"), iW = ix("trọng số"), iSt = ix("trạng thái");

const tasks = lines
  .filter((l) => /^\|\s*T\d+\s*\|/.test(l))
  .map((l) => {
    const c = l.split("|").slice(1, -1).map((x) => x.trim());
    return { id: c[iId], wave: c[iWave], ms: c[iMs], w: parseFloat(c[iW]) || 0, st: (c[iSt] || "pending").replace(/`/g, "").toLowerCase() };
  });

// blocked: giữ điểm trước đó không biết được → tính 0, nhưng báo riêng
const score = (t) => (t.st in SCORE ? SCORE[t.st] : 0);
const agg = (list) => {
  const act = list.filter((t) => t.st !== "skip");
  const tot = act.reduce((s, t) => s + t.w, 0);
  return tot ? (100 * act.reduce((s, t) => s + t.w * score(t), 0)) / tot : 0;
};
const group = (key) => [...new Set(tasks.map((t) => t[key]))].sort().map((k) => [k, agg(tasks.filter((t) => t[key] === k))]);

const total = agg(tasks);
const merged = tasks.filter((t) => t.st === "merged").length;
const active = tasks.filter((t) => t.st !== "skip").length;
const blocked = tasks.filter((t) => t.st === "blocked").map((t) => t.id);
const bar = "█".repeat(Math.round(total / 5)).padEnd(20, "░");
const waves = group("wave");
const mss = group("ms");
const now = new Date();
const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`; // giờ địa phương

const rows = [];
for (let i = 0; i < Math.max(waves.length, mss.length); i++) {
  const w = waves[i] ? [`W${waves[i][0]}`, `${waves[i][1].toFixed(0)}%`] : ["", ""];
  const m = mss[i] ? [mss[i][0], `${mss[i][1].toFixed(0)}%`] : ["", ""];
  rows.push(`| ${w[0]} | ${w[1]} | | ${m[0]} | ${m[1]} |`);
}
const block = [
  `**Tổng tiến độ: ${total.toFixed(1)}%** \`[${bar}]\` — merged ${merged}/${active} task · cập nhật ${today}` +
    (blocked.length ? ` · ⛔ blocked: ${blocked.join(", ")}` : ""),
  "",
  "| Wave | Tiến độ | | Mốc | Tiến độ |",
  "|---|---|---|---|---|",
  ...rows,
].join("\n");

console.log(block);
if (write) {
  const out = md.replace(/<!-- progress:start -->[\s\S]*?<!-- progress:end -->/, `<!-- progress:start -->\n${block}\n<!-- progress:end -->`);
  fs.writeFileSync(file, out);
  console.log("\n→ đã ghi " + path.relative(process.cwd(), file));
}
