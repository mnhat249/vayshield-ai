"""Judge Sandbox Mode (File 4 EC-DB16; File 6 §1.2 mục 3, EC-OPS10; File 5 §2.1).

- ``session``: phiên ephemeral (mỗi tab một ``sandbox_namespace`` + ``device_id`` riêng).
- ``budget``: cầu chì ngân sách ngày theo tier (PUBLIC / JUDGE_VIP).
- ``service``: ``/sandbox/simulate-event`` chạy xuyên lõi thật (ShieldService T12, store/HSM T05).
- ``deps``: dây nối FastAPI (ghi đè ``get_sandbox_resolver`` của T12).
"""
