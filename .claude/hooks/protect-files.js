#!/usr/bin/env node
// PreToolUse(Edit|Write|MultiEdit|NotebookEdit): bảo vệ file nhạy cảm và vùng cấm sửa.
let raw = "";
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {
  let input = {};
  try {
    input = JSON.parse(raw).tool_input || {};
  } catch {
    process.exit(0);
  }
  const p = String(input.file_path || input.notebook_path || "").replace(/\\/g, "/");
  const content = [input.content, input.new_string, ...(input.edits || []).map((e) => e.new_string)].filter(Boolean).join("\n");

  if (/(^|\/)\.env(?!\.example$)[^/]*$|\.(pem|key|jks|keystore|p12)$|(^|\/)id_rsa|google-services\.json$/i.test(p))
    return decide("deny", `Không được ghi file secret: ${p}`);

  if (/(AIza[0-9A-Za-z_\-]{35}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{36}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[bp]-[A-Za-z0-9-]{10,})/.test(content))
    return decide("deny", "Nội dung có chuỗi giống API key/private key. Dùng biến môi trường + .env.example.");

  // Đường dẫn tương đối so với gốc project (hoặc worktree) để tránh nhầm khi tên thư mục project trùng "momo".
  const root = String(process.env.CLAUDE_PROJECT_DIR || process.cwd()).replace(/\\/g, "/").replace(/\/$/, "");
  let rel = p.toLowerCase().startsWith(root.toLowerCase() + "/") ? p.slice(root.length + 1) : p;
  rel = rel.replace(/^\.claude\/worktrees\/[^/]+\//, ""); // file trong worktree agent

  if (/^momo\/[^/]+\.md$/.test(rel)) return decide("ask", "Đang sửa tài liệu đặc tả gốc (momo/*.md) — vốn chỉ đọc.");
  if (/(^|\/)contracts\//.test(rel)) return decide("ask", "Đang sửa hợp đồng API (contracts/) — đã khóa sau M0, cần người duyệt.");
  if (/^\.claude\/(settings\.json|hooks\/)/.test(rel)) return decide("ask", "Đang sửa settings/hooks bảo vệ của dự án.");
  process.exit(0);
});

function decide(decision, reason) {
  process.stdout.write(
    JSON.stringify({ hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: decision, permissionDecisionReason: reason } })
  );
  process.exit(0);
}
