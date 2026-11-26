"""Chạy hàm nặng CPU ngoài event loop (EC-OPS12)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable


async def run_cpu_bound[**P, R](func: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
    """Đẩy `func` sang threadpool; numpy/cv2/PIL/zxing nhả GIL nên không chặn loop."""
    return await asyncio.to_thread(func, *args, **kwargs)
