"""개인정보 자동 가림: 화면에 적은 글을 저장하거나 모델에 넘기기 전에 개인정보를 ○로 바꾼다.

가리는 것: 주민등록번호·외국인등록번호, 휴대전화·일반전화, 이메일, 카드번호, 계좌번호, 여권번호.
날짜(2026-10-27), 수치(4m, 33℃, 198,178kWh), 조문 번호는 가리지 않는다.
"""
import re

RULES = [
    ("주민등록번호", re.compile(r"(?<!\d)\d{6}\s*[-\s]?\s*[1-8]\d{6}(?!\d)")),
    ("카드번호", re.compile(r"(?<!\d)\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}(?!\d)")),
    ("휴대전화", re.compile(r"(?<!\d)01[016789][-. ]?\d{3,4}[-. ]?\d{4}(?!\d)")),
    ("전화번호", re.compile(r"(?<!\d)0(?:2|[3-6][1-5]|70|50\d)[-. )]\s?\d{3,4}[-. ]\d{4}(?!\d)")),
    ("이메일", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("여권번호", re.compile(r"(?<![A-Za-z0-9])[MSRODG]\d{8}(?![A-Za-z0-9])")),
    # 계좌: 숫자 묶음 3~4개를 하이픈으로 이은 10~16자리. 날짜(4-2-2)는 자리수가 모자라 걸리지 않는다
    ("계좌번호", re.compile(r"(?<![\d-])\d{2,6}-\d{2,6}-\d{2,8}(?:-\d{1,3})?(?![\d-])")),
]


def _digits(s):
    return sum(ch.isdigit() for ch in s)


# 업무 연락망처럼 전화·이메일이 업무 내용인 자료에서 가릴 것(신원·금융 정보)
SENSITIVE = ("주민등록번호", "카드번호", "여권번호", "계좌번호")


def mask(text, only=None):
    """(가린 글, 가린 종류 목록). 바꾼 자리는 같은 길이의 ○가 아니라 '○○○'로 줄여 표시한다.
    only를 주면 그 종류만 가린다(예: only=SENSITIVE)."""
    if not text:
        return text, []
    found = []
    out = text
    for kind, rx in RULES:
        if only and kind not in only:
            continue
        def rep(m, kind=kind):
            s = m.group()
            if kind == "계좌번호" and (not 10 <= _digits(s) <= 16 or any(k_ in ("휴대전화", "전화번호") and r.fullmatch(s) for k_, r in RULES)):
                return s  # 전화번호 모양은 계좌로 보지 않음(only로 전화를 빼고 가릴 때)
            found.append(kind)
            return "○○○"
        out = rx.sub(rep, out)
    return out, sorted(set(found), key=found.index)


def mask_fields(d, keys, only=None):
    """사전에서 keys 항목을 가리고, 가린 종류를 모아 돌려준다."""
    kinds = []
    for k in keys:
        if isinstance(d.get(k), str):
            d[k], ks = mask(d[k], only)
            kinds += [x for x in ks if x not in kinds]
    return kinds
