#!/usr/bin/env node
// PreToolUse(Bash): chặn lệnh nguy hiểm, bắt người duyệt cho deploy/push/xoá/gửi.
// Đầu vào: JSON hook trên stdin. Đầu ra: JSON permissionDecision (deny | ask) hoặc im lặng (cho qua).
const { execSync } = require("child_process");

let raw = "";
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {
  let cmd = "";
  try {
    cmd = (JSON.parse(raw).tool_input || {}).command || "";
  } catch {
    process.exit(0);
  }

  const DENY = [
    [/git\s+push\b[^\n]*(--force\b|-f\b|--force-with-lease)[^\n]*\b(main|master)\b/, "Force-push lên main/master bị cấm."],
    [/git\s+push\b[^\n]*\b(main|master)\b[^\n]*(--force\b|-f\b)/, "Force-push lên main/master bị cấm."],
    [/rm\s+-[a-z]*r[a-z]*f?[a-z]*\s+(\/|~|\$HOME|\.\.?)(\s|$)/i, "rm -rf trên thư mục gốc/home/project bị cấm."],
    [/\b(DROP\s+(DATABASE|SCHEMA)|TRUNCATE\s+TABLE)\b/i, "DROP DATABASE/SCHEMA/TRUNCATE bị cấm qua agent."],
    [/git\s+add\s+(-A|--all|\.)(\s|$)[^\n]*/, null], // kiểm tra riêng bên dưới
    [/(cat|type|less|more|Get-Content)\s+[^\n|]*\.env(\.|\s|$)(?!example)/i, "Không đọc file .env (secret)."],
  ];

  const ASK = [
    [/\bgit\s+push\b/, "git push — đưa code ra remote"],
    [/\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|branch\s+-D|worktree\s+remove\s+--force)\b/, "Thao tác git huỷ dữ liệu"],
    [/\bgh\s+(pr\s+(merge|create|close)|release|repo\s+(delete|edit))\b/, "Thao tác GitHub công khai"],
    [/\b(vercel\b[^\n]*--prod|vercel\s+deploy|gcloud\s+(run\s+deploy|app\s+deploy|builds\s+submit)|railway\s+up|fly\s+deploy|firebase\s+deploy|docker\s+push|npm\s+publish|twine\s+upload|kubectl\s+(apply|delete)|terraform\s+(apply|destroy))\b/, "DEPLOY / publish"],
    [/\b(curl|wget|Invoke-WebRequest|http)\b[^\n]*(-X\s*(POST|PUT|PATCH|DELETE)|--data|-d\s)/i, "Gửi request ghi ra dịch vụ ngoài"],
    [/\b(DELETE\s+FROM|DROP\s+TABLE|ALTER\s+TABLE[^\n]*DROP)\b/i, "Xoá dữ liệu DB"],
    [/\b(sendgrid|twilio|smtp|sendmail|mail\s+-s)\b/i, "Gửi email/SMS"],
    [/\b(alembic\s+downgrade|prisma\s+migrate\s+reset)\b/i, "Rollback/reset migration"],
  ];

  for (const [re, reason] of DENY) {
    if (reason && re.test(cmd)) return decide("deny", reason);
  }

  // git add -A / . : không được dính .claude/worktrees (gitlink lồng nhau) hay secret.
  if (/git\s+add\s+(-A|--all|\.)(\s|$)/.test(cmd)) {
    return decide("deny", "Không dùng `git add -A`/`git add .` — hãy add đúng file thuộc vùng `owns` của task.");
  }

  // git commit: quét file staged xem có secret không.
  if (/\bgit\s+commit\b/.test(cmd)) {
    try {
      const staged = execSync("git diff --cached --name-only", { encoding: "utf8" }).split(/\r?\n/).filter(Boolean);
      const bad = staged.filter((f) => /(^|\/)\.env(?!\.example$)|\.pem$|\.key$|\.jks$|\.keystore$|id_rsa|google-services\.json$|\.claude\/worktrees\//i.test(f));
      if (bad.length) return decide("deny", "File nhạy cảm đang được stage: " + bad.join(", "));
      const diff = execSync("git diff --cached -U0", { encoding: "utf8", maxBuffer: 20 * 1024 * 1024 });
      if (/^\+.*(AIza[0-9A-Za-z_\-]{35}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{36}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[bp]-[A-Za-z0-9-]{10,})/m.test(diff))
        return decide("deny", "Diff staged có chuỗi giống API key/private key. Gỡ ra và dùng biến môi trường.");
    } catch {
      /* không phải repo git hoặc chưa có HEAD: bỏ qua */
    }
  }

  for (const [re, reason] of ASK) {
    if (re.test(cmd)) return decide("ask", `⚠ Cần người duyệt: ${reason}.`);
  }
  process.exit(0);
});

function decide(decision, reason) {
  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: decision, permissionDecisionReason: reason },
    })
  );
  process.exit(0);
}
