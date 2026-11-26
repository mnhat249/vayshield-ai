"""Mật mã & bảo mật nền (T10 — File 4 §5.1–5.2, File 2 §1.1).

Module:
- ``keys``: nạp bí mật từ ``Settings`` (``SecretStr``), không log/repr giá trị.
- ``envelope``: AES-256-GCM, DEK riêng từng bản ghi bọc bằng KEK (xoay ``KMS_MASTER_KEK_PREV``).
- ``phone``: chuẩn hóa E.164 (EC-DB10).
- ``blind_index``: HMAC-SHA256 dual-pepper (EC-DB08).
- ``signing``: Ed25519 policy bundle + ``SignedServerEpochOffset`` (S2-EC29).
- ``device_auth``: dependency FastAPI kiểm chữ ký ECDSA P-256 thiết bị.
- ``partner_hmac``: HMAC webhook B2B + cửa sổ 300s (EC-DB19).
- ``tickets``: vé WS/SSE dùng 1 lần qua ``GETDEL`` (EC-DB12).
- ``rate_limit``: token bucket kép device → IP (EC-DB25).
- ``cors``: kiểm cấu hình origin chặt (EC-DB24).
- ``erasure``: xóa dữ liệu theo Luật 91 với bia mộ ẩn danh (EC-DB21).
"""
