"""한국어 텍스트 처리 도우미: 토큰화, 날짜·연락처 추출, 유사도."""
import re
from datetime import date, timedelta

_WORD = re.compile(r"[가-힣]+|[A-Za-z]+|\d+")

# 검색 품질을 떨어뜨리는 조사·어미 (단순 접미 제거)
_JOSA = ("에서는", "으로는", "에게서", "까지는", "부터는", "이라도", "으로서", "으로써",
         "에서", "에게", "으로", "까지", "부터", "보다", "처럼", "마다", "이나", "라도",
         "하고", "은", "는", "이", "가", "을", "를", "에", "의", "와", "과", "도", "로", "만", "요")


def strip_josa(w):
    if len(w) <= 1 or not re.match(r"[가-힣]", w):
        return w
    for j in _JOSA:
        if w.endswith(j) and len(w) - len(j) >= 2:
            return w[: -len(j)]
    return w


def tokens(text):
    """단어 + 한글 2글자 묶음(bigram). 형태소 분석기 없이도 검색이 되도록 한다."""
    out = []
    for w in _WORD.findall(text.lower()):
        w2 = strip_josa(w)
        out.append(w2)
        if re.match(r"[가-힣]", w2) and len(w2) > 2:
            out.extend(w2[i:i + 2] for i in range(len(w2) - 1))
    return out


def bigrams(text):
    t = re.sub(r"\s+", "", text)
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else {t}


def similarity(a, b):
    """두 문장의 글자 bigram 자카드 유사도 (0~1)."""
    A, B = bigrams(a), bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def split_sentences(text):
    """문장 나누기. '2026. 3. 16.' 같은 날짜 표기는 자르지 않는다."""
    parts = re.split(r"(?<=[가-힣A-Za-z)\]][.!])\s+|\n+", text)
    return [p.strip() for p in parts if p and p.strip()]


# ---------------------------------------------------------------- 날짜

RE_FULL_DATE = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?")
RE_MD = re.compile(r"(?<![\d.])(\d{1,2})\s*월\s*(\d{1,2})\s*일")
RE_MD_DOT = re.compile(r"(?<![\d.])(\d{1,2})\s*[./]\s*(\d{1,2})\s*\.?(?![\d.])")
RE_MONTH = re.compile(r"(?<![\d])(\d{1,2})\s*월(?!\s*\d{1,2}\s*일)")
RE_MONTH_RANGE = re.compile(r"(\d{1,2})\s*[~∼\-]\s*(\d{1,2})\s*월")
RE_EVERY_MONTH = re.compile(r"매월\s*(\d{1,2})\s*일|매월\s*(말일|초|말)|매월")
RE_QUARTER = re.compile(r"(\d)\s*분기|매\s*분기|분기\s*별|분기마다")
RE_HALF = re.compile(r"(상반기|하반기)")
RE_EVERY_YEAR = re.compile(r"매년|연\s*1회|정기|해마다")

QUARTER_MONTHS = {1: [3], 2: [6], 3: [9], 4: [12]}


def extract_dates(text):
    """문장에서 시기 정보를 뽑는다.

    반환: {"months": [..], "date": "YYYY-MM-DD" 또는 None, "day": int 또는 None,
          "recurring": "매월"/"매분기"/"매년"/"", "label": 원문 표현}
    """
    res = {"months": [], "date": None, "day": None, "recurring": "", "label": ""}
    m = RE_FULL_DATE.search(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            res.update(months=[mo], day=d, label=m.group(0))
            try:
                res["date"] = date(y, mo, d).isoformat()
            except ValueError:
                pass
    if not res["months"]:
        m = RE_MD.search(text)
        if m and 1 <= int(m.group(1)) <= 12:
            res.update(months=[int(m.group(1))], day=int(m.group(2)), label=m.group(0))
    if not res["months"]:
        m = RE_MONTH_RANGE.search(text)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if 1 <= a <= 12 and 1 <= b <= 12 and a <= b:
                res.update(months=list(range(a, b + 1)), label=m.group(0))
    if not res["months"]:
        ms = [int(x) for x in RE_MONTH.findall(text) if 1 <= int(x) <= 12]
        if ms:
            enum = re.search(r"\d{1,2}\s*월(?:\s*[,·및]\s*\d{1,2}\s*월)+", text)
            res.update(months=sorted(set(ms)) if enum else [ms[0]], label=RE_MONTH.search(text).group(0))
    m = RE_EVERY_MONTH.search(text)
    if m:
        res["recurring"] = "매월"
        res["months"] = list(range(1, 13))
        if m.group(1):
            res["day"] = int(m.group(1))
        res["label"] = m.group(0)
    else:
        m = RE_QUARTER.search(text)
        if m:
            if m.group(1) and not re.search(r"매\s*분기|분기\s*별|분기마다", text):
                q = int(m.group(1))
                if 1 <= q <= 4 and not res["months"]:
                    res["months"] = QUARTER_MONTHS[q]
                    res["label"] = m.group(0)
            else:
                res["recurring"] = "매분기"
                if not res["months"]:
                    res["months"] = [3, 6, 9, 12]
                res["label"] = m.group(0)
        m = RE_HALF.search(text)
        if m and not res["months"]:
            res["months"] = [6] if m.group(1) == "상반기" else [12]
            res["label"] = m.group(0)
        if not res["recurring"] and RE_EVERY_YEAR.search(text) and res["months"]:
            res["recurring"] = "매년"
    return res


def parse_relative_date(text, today=None):
    """'어제', '지난주 금요일', '10월 5일', '3일 전' 같은 표현을 날짜로 바꾼다."""
    today = today or date.today()
    m = RE_FULL_DATE.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = RE_MD.search(text)
    if m:
        try:
            d = date(today.year, int(m.group(1)), int(m.group(2)))
            if d > today + timedelta(days=180):
                d = date(today.year - 1, d.month, d.day)
            return d
        except ValueError:
            pass
    if "그저께" in text or "그제" in text:
        return today - timedelta(days=2)
    if "어제" in text:
        return today - timedelta(days=1)
    if "오늘" in text:
        return today
    if "내일" in text:
        return today + timedelta(days=1)
    m = re.search(r"(\d+)\s*일\s*전", text)
    if m:
        return today - timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\s*일\s*(후|뒤)", text)
    if m:
        return today + timedelta(days=int(m.group(1)))
    days = "월화수목금토일"
    m = re.search(r"(지난주|이번\s*주|다음\s*주)\s*([월화수목금토일])요일", text)
    if m:
        target = days.index(m.group(2))
        monday = today - timedelta(days=today.weekday())
        if m.group(1) == "지난주":
            monday -= timedelta(days=7)
        elif m.group(1).startswith("다음"):
            monday += timedelta(days=7)
        return monday + timedelta(days=target)
    return None


def add_business_days(d, n, holidays=()):
    cur = d
    step = 1 if n >= 0 else -1
    left = abs(n)
    while left:
        cur += timedelta(days=step)
        if cur.weekday() < 5 and cur not in holidays:
            left -= 1
    return cur


# ---------------------------------------------------------------- 연락처

RE_PHONE = re.compile(r"(?<!\d)(0\d{1,2})[-) .]?(\d{3,4})[- .](\d{4})(?!\d)")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
TITLES = ("주무관", "팀장", "과장", "대리", "주임", "부장", "차장", "사원", "담당자", "국장", "계장",
          "실장", "소장", "본부장", "이사장", "이사", "센터장", "선임", "책임", "연구원", "대표", "감독관",
          "위원", "님")
RE_PERSON = re.compile(r"([가-힣]{2,4})\s?(" + "|".join(TITLES) + r")(?=$|[^가-힣]|이랑|님|께|에게|과|와|이|가|은|는|을|를|의|한테|랑|도)")
ORG_SUFFIX = r"(?:과|팀|실|국|청|구청|시청|공단|공사|센터|협회|재단|위원회|사업소|주민센터|지사|본부|㈜|\(주\)|주식회사|업체|사무소)"
RE_ORG = re.compile(r"((?:[가-힣A-Za-z]+\s)?[가-힣A-Za-z]{1,12}" + ORG_SUFFIX + r")(?![가-힣])")
NOT_NAMES = {"담당", "업무", "관련", "해당", "기관", "부서", "전임", "후임", "각각", "모든", "우리", "다음",
             "이번", "지난", "상기", "공단", "업체", "계약", "시설", "운영", "시청", "구청", "본부", "기타",
             "사업", "총무", "회계", "인사", "예산", "직원", "외부", "내부", "주관", "협력", "확인", "감독", "이내", "이후",
             "이전", "부터", "까지", "결과", "보고", "통보", "참고"}


def find_phones(text):
    return ["-".join(m.groups()) for m in RE_PHONE.finditer(text)]


def find_emails(text):
    return RE_EMAIL.findall(text)


def find_people(text):
    out = []
    for m in RE_PERSON.finditer(text):
        name, title = m.group(1), m.group(2)
        if name in NOT_NAMES or any(name.endswith(x) for x in ("팀", "과", "실", "부", "청", "단")):
            continue
        out.append((name, title, m.start()))
    return out


def find_orgs(text):
    out = []
    for m in RE_ORG.finditer(text):
        o = m.group(1).strip()
        if len(o) >= 3 and o not in ("담당과", "해당과", "관련과"):
            out.append(o)
    return out


def normalize_phone(p):
    return re.sub(r"\D", "", p)


HEDGES = re.compile(r"미정|확인\s*필요|확인\s*요망|추정|아마|같음|같다|불확실|예정\s*\(\?\)|\?|TBD|tbd|미확인|여부\s*확인|검토\s*필요")
