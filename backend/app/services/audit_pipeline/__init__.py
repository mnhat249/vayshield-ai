"""Pipeline Trạm 1 & 2 (T11 / WP1-06): APK → ảnh/giọng nói → kernel T02 → ma trận T09 → SSE.

Module chính: ``stream`` (SSE 5 pha), ``service`` (apk-verify / voice-terms / recalculate),
``compute`` (chặng tất định, dùng chung cả 3 đường), ``facts`` (cầu nối schema LLM ↔ kernel),
``ingest`` (làm sạch ảnh + VietQR trong threadpool), ``repository`` (đã che PII, session ngắn),
``container`` (dựng dịch vụ + readiness registry), ``auth`` (chữ ký thiết bị cho multipart).
"""
