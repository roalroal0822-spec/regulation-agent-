"""조문 문장 도우미: 조사 고르기, 표준 조문 틀, 유사 조문 고쳐 쓰기."""
import re

from core import regparse
from core.textutil import similarity

PH = "【", "】"  # 사람이 채울 자리 표시


def has_batchim(word):
    w = re.sub(r"[\s\"'”’」)\]】]+$", "", word or "")
    if not w:
        return False
    c = w[-1]
    if "가" <= c <= "힣":
        return (ord(c) - 0xAC00) % 28 != 0
    if c.isdigit():
        return c in "013678"  # 영, 일, 삼, 육, 칠, 팔
    return False


def rieul(word):
    w = re.sub(r"[\s\"'”’」)\]】]+$", "", word or "")
    if not w:
        return False
    c = w[-1]
    if "가" <= c <= "힣":
        return (ord(c) - 0xAC00) % 28 == 8
    return c in "178"  # 일, 칠, 팔


def josa(word, pair):
    """pair: '을/를', '은/는', '이/가', '과/와', '으로/로'"""
    a, b = pair.split("/")
    if pair == "으로/로":
        return word + ("로" if (not has_batchim(word) or rieul(word)) else "으로")
    return word + (a if has_batchim(word) else b)


def placeholders(text):
    return re.findall(r"【([^】]*)】", text or "")


# 표준 조문 틀: 제목 키워드 -> 본문. {head} 기관장, {self} 이 규정/지침, {x} 대상 이름
TEMPLATES = [
    (("목적",), "이 {self}은 {purpose}에 필요한 사항을 정함을 목적으로 한다."),
    (("정의",), "이 {self}에서 사용하는 용어의 뜻은 다음과 같다.\n1. \"{x}\"란 【뜻을 적으세요】을 말한다."),
    (("취소", "철회"), "① {head}은 다음 각 호의 어느 하나에 해당하는 경우에는 {x}을 취소할 수 있다.\n1. 거짓이나 그 밖의 부정한 방법으로 {x}을 받은 경우\n2. {x}의 조건이나 이 {self}을 위반한 경우\n3. 【그 밖의 취소 사유】\n② {head}은 제1항에 따라 취소하려는 경우에는 미리 상대방에게 의견을 제출할 기회를 주어야 한다."),
    (("감면",), "사용료의 감면 대상과 감면율은 별표 【○】과 같다."),
    (("반환", "환불"), "① 이미 낸 사용료는 반환하지 아니한다. 다만, 다음 각 호의 어느 하나에 해당하는 경우에는 그 전부 또는 일부를 반환할 수 있다.\n1. 공단의 사정으로 사용하지 못한 경우: 전액\n2. 사용 예정일 【○】일 전까지 취소한 경우: 【100분의 ○】"),
    (("위원회",), "① 【심의 사항】을 심의하기 위하여 【위원회 이름】을 둔다.\n② 위원회는 위원장 1명을 포함하여 【5명 이상 9명 이하】의 위원으로 구성한다.\n③ 그 밖에 위원회 운영에 필요한 사항은 {head}이 정한다."),
    (("개인정보", "비밀"), "{x} 업무를 수행하는 사람은 업무상 알게 된 개인정보를 업무 목적 외로 이용하거나 다른 사람에게 제공해서는 아니 된다."),
    (("손해", "배상"), "{x}의 이용자가 고의 또는 과실로 시설을 훼손한 경우에는 원상복구하거나 그 손해를 배상하여야 한다."),
    (("준수", "의무"), "{x}의 이용자는 다음 각 호의 사항을 지켜야 한다.\n1. 【준수사항】\n2. 직원의 안내와 안전수칙에 따를 것"),
    (("점검", "보고", "평가"), "{head}은 {x}의 운영 실태를 【매년】 점검하고, 그 결과를 【보고 대상】에게 보고하여야 한다."),
    (("서식",), "이 {self}의 시행에 필요한 서식은 별지와 같다."),
    (("세부", "위임", "시행"), "이 {self}에서 정한 사항 외에 {x}의 운영에 필요한 세부 사항은 {head}이 따로 정한다."),
    (("선정", "심사", "추첨"), "① {head}은 신청자가 【정원】을 초과하는 경우에는 【공개 추첨 또는 심사】로 대상자를 선정한다.\n② 선정 기준과 방법은 {head}이 정하여 미리 공고한다."),
    (("사용료", "요금", "수수료", "납부"), "① {x}의 사용료는 【근거 규정 또는 기준】에 따른 금액으로 한다.\n② 사용료는 허가를 받은 날부터 【○】일 이내에 납부하여야 한다."),
    (("허가", "승인"), "① {head}은 제【○】조에 따른 신청이 다음 각 호의 어느 하나에 해당하는 경우에는 허가하지 아니할 수 있다.\n1. 공공질서나 미풍양속을 해칠 우려가 있는 경우\n2. 시설의 보존이나 운영에 지장이 있다고 인정되는 경우\n② {head}은 허가를 하는 경우 필요한 조건을 붙일 수 있다."),
    (("신청",), "① {x}을 하려는 자는 【○】일 전까지 별지 제【○】호서식의 신청서를 {head}에게 제출하여야 한다.\n② {head}은 제1항에 따른 신청을 받은 날부터 【○】일 이내에 그 결과를 신청인에게 알려야 한다."),
    (("적용",), "{x}에 관하여 다른 규정에 특별한 규정이 있는 경우를 제외하고는 이 {self}에서 정하는 바에 따른다."),
]


def fill(tpl, head="이사장", self_word="규정", x="", purpose=""):
    vals = {"{head}": head, "{self}": self_word, "{x}": x or "【대상】", "{purpose}": purpose or "【목적】"}
    s = tpl
    for key, val in vals.items():
        for pair in ("은/는", "이/가", "을/를", "과/와"):
            s = s.replace(key + pair.split("/")[0], josa(val, pair))
        s = s.replace(key, val)
    return s


def template_for(title, **kw):
    for keys, tpl in TEMPLATES:
        if any(k in title for k in keys):
            return fill(tpl, **kw)
    return f"{title}에 관하여 필요한 사항은 【내용을 작성하세요】."


def adapt(article, head="이사장", self_word="규정", from_word=None):
    """다른 규정·조례의 조문을 대상 규정 표현으로 고쳐 쓴다(주체·자기지칭)."""
    text = "\n".join(article.body_lines()).strip()
    text = re.sub(r"^\s+", "", text, flags=re.M)
    for h in ("구청장", "시장", "군수", "도지사", "장관", "사장"):
        if h != head and h not in head:
            text = re.sub(rf"(?<![가-힣]){h}(?=[은는이가의에을를과와(\s])", head, text)
    text = re.sub(r"\(관리를 위탁한 경우에는[^)]*\)", "", text)
    if from_word and from_word != self_word:
        text = re.sub(rf"이\s*{from_word}", f"이 {self_word}", text)
    # 조사 맞추기(주체가 바뀐 경우)
    text = re.sub(rf"{head}(은|는)", josa(head, "은/는"), text)
    text = re.sub(rf"{head}(이|가)(?=\s)", josa(head, "이/가"), text)
    return text


def similar_article(lib, title, exclude_entry=None, min_sim=0.45):
    """제목이 비슷한 조문을 규정집(기관 규정·상위 법령)에서 찾는다."""
    best = None
    t = re.sub(r"\s|의", "", title)
    for e in lib.regs():
        for a in e.reg.articles:
            if a.deleted or not a.title:
                continue
            s = similarity(t, re.sub(r"\s|의", "", a.title))
            if t and t in re.sub(r"\s|의", "", a.title):
                s = max(s, 0.8)
            if exclude_entry is not None and e is exclude_entry:
                s -= 0.05
            if s >= min_sim and (best is None or s > best[0]):
                best = (s, e, a)
    return best


def article_from_text(no, sub, title, body, chapter=""):
    text = f"{regparse.alabel(no, sub)}({title}) {body.strip()}" if title else f"{regparse.alabel(no, sub)} {body.strip()}"
    reg = regparse.parse_text("임시\n" + text)
    a = reg.articles[0] if reg.articles else regparse.Article(no=no, sub=sub, title=title)
    a.chapter = chapter
    return a
