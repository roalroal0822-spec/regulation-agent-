"""제정: 제정 목적 + 상위법 위임 조항 -> 조문 체계, 표준 조문 초안, 유사 규정 비교표, 위임 범위 검토."""
import re

from core import regparse
from core.llm import LLMError
from core.textutil import similarity, strip_josa, tokens

import drafting
from library import kind_of, SELF_WORD

# (키, 제목 틀, 기본 포함 여부를 정하는 낱말, 장)
MATTERS = [
    ("목적", "목적", None, "총칙"),
    ("정의", "정의", None, "총칙"),
    ("적용", "적용 범위", r"적용|범위|대상", "총칙"),
    ("신청", "{core} 신청", r"신청|접수", "본문"),
    ("선정", "대상자 선정", r"선정|추첨|심사|배정", "본문"),
    ("허가", "{core}의 허가", r"허가|승인", "본문"),
    ("사용료", "사용료 납부", r"사용료|요금|수수료|납부", "본문"),
    ("감면", "사용료의 감면", r"감면|할인", "본문"),
    ("반환", "사용료의 반환", r"반환|환불", "본문"),
    ("취소", "{core}의 취소", r"취소|철회|제재", "본문"),
    ("준수", "이용자 준수사항", r"준수|의무|안전", "보칙"),
    ("위원회", "{core}심의위원회", r"위원회|심의", "보칙"),
    ("점검", "운영 점검", r"점검|평가|보고|실태", "보칙"),
    ("개인정보", "개인정보 보호", r"개인정보|비밀", "보칙"),
    ("세부", "세부 사항", None, "보칙"),
]
ALWAYS = {"목적", "정의", "세부"}
MATTER_KW = {"목적": "목적", "정의": "정의", "적용": "적용", "신청": "신청", "선정": "선정|추첨", "허가": "허가",
             "사용료": "사용료|요금", "감면": "감면", "반환": "반환", "취소": "취소", "준수": "준수",
             "위원회": "위원회", "점검": "점검", "개인정보": "개인정보", "세부": "세부|위임|시행"}


def _ns(t):
    return re.sub(r"\s|의", "", t or "")


def match_article(entries, matter, a_title, prefer_org=False):
    """같은 주제(제목 핵심어)의 조문 중 제목이 가장 비슷한 것."""
    kw = MATTER_KW.get(matter)
    best = None
    for e in entries:
        for a in e.reg.articles:
            if a.deleted or not a.title or not kw or not re.search(kw, a.title):
                continue
            sc = similarity(_ns(a_title), _ns(a.title)) + (0.2 if prefer_org and e.layer == "org" else 0)
            if best is None or sc > best[0]:
                best = (sc, e, a)
    return (best[1], best[2]) if best else None
RIGHTS = re.compile(r"제한|금지|과태료|징수|의무|배상|박탈|정지")
CHAPTER_NAMES = {"총칙": "총칙", "본문": "{core} 운영", "보칙": "보칙"}


def core_of(title, org=""):
    t = title.replace(org, "").strip()
    t = re.sub(r"\s*(?:운영|관리|처리|시행)?\s*(?:에\s*관한)?\s*(?:규정|지침|세칙|요령|기준|조례|규칙)$", "", t).strip()
    t = re.sub(r"\s*(?:운영|관리)$", "", t).strip()
    return t or title


def suggest_matters(purpose, title=""):
    text = f"{title} {purpose}"
    return [k for k, _, kw, _ in MATTERS if k in ALWAYS or (kw and re.search(kw, text))]


def purpose_phrase(purpose):
    p = re.sub(r"\s+", " ", (purpose or "").strip().rstrip("."))
    p = re.sub(r"\s*(?:위하여|위해서|위해|위함)$", "", p)
    p = re.sub(r"\s*(?:을|를)?\s*(?:정하기|정하려고\s*함|정하려\s*함|정하고자\s*함|정하기\s*위함|정하기\s*위해|정한다|정함|정하려고\s*합니다|정하려고\s*해|마련하려고\s*함|마련하고자\s*함|마련|규정하려고\s*함|규정함)$", "", p)
    p = re.sub(r"(?:\s*에\s*관하여|\s*에\s*관한)?\s+(?:필요한\s*)?사항$|\s*(?:에\s*관하여|에\s*관한)\s*(?:필요한\s*)?사항$", "", p)
    p = re.sub(r"\s*(?:에\s*관하여|에\s*관한|을|를)$", "", p)
    return p.strip() or "【목적】"


def purpose_clause(purpose):
    """제1조에 들어갈 '…에 관하여 필요한 사항' 구절. '…운영하기'처럼 동사로 끝나면 '…운영하는 데 필요한 사항'."""
    p = purpose_phrase(purpose)
    if p.endswith("하기") and len(p) > 2:
        return f"{p[:-1]}는 데 필요한 사항"
    return f"{p}에 관하여 필요한 사항"


def _ref_placeholders(articles):
    """'제【○】조에 따른 신청'처럼 초안 안에서 가리키는 조가 정해져 있으면 번호를 넣는다."""
    by = {a["matter"]: a["key"] for a in articles}
    for a in articles:
        if "신청" in by and by["신청"] != a["key"]:
            a["text"] = a["text"].replace("제【○】조에 따른 신청", f"제{by['신청']}조에 따른 신청")


def _definitions(self_word, core_word, others):
    """정의 조: 본문에 실제로 쓰인 용어만 정의한다."""
    terms = [core_word] + [t for t in ("이용자",) if t != core_word and any(t in x for x in others)]
    ran = lambda w: "이란" if drafting.has_batchim(w) else "란"
    if len(terms) == 1:
        return f"이 {self_word}에서 \"{core_word}\"{ran(core_word)} 【뜻을 적으세요】을 말한다."
    return f"이 {self_word}에서 사용하는 용어의 뜻은 다음과 같다.\n" + "\n".join(
        f"{i}. \"{t}\"{ran(t)} 【뜻을 적으세요】을 말한다." for i, t in enumerate(terms, 1))


def parse_delegation(text):
    """위임 조항 문장에서 누구에게, 무엇을, 어떤 형식으로 맡겼는지 읽는다."""
    t = re.sub(r"\s+", " ", text or "")
    who, scope, form = "", "", ""
    m = re.search(r"(.+?)(?:에\s*관하여|에\s*관한|에)?\s*필요한\s*(?:세부\s*)?사항(?:은|을)\s*(.+?)(?:으로|로|이|가)?\s*정(?:한다|할 수 있다|하여야 한다)", t)
    if m:
        scope = re.sub(r"^.*?(?:은|는)\s", "", m.group(1)).strip()
        who = m.group(2).strip()
        if not scope:
            scope = m.group(1).strip()
    m2 = re.match(r"^(.+?)(?:은|는)\s+(.+?)(?:에\s*필요한|에\s*관한)\s*(?:세부\s*)?사항을\s*정할\s*수\s*있다", t)
    if m2:
        who, scope = m2.group(1).strip(), m2.group(2).strip()
    if re.search(r"규칙", who):
        form = "규칙"
    elif re.search(r"이사장|사장|기관장|장이", who):
        form = "기관장이 정하는 규정·지침"
    elif re.search(r"조례", who):
        form = "조례"
    return {"who": who, "scope": scope, "form": form, "text": t}


def _ref_article(lib, matter, a_title):
    regs = [e for e in lib.regs("org") if "규정관리" not in e.title]
    return match_article(regs, matter, a_title, prefer_org=True)


def _prefill_numbers(body, ref_text):
    """틀의 【○】 숫자 자리를 유사 조문의 값으로 미리 채운다(확인 필요 표시는 유지)."""
    if not ref_text:
        return body
    for pat in (r"(\d+)일\s*전까지", r"(\d+)일\s*이내"):
        m = re.search(pat, ref_text)
        if m:
            unit = pat.split(")")[1].replace("\\s*", " ").replace("일", "")
            body = re.sub(r"【○】(일\s*" + unit.strip() + ")", lambda x: f"【{m.group(1)}】" + x.group(1), body, count=1)
    return body


def draft(lib, title, purpose, delegation=None, matters=None, head="이사장", org=""):
    """반환: {reg_text, articles[{key,title,chapter,text,ref,basis}], structure, notes, kind}"""
    kind = kind_of(title)
    self_word = SELF_WORD.get(kind, "규정")
    core = core_of(title, org)
    core_word = core.split()[-1] if core.split() else core
    matters = matters or suggest_matters(purpose, title)
    matters = [m for m, *_ in MATTERS if m in set(matters) | ALWAYS]
    deleg = delegation or {}
    basis = ""
    if deleg.get("law") and deleg.get("label"):
        basis = f"「{deleg['law']}」 {deleg['label']}에 따라 "
    upper = lib.regs("upper")
    articles, notes = [], []
    for key in matters:
        _, ttl, _, part = next(x for x in MATTERS if x[0] == key)
        a_title = ttl.format(core=core_word)
        ref = None
        upper_basis = None
        if key == "목적":
            body = f"{drafting.josa('이 ' + self_word, '은/는')} {basis}{purpose_clause(purpose)}을 정함을 목적으로 한다."
        elif key == "정의":
            ra = _ref_article(lib, "정의", "정의")
            body = ""  # 다른 조를 다 만든 뒤 쓰인 용어만 정의한다
            if ra:
                ref = {"reg": ra[0].short, "label": ra[1].label, "title": ra[1].title, "text": ra[1].plain}
        elif key == "세부":
            body = f"이 {self_word}에서 정한 사항 외에 {core}의 운영에 필요한 세부 사항은 {head}이 따로 정한다."
        else:
            body = drafting.template_for(a_title, head=head, self_word=self_word, x=core_word)
            ra = _ref_article(lib, key, a_title)
            if ra:
                ref = {"reg": ra[0].short, "label": ra[1].label, "title": ra[1].title, "text": ra[1].plain}
                body = _prefill_numbers(body, ra[1].plain)
            # 상위 법령에 같은 주제 조문이 있으면 근거로 인용
            ua = match_article(upper, key, a_title)
            if ua:
                upper_basis = {"law": ua[0].title, "label": ua[1].label, "title": ua[1].title}
            if upper_basis and key in ("사용료", "감면"):
                body = re.sub(r"【근거 규정 또는 기준】", f"「{upper_basis['law']}」 {upper_basis['label']}", body)
                if key == "감면":
                    body = f"사용료의 감면 대상과 감면율은 「{upper_basis['law']}」 {upper_basis['label']}에 따른다."
        articles.append({"matter": key, "title": a_title, "part": part, "text": body, "ref": ref, "upper": upper_basis})
    for i, a in enumerate(articles, 1):
        a["key"] = str(i)
    _ref_placeholders(articles)
    for a in articles:
        if a["matter"] == "정의":
            a["text"] = _definitions(self_word, core_word, [x["text"] for x in articles if x is not a])
    # 장 나누기: 조가 9개 이상이면 총칙·본문·보칙
    use_ch = len(articles) >= 9
    lines = [title if not org or org in title else f"{org} {title}"]
    ch_no, cur = 0, None
    for i, a in enumerate(articles, 1):
        a["key"] = str(i)
        if use_ch and a["part"] != cur:
            ch_no += 1
            cur = a["part"]
            lines.append(f"제{ch_no}장 {CHAPTER_NAMES[cur].format(core=core)}")
        a["chapter"] = f"제{ch_no}장 {CHAPTER_NAMES[cur].format(core=core)}" if use_ch else ""
        body_lines = a["text"].split("\n")
        lines.append(f"제{i}조({a['title']}) {body_lines[0]}")
        lines += body_lines[1:]
    lines += ["부칙", f"{drafting.josa('이 ' + self_word, '은/는')} 공포한 날부터 시행한다."]
    text = "\n".join(lines)
    n_ph = sum(len(drafting.placeholders(a["text"])) for a in articles)
    if n_ph:
        notes.append(f"【 】로 표시한 {n_ph}곳은 기관 사정에 맞게 채워야 합니다. 숫자가 들어 있는 곳은 유사 규정 값을 미리 넣은 것입니다.")
    return {"title": lines[0], "kind": kind, "self_word": self_word, "core": core, "articles": articles,
            "reg_text": text, "notes": notes, "matters": matters, "chapters": use_ch}


def rebuild_text(d):
    """화면에서 조문을 고친 뒤 전체 글을 다시 만든다."""
    lines = [d["title"]]
    cur = None
    for i, a in enumerate(d["articles"], 1):
        a["key"] = str(i)
    _ref_placeholders(d["articles"])
    for i, a in enumerate(d["articles"], 1):
        if a.get("chapter") and a["chapter"] != cur:
            cur = a["chapter"]
            lines.append(cur)
        body = a["text"].strip().split("\n")
        lines.append(f"제{i}조({a['title']}) {body[0]}")
        lines += body[1:]
    lines += ["부칙", f"{drafting.josa('이 ' + d['self_word'], '은/는')} 공포한 날부터 시행한다."]
    d["reg_text"] = "\n".join(lines)
    return d


def compare_similar(lib, d, k=2):
    """유사 규정 비교표: 초안 조문 주제별로 유사 규정의 해당 조문을 나란히 보여준다."""
    query = f"{d['title']} {d['core']} " + " ".join(a["title"] for a in d["articles"])
    bm = {e.id: sc for e, sc in lib.search_regs(query, k=50)}
    cands = []
    for e in lib.regs("org"):
        if "규정관리" in e.title:
            continue
        hits = sum(1 for a in d["articles"] if a["matter"] not in ("목적", "정의", "세부") and match_article([e], a["matter"], a["title"]))
        if hits:
            cands.append((hits, bm.get(e.id, 0), e))
    cands.sort(key=lambda x: (-x[0], -x[1]))
    regs = [e for _, _, e in cands[:k]]
    rows = []
    for a in d["articles"]:
        row = {"title": a["title"], "draft": a["text"], "cells": []}
        for e in regs:
            m = match_article([e], a["matter"], a["title"])
            row["cells"].append({"label": m[1].label, "title": m[1].title, "text": m[1].plain} if m else None)
        rows.append(row)
    return {"regs": [{"id": e.id, "title": e.short} for e in regs], "rows": rows}


GENERIC = {"운영", "관리", "처리", "시행", "지침", "규정", "세칙", "요령", "기준", "규칙", "조례", "사항", "필요", "세부",
           "공단", "기관", "업무", "관한", "대한", "이사장", "구청장", "위탁", "위탁받은"}
RX_WORDING = re.compile(r"필요한\s*(?:세부\s*)?사항(?:은|을)\s*.{0,30}?정(?:한다|할\s*수\s*있다|하여야\s*한다)|정하는\s*바에\s*따른다|위임한다|따로\s*정한다")


def subject_words(d):
    """만들려는 규정의 주제 낱말(이름의 핵심어). 예: '체육시설 정기대관' -> [체육시설, 정기대관]"""
    words = [strip_josa(w) for w in re.findall(r"[가-힣A-Za-z0-9]+", d.get("core") or core_of(d.get("title", "")))]
    return [w for w in words if len(w) >= 2 and w not in GENERIC]


def _subject_match(words, text):
    t = re.sub(r"\s", "", text or "")
    return [w for w in words if w in t]


def _units(article):
    """조문의 항 단위 (표시, 글). 항이 없으면 조 전체."""
    paras = [p for p in article.paras if p.text.strip() or p.items]
    if len(paras) <= 1:
        return [(article.label, article.plain)]
    out = []
    for p in paras:
        text = " ".join([p.text] + [it.text for it in p.items])
        out.append((f"{article.label}제{p.no}항" if p.no else article.label, text))
    return out


def _wording(text):
    info = parse_delegation(text)
    return info if (info["who"] or info["scope"] or RX_WORDING.search(info["text"])) else None


def _scope_text(e, info, unit_text):
    """위임 대상 범위 글. '이 조례의 시행'처럼 법령 자체를 가리키면 법령 이름을 범위로 본다."""
    sc = info.get("scope") or unit_text
    if re.search(r"^이\s*(?:조례|법|규칙|영|규정)(?:의)?\s*(?:시행|운영)?$", sc.strip()) or not info.get("scope"):
        sc = f"{sc} {e.title}"
    return sc


def delegation_candidates(lib, d, exclude=None):
    """상위 법령에서 위임 문구가 있고 주제가 맞는 조·항 (형식이 맞는 것 먼저)."""
    words = subject_words(d)
    out = []
    for e in lib.regs("upper"):
        for a in e.reg.articles:
            if a.deleted:
                continue
            for label, text in _units(a):
                if exclude and (e.id, label) == exclude:
                    continue
                info = _wording(text)
                if not info:
                    continue
                hit = _subject_match(words, _scope_text(e, info, text))
                if not hit:
                    continue
                form_ok = not (info["form"] == "규칙" and d.get("kind") != "규칙")
                out.append((form_ok, len(hit), {"law": e.short, "entry_id": e.id, "key": a.key, "label": label,
                                                 "text": text, "form_ok": form_ok}))
    out.sort(key=lambda x: (not x[0], -x[1]))
    return [c for *_, c in out]


def _cand_text(cands, k=3):
    if not cands:
        return " 규정집의 상위 법령에서 주제가 맞는 위임 조항을 찾지 못했습니다."
    return " 주제가 맞는 위임 조항 후보: " + "; ".join(
        f"{c['law']} {c['label']}(\"{c['text'][:60]}{'…' if len(c['text']) > 60 else ''}\")" + ("" if c["form_ok"] else " — 규칙으로 정하도록 한 위임이라 형식 확인 필요")
        for c in cands[:k]) + "."


def check_delegation(lib, d, deleg):
    """위임 범위 검토: 인용한 상위법 조항이 (1) 위임 문구를 갖고 (2) 만들려는 규정의 주제를 맡겼는지,
    위임 형식, 권리·의무 조항의 상위법 근거."""
    items = []
    info = {"who": "", "scope": "", "form": ""}
    words = subject_words(d)
    subj = ", ".join(words) or d.get("core", "")
    if not deleg or not deleg.get("text"):
        items.append({"level": "주의", "item": "위임 근거", "result": "상위법 위임 조항이 지정되지 않았습니다. 근거 조항을 확인하세요." + _cand_text(delegation_candidates(lib, d))})
    else:
        e = lib.get(deleg["entry_id"]) if deleg.get("entry_id") else None
        a = e.reg.get(deleg["key"]) if e and deleg.get("key") else None
        cited = f"{deleg.get('law', '')} {deleg.get('label', '')}".strip()
        # 조 전체를 인용했으면 위임 문구가 있는 항을 찾는다
        units = _units(a) if (a and not deleg.get("para")) else [(deleg.get("label", ""), deleg["text"])]
        found = [(lb, tx, _wording(tx)) for lb, tx in units]
        found = [x for x in found if x[2]]
        if found and e is not None:
            found.sort(key=lambda x: -len(_subject_match(words, _scope_text(e, x[2], x[1]))))
        if not found:
            what = f"({a.title})" if a and a.title else ""
            items.append({"level": "근거 없음", "item": "위임 근거",
                          "result": f"{cited}{what}에는 다른 규정에 정하도록 맡기는 위임 문구(예: '…에 필요한 사항은 …이 정한다')가 없습니다. "
                                    f"내용: \"{deleg['text'][:80]}{'…' if len(deleg['text']) > 80 else ''}\"." +
                                    _cand_text(delegation_candidates(lib, d))})
        else:
            label, text, info = found[0]
            scope_txt = _scope_text(e, info, text) if e is not None else (info["scope"] or text)
            hit = _subject_match(words, scope_txt)
            unit = label if label and label != deleg.get("label") else deleg.get("label", "")
            where = f"{deleg.get('law', '')} {unit}".strip()
            if hit or not words:
                items.append({"level": "확인", "item": "위임 근거",
                              "result": f"{where}: {text} — 위임 문구가 있고, 위임 대상('{info['scope'] or scope_txt}')이 만들려는 {d['kind']}의 주제({', '.join(hit) or subj})와 맞습니다."})
            else:
                excl = (e.id, label) if e is not None else None
                items.append({"level": "확인 필요", "item": "위임 근거",
                              "result": f"{where}: {text} — 위임 문구는 있으나 위임 대상('{info['scope'] or text[:40]}')이 만들려는 {d['kind']}의 주제({subj})와 맞지 않습니다. 이 조항이 근거가 되는지 확인하세요." +
                                        _cand_text(delegation_candidates(lib, d, exclude=excl))})
            if info["who"]:
                ok = not (info["form"] == "규칙" and d["kind"] not in ("규칙",))
                items.append({"level": "확인" if ok else "주의", "item": "위임 형식",
                              "result": f"위임받는 자: {info['who']} / 만들려는 문서: {d['kind']}" + ("" if ok else " (규칙으로 정하도록 위임되어 있어 형식이 맞지 않을 수 있음)")})
            if info["scope"]:
                items.append({"level": "참고", "item": "위임 범위", "result": f"'{info['scope']}' 범위 안에서 정해야 합니다."})
    for a in d["articles"]:
        if a["matter"] in ("목적", "정의", "세부"):
            continue
        if a["matter"] in ("사용료", "감면", "취소", "준수") or RIGHTS.search(a["title"]):
            if a.get("upper"):
                items.append({"level": "확인", "item": f"제{a['key']}조({a['title']})",
                              "result": f"주민 권리·의무 관련 조항 — 상위 근거: 「{a['upper']['law']}」 {a['upper']['label']}({a['upper']['title']})"})
            else:
                items.append({"level": "주의", "item": f"제{a['key']}조({a['title']})",
                              "result": "주민 권리를 제한하거나 의무를 지우는 내용이 있으나 상위 법령에서 같은 주제 조문을 찾지 못했습니다. 위임 범위를 넘지 않는지 확인하세요."})
    return {"delegation": info, "items": items}


ENACT_SYSTEM = """당신은 공공기관 규정 입안 실무자입니다. 주어진 조문 체계(조 제목 목록)에 맞춰 각 조의 본문을 작성합니다.
규칙: 조 제목과 순서는 바꾸지 않습니다. 항은 ①②③, 호는 1. 2. 로 씁니다. 숫자·기한처럼 기관이 정할 값은 【 】로 표시합니다.
상위법 위임 범위를 넘는 의무·제한은 쓰지 않습니다. 출력: {"articles":[{"title":"조 제목","text":"본문"}]}"""


def llm_refine(llm, d, purpose, deleg):
    refs = "\n".join(f"- {a['title']}: 참고 {a['ref']['reg']} {a['ref']['label']} {a['ref']['text'][:150]}" for a in d["articles"] if a.get("ref"))
    outline = "\n".join(f"제{i}조({a['title']}) 초안: {a['text']}" for i, a in enumerate(d["articles"], 1))
    user = (f"[문서 이름] {d['title']}\n[제정 목적] {purpose}\n[위임 조항] {(deleg or {}).get('law', '')} {(deleg or {}).get('label', '')} {(deleg or {}).get('text', '')}\n"
            f"[유사 규정 참고]\n{refs}\n[조문 체계와 기본 초안]\n{outline}")
    out = llm.json(ENACT_SYSTEM, user, max_tokens=3500)
    arts = out.get("articles") or []
    used = 0
    for a, m in zip(d["articles"], arts):
        if isinstance(m, dict) and str(m.get("text") or "").strip() and len(str(m["text"])) < 1500:
            if a["matter"] == "목적" and "목적" not in str(m["text"]):
                continue
            a["text"] = re.sub(r"^제\d+조(?:\([^)]*\))?\s*", "", str(m["text"]).strip())
            a["by_model"] = True
            used += 1
    return used


def make(lib, title, purpose, delegation=None, matters=None, head="이사장", org="", llm=None):
    d = draft(lib, title, purpose, delegation, matters, head, org)
    d["engine"] = "규칙"
    if llm is not None and llm.enabled:
        try:
            n = llm_refine(llm, d, purpose, delegation)
            if n:
                d["engine"] = "모델"
                d["notes"].append(f"모델({llm.label})이 {n}개 조의 본문을 다듬었습니다. 조 제목·순서는 규칙 초안을 유지합니다.")
                rebuild_text(d)
        except LLMError as e:
            d["notes"].append(f"모델 호출 실패로 표준 조문 초안을 썼습니다: {e}")
    d["similar"] = compare_similar(lib, d)
    d["delegation_check"] = check_delegation(lib, d, delegation)
    top = d["delegation_check"]["items"][0] if d["delegation_check"]["items"] else None
    if top and top["item"] == "위임 근거" and top["level"] in ("근거 없음", "확인 필요"):
        d["notes"].append(f"위임 근거 검토 결과 '{top['level']}'입니다. 제1조의 위임 근거 표기를 확인하세요(위임 범위 검토 참고).")
    return d


def to_regulation(d):
    return regparse.parse_text(d["reg_text"])
