"""Domain Guardrail (EC-AI17): chặn câu hỏi ngoài phạm vi và ý định né nghĩa vụ trả nợ.

So khớp theo cửa sổ token. Cụm từ khai báo CÓ dấu; một token của câu hỏi khớp nếu giống đúng
dạng có dấu hoặc là dạng đã bỏ dấu hoàn toàn (người dùng gõ không dấu). Vì vậy "hợp lý hơn"
không khớp "ly hôn" và "bùng nổ" không khớp "bùng nợ", còn "ne no app vay" vẫn bị chặn.
Giả định an toàn: câu hỏi gõ không dấu hoàn toàn mà trùng cụm (vd "bung no") bị từ chối.
"""

from __future__ import annotations

import re
import unicodedata

from .text import fold

MAX_QUERY_CHARS = 2000

_TOKEN = re.compile(r"[^\W_]+")

# Chủ đề ngoài phạm vi an toàn vay vốn (đất đai, thừa kế, hôn nhân...).
_OUT_OF_SCOPE_TOPICS: tuple[str, ...] = (
    "đất đai",
    "tranh chấp đất",
    "ranh giới đất",
    "quyền sử dụng đất",
    "sổ hồng",
    "thừa kế",
    "di sản",
    "di chúc",
    "ly hôn",
    "ly dị",
    "quyền nuôi con",
    "divorce",
    "inheritance",
    "custody",
    "probate",
    "land dispute",
    "property dispute",
)

# Ý định né nghĩa vụ trả nợ: luôn chặn.
_EVASION_STRONG: tuple[str, ...] = (
    "bùng nợ",
    "bùng app",
    "bùng tiền",
    "bùng khoản vay",
    "trốn nợ",
    "trốn trả nợ",
    "trốn tiền vay",
    "né nợ",
    "né trả nợ",
    "xù nợ",
    "xử nợ",
    "quịt nợ",
    "giấu tài sản để",
    "evade debt",
    "dodge debt",
    "escape debt",
    "avoid repaying",
    "avoid paying back",
    "avoid paying the loan",
)

# Chỉ chặn khi đi kèm từ chỉ "cách làm" (để không chặn câu hỏi hậu quả của nạn nhân).
_EVASION_WEAK: tuple[str, ...] = (
    "không trả nợ",
    "không phải trả nợ",
    "không cần trả nợ",
    "không trả tiền",
    "xóa nợ",
    "not repay",
    "not pay back",
    "not pay the loan",
)
_HOW_CUES: tuple[str, ...] = (
    "cách",
    "làm sao",
    "làm thế nào",
    "bằng cách",
    "mẹo",
    "để",
    "how to",
    "how can i",
    "tips",
)

REFUSAL_MESSAGE = (
    "Xin lỗi, VayShield chỉ hỗ trợ các câu hỏi về an toàn vay vốn và phòng chống lừa đảo tài "
    "chính. Tôi không thể tư vấn câu hỏi này và không hướng dẫn cách né tránh nghĩa vụ trả nợ "
    "trái pháp luật. Nếu đang bị đòi nợ quá mức hoặc bị đe dọa, hãy liên hệ cơ quan chức năng "
    "hoặc tổ chức tín dụng liên quan."
)
TOO_LONG_MESSAGE = f"Câu hỏi quá dài (tối đa {MAX_QUERY_CHARS} ký tự). Vui lòng rút gọn."


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(unicodedata.normalize("NFC", text).lower())


def _phrase_present(query_tokens: list[str], phrase: str) -> bool:
    ptoks = _tokens(phrase)
    n = len(ptoks)
    if n == 0 or len(query_tokens) < n:
        return False
    for i in range(len(query_tokens) - n + 1):
        window = query_tokens[i : i + n]
        if all(w == p or w == fold(p) for w, p in zip(window, ptoks, strict=True)):
            return True
    return False


def _any(query_tokens: list[str], phrases: tuple[str, ...]) -> bool:
    return any(_phrase_present(query_tokens, p) for p in phrases)


def is_in_scope(query: str) -> bool:
    """False nếu câu hỏi ngoài phạm vi hoặc có ý định né nghĩa vụ trả nợ."""
    toks = _tokens(query)
    if _any(toks, _OUT_OF_SCOPE_TOPICS) or _any(toks, _EVASION_STRONG):
        return False
    return not (_any(toks, _EVASION_WEAK) and _any(toks, _HOW_CUES))
