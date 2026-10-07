"""개정: 개정 의도 -> 개정 작업 목록 -> 개정안·조 번호 이동표 -> 인용 정정·신구조문대비표·개정문.

개정 작업(op) 형식
  replace      {article, path, find, repl, anchor, all}   글자 바꾸기(조 생략 시 규정 전체)
  retitle      {article, title}
  add_item     {article, para, text}                      호 추가
  add_para     {article, text}                            항 추가
  insert       {after, title, text}                       조 신설
  delete       {article}                                  조 삭제
  appendix_replace {find, repl} / appendix_add {after_find, text}
조 key 는 모두 '현행' 번호 기준이다. 번호 방식(mode): branch(가지 번호) | renumber(번호 이동).
"""
import copy
import re

from core import regparse
from core.llm import LLMError
from core.textutil import tokens

import drafting
from library import RefFinder, norm_name

Q = r"(?:\"[^\"]+\"|'[^']+'|“[^”]+”|‘[^’]+’|「[^」]+」)"
RX_ART = re.compile(r"제(\d+)조(?:의(\d+))?(?:\s*제(\d+)항)?(?:\s*제(\d+)호)?")
RX_REPL = re.compile(rf"(?P<a>{Q}|[^\s'\"“”‘’]+?)\s*에서\s*(?P<b>{Q}|[^\s'\"“”‘’]+?)\s*(?:으로|로)(?=\s|$|[,.]|바|변|줄|늘|조|단|연|상|하|수|고|개|완|강|통|정)")
RX_REPL2 = re.compile(rf"(?P<a>{Q}|[^\s'\"“”‘’]+?)\s*(?:을|를)\s+(?P<b>{Q}|[^\s'\"“”‘’]+?)\s*(?:으로|로)\s*(?:바꾸|바꿔|변경|수정|고치|고쳐|줄이|줄여|늘리|늘려|단축|연장|조정|통일|대체|한다|하고|상향|하향)")
RX_DELETE = re.compile(r"제(\d+)조(?:의(\d+))?\s*(?:\([^)]*\))?\s*(?:을|를|는|은)?\s*(?:삭제|없애|폐지)")
RX_RETITLE = re.compile(rf"제(\d+)조(?:의(\d+))?\s*(?:의)?\s*제목\s*(?:을|를)\s*(?P<t>{Q}|\S+?)\s*(?:으로|로)")
RX_ADD_ITEM = re.compile(rf"제(\d+)조(?:의(\d+))?(?:\s*제(\d+)항)?\s*에\s*(?P<t>{Q})\s*(?:을|를|라는)?\s*(?:내용\s*(?:을|를)\s*)?(?:호|항목|사항|요건|사유|서류)?(?:로|으로)?\s*(?:추가|넣|신설)")
RX_ADD_PARA = re.compile(rf"제(\d+)조(?:의(\d+))?\s*에\s*(?P<t>{Q})\s*(?:을|를|라는)?\s*(?:내용\s*(?:을|를)\s*)?(?:새\s*)?항(?:으로|을)?\s*(?:추가|넣|신설)")
RX_INSERT = re.compile(r"(?:신설|새로\s*(?:만들|넣|추가|두)|추가|넣어|넣고|넣는|만들어|만들고)")
RX_AFTER = re.compile(r"제(\d+)조(?:의(\d+))?\s*(?:\([^)]*\))?\s*(?:의\s*)?(?:다음에|뒤에|다음으로|이후에|다음|뒤)")
RX_EFFECT = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(?:부터)?\s*시행")
STOP = {"기한", "기간", "바꾸", "변경", "줄이", "늘리", "규정", "조문", "내용", "수정", "개정", "하고", "싶어", "해줘", "한다",
        "으로", "에서", "부터", "이내", "까지", "에서는", "관련", "부분", "현행", "있는", "없는", "모두", "전부"}


def unq(s):
    s = (s or "").strip()
    if len(s) >= 2 and s[0] in "\"'“‘「" and s[-1] in "\"'”’」":
        return s[1:-1].strip()
    return s


# ------------------------------------------------------------------ 조문 안 글 단위

def walk(a):
    """조문 안의 글 단위: (path, 표시 위치, 글)"""
    for i, p in enumerate(a.paras):
        ploc = f"제{p.no}항" if p.no else ""
        yield ("p", i), ploc, p.text
        for j, it in enumerate(p.items):
            yield ("i", i, j), f"{ploc}제{it.no}호", it.text
            for k, (sno, st) in enumerate(it.subitems):
                yield ("s", i, j, k), f"{ploc}제{it.no}호{sno}목", st


def get_unit(a, path):
    if path[0] == "p":
        return a.paras[path[1]].text
    if path[0] == "i":
        return a.paras[path[1]].items[path[2]].text
    return a.paras[path[1]].items[path[2]].subitems[path[3]][1]


def set_unit(a, path, text):
    if path[0] == "p":
        a.paras[path[1]].text = text
    elif path[0] == "i":
        a.paras[path[1]].items[path[2]].text = text
    else:
        it = a.paras[path[1]].items[path[2]]
        it.subitems[path[3]] = (it.subitems[path[3]][0], text)


def unit_loc(a, path):
    for p, loc, _ in walk(a):
        if tuple(p) == tuple(path):
            return a.label + loc
    return a.label


def _anchor(text, start, end):
    s = max(0, start - 14)
    e = min(len(text), end + 6)
    # 어절 경계로 넓힌다
    while s > 0 and text[s - 1] != " ":
        s -= 1
    while e < len(text) and text[e] not in " ,.":
        e += 1
    return text[s:e].strip()


def _topic_score(topic_toks, text):
    tt = set(tokens(text))
    return sum(1 for t in topic_toks if t in tt)


# ------------------------------------------------------------------ 개정 의도 읽기(규칙)

def split_clauses(text):
    text = re.sub(r"\s+", " ", text.strip())
    parts = re.split(r"(?<=[이꾸리하치])고\s*,?\s+(?=\S)|[,;]\s+|(?<=[다요])\.\s*|\.\s+|\n|그리고\s+|또한\s+|또\s+", text)
    return [p.strip(" ,.") for p in parts if p and p.strip(" ,.")]


def _key(no, sub):
    return regparse.akey(int(no), int(sub or 0))


def parse_intent(text, entry, lib=None, head="이사장"):
    """자연어 개정 의도를 개정 작업 목록으로 바꾼다. 반환: (ops, notes)"""
    reg = entry.reg
    ops, notes = [], []
    effective = None
    m = RX_EFFECT.search(text)
    if m:
        effective = f"{m.group(1)}년 {int(m.group(2))}월 {int(m.group(3))}일"
    for cl in split_clauses(text):
        if RX_EFFECT.search(cl) and not RX_REPL.search(cl):
            continue
        op = None
        # 조 삭제
        m = RX_DELETE.search(cl)
        if m and "항" not in cl[m.end(): m.end() + 3]:
            k = _key(m.group(1), m.group(2))
            if reg.get(k):
                ops.append({"type": "delete", "article": k, "src": cl})
            else:
                notes.append(f"'{cl}': 제{k}조가 없습니다")
            continue
        m = RX_RETITLE.search(cl)
        if m:
            k = _key(m.group(1), m.group(2))
            if reg.get(k):
                ops.append({"type": "retitle", "article": k, "title": unq(m.group("t")), "src": cl})
            else:
                notes.append(f"'{cl}': 제{k}조가 없습니다")
            continue
        m = RX_ADD_PARA.search(cl)
        if m:
            k = _key(m.group(1), m.group(2))
            if reg.get(k):
                ops.append({"type": "add_para", "article": k, "text": unq(m.group("t")), "src": cl})
                continue
        m = RX_ADD_ITEM.search(cl)
        if m:
            k = _key(m.group(1), m.group(2))
            a = reg.get(k)
            if a:
                para = int(m.group(3)) if m.group(3) else None
                ops.append({"type": "add_item", "article": k, "para": para, "text": unq(m.group("t")), "src": cl})
                continue
        m = RX_REPL.search(cl) or RX_REPL2.search(cl)
        if m:
            op = _resolve_replace(cl, m, entry)
            if op.get("error"):
                notes.append(op["error"])
            else:
                ops.append(op)
                if op.get("alternatives"):
                    notes.append(f"{drafting.qj(op['find'], '이/가')} 들어간 곳이 여러 곳입니다. {drafting.josa(op['loc'], '을/를')} 골랐습니다. 다른 후보: " +
                                 ", ".join(x["loc"] for x in op["alternatives"]))
            continue
        if RX_INSERT.search(cl) and not re.search(r"별표|별지", cl):
            op = _resolve_insert(cl, entry, lib, head)
            if op.get("error"):
                notes.append(op["error"])
            else:
                ops.append(op)
                notes.extend(op.pop("notes", []))
            continue
        if re.search(r"규정|지침|조례|규칙", cl) and len(cl) < 25:
            continue  # "체육시설 운영 규정에서" 같은 대상 지정 구절
        notes.append(f"'{cl}': 어떤 개정인지 알아내지 못했습니다. 작업을 직접 추가하거나 문장을 바꿔 주세요.")
    return ops, notes, effective


def _resolve_replace(cl, m, entry):
    reg = entry.reg
    a_raw, b_raw = m.group("a"), m.group("b")
    find, repl = unq(a_raw), unq(b_raw)
    quoted = a_raw.strip()[:1] in "\"'“‘「"
    topic = cl[: m.start()]
    scope = RX_ART.search(topic) or RX_ART.search(cl)
    art_keys = None
    para_no = None
    if scope:
        k = _key(scope.group(1), scope.group(2))
        if not reg.get(k):
            return {"error": f"'{cl}': 제{k}조가 없습니다"}
        art_keys = [k]
        para_no = int(scope.group(3)) if scope.group(3) else None
        topic = topic.replace(scope.group(0), " ")
    topic = re.sub(r"(?:을|를|은|는|의|에서)\s*$", "", topic.strip())
    ttoks = [t for t in tokens(topic) if len(t) >= 2 and t not in STOP and t not in tokens(entry.title)]
    everywhere = quoted and (not ttoks or re.search(r"모두|전부|통일|일괄|전체", cl)) and not scope
    rx = re.compile(r"(?<![\d가-힣])" + re.escape(find) if find[:1].isdigit() else re.escape(find))
    cands = []
    for a in reg.articles:
        if a.deleted or (art_keys and a.key not in art_keys):
            continue
        for path, loc, text in walk(a):
            if para_no and a.paras[path[1]].no != para_no:
                continue
            for mm in rx.finditer(text):
                cands.append({"article": a.key, "path": list(path), "loc": a.label + loc,
                              "anchor": _anchor(text, mm.start(), mm.end()),
                              "score": _topic_score(ttoks, a.title + " " + text), "text": text})
                break
    if not cands:
        return {"error": f"'{cl}': 규정에서 {drafting.qj(find, '을/를')} 찾지 못했습니다"}
    if everywhere:
        return {"type": "replace", "article": None, "find": find, "repl": repl, "all": True, "src": cl,
                "loc": f"규정 전체({len(cands)}곳)"}
    cands.sort(key=lambda c: -c["score"])
    best = cands[0]
    ties = [c for c in cands[1:] if c["score"] == best["score"]]
    op = {"type": "replace", "article": best["article"], "path": best["path"], "find": find, "repl": repl,
          "anchor": best["anchor"], "all": False, "src": cl, "loc": best["loc"]}
    if ties:
        op["alternatives"] = [{"article": c["article"], "path": c["path"], "loc": c["loc"], "anchor": c["anchor"]} for c in ties]
    return op


def _insert_title(cl):
    t = RX_AFTER.sub(" ", cl)
    t = re.sub(r"^.*?(?:규정|지침|조례|규칙)(?:에서|에|의)\s+", "", t)
    t = re.split(r"\s*(?:에\s*관한|에\s*대한|에\s*관하여|관련|하는|을\s*위한)\s*", t)[0] if re.search(r"에\s*관한|에\s*대한|에\s*관하여|관련|하는\s*(?:조|규정)|을\s*위한", t) else t
    t = re.sub(r"(?:조문|조항|규정|조)?\s*(?:을|를)?\s*(?:신설|새로.*|추가.*|넣.*|만들.*)$", "", t)
    t = re.sub(r"^(?:새로운|새로|새)\s*", "", t).strip(" '\"“”‘’")
    t = re.sub(r"\s*(?:을|를)$", "", t)
    return t.strip()


def _resolve_insert(cl, entry, lib, head):
    reg = entry.reg
    notes = []
    title = _insert_title(cl)
    if not title:
        return {"error": f"'{cl}': 신설할 조문의 제목(내용)을 알아내지 못했습니다"}
    sim = drafting.similar_article(lib, title, exclude_entry=entry) if lib else None
    body = None
    ref = None
    if sim:
        s, e, a = sim
        if e is entry:
            sim = None
        else:
            title = a.title if len(a.title) <= len(title) + 3 else title
            body = drafting.adapt(a, head=head, self_word=entry.self_word, from_word=e.self_word)
            ref = {"reg": e.short, "label": a.label, "title": a.title}
            notes.append(f"신설 조문 {drafting.qj(title, '은/는')} {e.short} {a.label}{drafting.josa('(' + a.title + ')', '을/를')} 참고해 작성했습니다. 내용을 확인하세요.")
    if body is None:
        x = re.sub(r"(?:의)?\s*(?:취소|철회|감면|반환|신청|기준|제한)$", "", title).strip() or title
        body = drafting.template_for(title, head=head, self_word=entry.self_word, x=x)
        notes.append(f"신설 조문 {drafting.qj(title, '은/는')} 표준 조문 틀로 작성했습니다. 【 】 부분을 채워 주세요.")
    m = RX_AFTER.search(cl)
    if m:
        after = _key(m.group(1), m.group(2))
        if not reg.get(after):
            return {"error": f"'{cl}': 제{after}조가 없습니다"}
    else:
        # 위치를 말하지 않았으면 내용이 가장 가까운 조문 다음
        best = max(reg.articles, key=lambda a: _topic_score(tokens(title), a.title + " " + a.plain))
        after = best.key
        notes.append(f"신설 위치를 말하지 않아 내용이 가장 가까운 {best.header} 다음에 두었습니다.")
    return {"type": "insert", "after": after, "title": title, "text": body, "src": cl, "ref": ref, "notes": notes}


# ------------------------------------------------------------------ 개정 의도 읽기(모델)

LLM_SYSTEM = """당신은 공공기관 규정 개정 실무자입니다. 현행 규정과 개정 의도를 보고 개정 작업 목록을 JSON으로 만듭니다.
가능한 작업:
- {"type":"replace","article":"8","find":"바꿀 글자(현행 조문에 그대로 있는 글자)","repl":"새 글자"}
- {"type":"retitle","article":"8","title":"새 제목"}
- {"type":"add_item","article":"9","para":1,"text":"추가할 호 내용"}
- {"type":"add_para","article":"9","text":"추가할 항 내용"}
- {"type":"insert","after":"8","title":"신설 조 제목","text":"① 첫째 항\\n② 둘째 항"}
- {"type":"delete","article":"4"}
article 은 조 번호만 씁니다(예: "8", 가지 조문은 "8의2"). find 는 반드시 현행 조문 글자를 그대로 복사합니다.
출력 형식: {"operations":[...], "effective":"시행일(없으면 빈 문자열)"}"""


def llm_ops(llm, entry, intent):
    reg = entry.reg
    body = "\n".join(a.to_text() for a in reg.articles)
    out = llm.json(LLM_SYSTEM, f"[현행 규정: {entry.title}]\n{body[:9000]}\n\n[개정 의도]\n{intent}", max_tokens=2500)
    ops, notes = [], []
    for o in (out.get("operations") or []):
        if not isinstance(o, dict):
            continue
        t = o.get("type")
        k = str(o.get("article") or o.get("after") or "").replace("제", "").replace("조", "").strip()
        a = reg.get(k) if k else None
        if t == "replace":
            find, repl = str(o.get("find") or ""), str(o.get("repl") or "")
            if not find or a is None:
                notes.append(f"모델 제안 제외(대상 조문 없음): {o}")
                continue
            hit = None
            for path, loc, text in walk(a):
                i = text.find(find)
                if i >= 0:
                    hit = (path, loc, _anchor(text, i, i + len(find)))
                    break
            if not hit:
                notes.append(f"모델 제안 제외(현행 조문에 '{find}' 없음): {a.label}")
                continue
            ops.append({"type": "replace", "article": a.key, "path": list(hit[0]), "find": find, "repl": repl,
                        "anchor": hit[2], "all": False, "loc": a.label + hit[1], "src": "모델"})
        elif t in ("retitle", "add_item", "add_para", "delete") and a is not None:
            op = {"type": t, "article": a.key, "src": "모델"}
            if t == "retitle":
                op["title"] = str(o.get("title") or "").strip()
            if t in ("add_item", "add_para"):
                op["text"] = str(o.get("text") or "").strip()
                op["para"] = o.get("para")
            if (t == "retitle" and not op["title"]) or (t in ("add_item", "add_para") and not op["text"]):
                continue
            ops.append(op)
        elif t == "insert" and a is not None and o.get("title"):
            ops.append({"type": "insert", "after": a.key, "title": str(o["title"]).strip(),
                        "text": str(o.get("text") or "").strip() or drafting.template_for(str(o["title"])), "src": "모델"})
        else:
            notes.append(f"모델 제안 제외(형식 오류): {str(o)[:120]}")
    eff = (out.get("effective") or "").strip() or None
    return ops, notes, eff


def plan(intent, entry, lib, llm=None, head="이사장"):
    """규칙으로 읽고, 모델이 연결되어 있으면 모델 제안도 받아 검증된 쪽을 쓴다."""
    ops, notes, eff = parse_intent(intent, entry, lib, head)
    engine = "규칙"
    if llm is not None and llm.enabled:
        try:
            m_ops, m_notes, m_eff = llm_ops(llm, entry, intent)
            if m_ops and len(m_ops) >= len(ops):
                # 모델이 만든 신설 조문이 비어 있으면 규칙 쪽 본문을 쓴다
                ops, notes, eff = m_ops, m_notes + [f"모델({llm.label}) 제안을 현행 조문과 대조해 사용했습니다."], m_eff or eff
                engine = "모델"
            else:
                notes.append(f"모델 제안이 규칙 결과보다 적거나 검증되지 않아 규칙 결과를 썼습니다. ({'; '.join(m_notes)[:200]})")
        except LLMError as e:
            notes.append(f"모델 호출 실패로 규칙 결과를 썼습니다: {e}")
    return {"ops": ops, "notes": notes, "effective": eff, "engine": engine}


# ------------------------------------------------------------------ 개정 적용

def apply(entry, ops, mode="branch", effective=None, lib=None):
    """반환: {reg, mapping, changes(인용 정정), errors, inserted(새 key 목록)}"""
    reg = regparse.clone(entry.reg)
    for a in reg.articles:
        a._orig = a.key
    errors, inserted = [], []
    applied = []
    for op in ops:  # 위치(항·호)를 모르는 글자 바꾸기는 현행 조문에서 찾아 둔다(개정문 표기용)
        if op.get("type") == "replace" and op.get("article") and not op.get("path") and not op.get("all"):
            a0 = entry.reg.get(op["article"])
            key = op.get("anchor") or op["find"]
            for path, loc, text in (walk(a0) if a0 else []):
                if key in text:
                    op["path"] = list(path)
                    op["loc"] = a0.label + loc
                    break
    # 1) 글자 수준
    for op in ops:
        t = op.get("type")
        try:
            if t == "replace":
                _apply_replace(reg, op)
            elif t == "retitle":
                reg.get(op["article"]).title = op["title"]
            elif t == "add_item":
                a = reg.get(op["article"])
                p = _para(a, op.get("para"))
                nums = [int(re.match(r"\d+", it.no).group()) for it in p.items if re.match(r"\d+", it.no)]
                p.items.append(regparse.Item(no=str((max(nums) if nums else 0) + 1), text=op["text"].rstrip()))
                op["_new_item"] = p.items[-1].no
                op["_para_no"] = p.no
            elif t == "add_para":
                a = reg.get(op["article"])
                if len(a.paras) == 1 and a.paras[0].no == 0:
                    a.paras[0].no = 1
                n = max([p.no for p in a.paras] or [0]) + 1
                a.paras.append(regparse.Para(no=n, text=op["text"].lstrip("①②③④⑤⑥⑦⑧⑨⑩ ").rstrip()))
                op["_new_para"] = n
            elif t == "appendix_replace":
                hit = False
                for i, line in enumerate(reg.appendices):
                    if op["find"] in line:
                        reg.appendices[i] = line.replace(op["find"], op["repl"])
                        hit = True
                if not hit:
                    raise ValueError(f"별표에서 {drafting.qj(op['find'], '을/를')} 찾지 못했습니다")
            elif t == "appendix_add":
                idx = next((i for i, l in enumerate(reg.appendices) if op.get("after_find") and op["after_find"] in l), len(reg.appendices) - 1)
                reg.appendices.insert(idx + 1, op["text"])
            elif t in ("insert", "delete"):
                continue
            else:
                raise ValueError(f"알 수 없는 작업: {t}")
            applied.append(op)
        except Exception as e:  # 한 작업이 실패해도 나머지는 적용
            errors.append(f"{op.get('loc') or op.get('article') or ''} {t}: {e}")
    # 2) 조 신설·삭제
    for op in ops:
        t = op.get("type")
        try:
            if t == "insert":
                a = _insert(reg, op, mode)
                op["_new_key"] = a.key
                inserted.append(a.key)
                applied.append(op)
            elif t == "delete":
                _delete(reg, op["article"], mode)
                applied.append(op)
        except Exception as e:
            errors.append(f"{t} {op.get('after') or op.get('article')}: {e}")
    mapping = {}
    for a in entry.reg.articles:
        mapping[a.key] = None
    for a in reg.articles:
        if getattr(a, "_orig", None):
            mapping[a._orig] = None if (a.deleted and mode == "renumber") else a.key
    # 3) 이 규정 안의 인용 정정
    changes = []
    moved = {k: v for k, v in mapping.items() if v != k}
    if moved:
        rf = RefFinder(entry.aliases)
        for a in reg.articles:
            for path, loc, text in list(walk(a)):
                new, ch = fix_refs(text, rf, moved, None)
                if ch:
                    set_unit(a, path, new)
                    changes.append({"where": "이 규정", "loc": a.label + loc, "orig_article": getattr(a, "_orig", None),
                                    "old": text, "new": new, "fixes": ch})
        for i, line in enumerate(reg.appendices):
            new, ch = fix_refs(line, rf, moved, None)
            if ch:
                reg.appendices[i] = new
                changes.append({"where": "이 규정", "loc": "별표", "old": line, "new": new, "fixes": ch})
    # 4) 부칙
    word = entry.self_word
    eff = f"{effective}부터" if effective else "공포한 날부터"
    reg.addenda = list(reg.addenda) + [f"부칙({effective or '공포일'})", f"{drafting.josa('이 ' + word, '은/는')} {eff} 시행한다."]
    return {"reg": reg, "mapping": mapping, "changes": changes, "errors": errors, "inserted": inserted, "applied": applied}


def _para(a, para_no):
    if para_no:
        p = next((p for p in a.paras if p.no == int(para_no)), None)
        if p is None:
            raise ValueError(f"{a.label}에 제{para_no}항이 없습니다")
        return p
    with_items = [p for p in a.paras if p.items]
    if with_items:
        return with_items[-1]
    if not a.paras:
        a.paras.append(regparse.Para(no=0, text=""))
    return a.paras[-1]


def _apply_replace(reg, op):
    find, repl = op["find"], op["repl"]
    targets = [reg.get(op["article"])] if op.get("article") else [a for a in reg.articles if not a.deleted]
    if op.get("article") and targets[0] is None:
        raise ValueError(f"제{op['article']}조가 없습니다")
    n = 0
    for a in targets:
        units = list(walk(a))
        if op.get("path") and not op.get("all"):
            units = [u for u in units if list(u[0]) == list(op["path"])] or units
        for path, loc, text in units:
            if op.get("anchor") and not op.get("all"):
                if op["anchor"] in text:
                    set_unit(a, path, text.replace(op["anchor"], op["anchor"].replace(find, repl, 1), 1))
                    return
                continue
            if find in text:
                set_unit(a, path, text.replace(find, repl) if op.get("all") else text.replace(find, repl, 1))
                n += 1
                if not op.get("all"):
                    return
    if n == 0:
        raise ValueError(f"{drafting.qj(find, '을/를')} 찾지 못했습니다(이미 바뀌었거나 다른 작업과 겹침)")


def _insert(reg, op, mode):
    after = next((a for a in reg.articles if getattr(a, "_orig", None) == op["after"]), None)
    if after is None:
        raise ValueError(f"제{op['after']}조가 없습니다")
    same = [i for i, a in enumerate(reg.articles) if a.no == after.no]
    pos = max(same) + 1
    if mode == "renumber":
        for a in reg.articles[pos:]:
            a.no += 1
        no, sub = after.no + 1, 0
    else:
        subs = [a.sub for a in reg.articles if a.no == after.no]
        no, sub = after.no, max(2, max(subs) + 1)
    new = drafting.article_from_text(no, sub, op.get("title", ""), op.get("text", ""), chapter=after.chapter)
    new._orig = None
    reg.articles.insert(pos, new)
    return new


def _delete(reg, key, mode):
    a = next((x for x in reg.articles if getattr(x, "_orig", None) == key), None)
    if a is None:
        raise ValueError(f"제{key}조가 없습니다")
    if mode == "renumber":
        i = reg.articles.index(a)
        reg.articles.remove(a)
        if a.sub == 0:
            for x in reg.articles[i:]:
                x.no -= 1
        a.deleted = True
    else:
        a.deleted = True
        a.paras = []
        a.title = ""


def fix_refs(text, rf, moved, law_names):
    """moved(옛 key->새 key|None) 에 따라 인용 번호를 고친다.
    law_names=None 이면 내부 인용(규정명 없음·'이 규정')만, 집합이면 그 규정 이름의 외부 인용만."""
    out, last, fixes = [], 0, []
    for r in rf.find(text):
        if law_names is None:
            if r["law"] is not None:
                continue
        else:
            if r["law"] is None or norm_name(r["law"]) not in law_names:
                continue
        if r["key"] not in moved:
            continue
        new = moved[r["key"]]
        if new is None:
            fixes.append({"old": r["raw"], "new": None, "note": "삭제된 조를 인용함(확인 필요)"})
            continue
        no, sub = regparse.parse_key(new)
        out.append(text[last:r["num_start"]])
        out.append(regparse.alabel(no, sub))
        last = r["num_end"]
        fixes.append({"old": regparse.alabel(*regparse.parse_key(r["key"])), "new": regparse.alabel(no, sub), "raw": r["raw"]})
    out.append(text[last:])
    return "".join(out), fixes


# ------------------------------------------------------------------ 다른 규정·서식 파급

def propagate(lib, entry, mapping, ops):
    """개정으로 손봐야 할 다른 규정·별표·서식 위치."""
    moved = {k: v for k, v in mapping.items() if v != k}
    names = {norm_name(entry.title, lib.org_name), norm_name(entry.short, lib.org_name)}
    fixes, reviews = [], []
    changed_keys = {op.get("article") for op in ops if op.get("article")}
    phrases = [(op["anchor"], op["anchor"].replace(op["find"], op["repl"], 1), op) for op in ops
               if op.get("type") == "replace" and op.get("anchor")]
    for c in lib.citations_of(entry):
        e, r = c["entry"], c["ref"]
        if r["key"] in moved:
            new = moved[r["key"]]
            item = {"entry_id": e.id, "doc": e.short, "layer": e.layer, "loc": c["loc"], "article": c["article"],
                    "old_ref": regparse.alabel(*regparse.parse_key(r["key"])), "text": c["text"]}
            if new is None:
                item["new_ref"] = None
                item["note"] = "삭제되는 조를 인용합니다. 인용 문장을 직접 고쳐야 합니다."
                reviews.append(item)
            else:
                item["new_ref"] = regparse.alabel(*regparse.parse_key(new))
                rf = RefFinder(e.aliases)
                if e.layer == "forms":
                    # 서식의 '같은 규정' 인용도 이 규정을 가리킨다
                    new_text, ch = _fix_form_line(c["text"], rf, moved)
                else:
                    new_text, ch = fix_refs(c["text"], rf, moved, names)
                item["new_text"] = new_text
                prev = next((f for f in fixes if f["entry_id"] == e.id and f["loc"] == c["loc"]), None)
                if prev:
                    prev["old_ref"] += ", " + item["old_ref"]
                    prev["new_ref"] += ", " + item["new_ref"]
                else:
                    fixes.append(item)
        elif r["key"] in changed_keys:
            reviews.append({"entry_id": e.id, "doc": e.short, "layer": e.layer, "loc": c["loc"], "article": c["article"],
                            "old_ref": regparse.alabel(*regparse.parse_key(r["key"])), "text": c["text"],
                            "note": "인용하는 조문의 내용이 바뀝니다. 이 문장도 바꿔야 하는지 확인하세요."})
    # 같은 문구(기한·금액 등)를 옮겨 적은 서식·규정
    for e in lib.entries:
        if e is entry or e.layer == "upper":
            continue  # 상위 법령은 기관 규정 개정으로 바꾸지 않는다
        for loc, akey, text in lib.units(e):
            for old, new, op in phrases:
                core = _core_phrase(old, op["find"])
                if core and core in text:
                    reviews.append({"entry_id": e.id, "doc": e.short, "layer": e.layer, "loc": loc, "article": akey,
                                    "text": text, "suggest": text.replace(core, core.replace(op["find"], op["repl"], 1)),
                                    "note": f"개정되는 문구('{core}')가 여기에도 적혀 있습니다."})
    # 중복 제거 (같은 위치에 고칠 문장 제안이 있으면 일반 안내는 뺀다)
    concrete = {(x["entry_id"], x["loc"]) for x in reviews if x.get("suggest")}
    seen, uniq = set(), []
    for x in reviews:
        if not x.get("suggest") and "new_text" not in x and (x["entry_id"], x["loc"]) in concrete:
            continue
        k = (x["entry_id"], x["loc"], x.get("note"))
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return {"fixes": fixes, "reviews": uniq}


def _core_phrase(anchor, find):
    """'허가를 받은 날부터 7일 이내에' -> 바뀌는 글자 앞 두 어절을 붙인 비교용 구절 '받은 날부터 7일'"""
    i = anchor.find(find)
    if i < 0:
        return None
    words = anchor[:i].split()
    if len(words) < 2:
        return None
    return " ".join(words[-2:]) + (" " if anchor[i - 1:i] == " " else "") + find


def _fix_form_line(text, rf, moved):
    out, last, ch = [], 0, []
    for r in rf.find(text):
        if r["key"] not in moved or moved[r["key"]] is None:
            continue
        no, sub = regparse.parse_key(moved[r["key"]])
        out.append(text[last:r["num_start"]])
        out.append(regparse.alabel(no, sub))
        last = r["num_end"]
        ch.append(r["raw"])
    out.append(text[last:])
    return "".join(out), ch


def fix_form_file(data, replacements):
    """hwpx/docx 서식 파일 안 문장을 바꾼 사본을 만든다(원본은 그대로)."""
    import io
    import zipfile
    from xml.sax.saxutils import escape
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    done = 0
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            raw = src.read(info.filename)
            if info.filename.endswith(".xml"):
                text = raw.decode("utf-8")
                for old, new in replacements:
                    for o, n in ((escape(old), escape(new)), (old, new)):
                        if o in text:
                            text = text.replace(o, n)
                            done += 1
                            break
                raw = text.encode("utf-8")
            ctype = zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
            dst.writestr(info, raw, compress_type=ctype)
    return out.getvalue(), done


# ------------------------------------------------------------------ 개정문

def _loc_of(reg, op):
    a = reg.get(op["article"]) if op.get("article") else None
    if a is None:
        return op.get("loc") or ""
    if op.get("path"):
        return unit_loc(a, op["path"])
    return a.label


def amendment_text(entry, result, ops, mode, effective=None):
    """'○○규정 일부를 다음과 같이 개정한다.' 형식의 개정문."""
    old = entry.reg
    lines = [f"{entry.title} 일부를 다음과 같이 개정한다.", ""]
    mapping = result["mapping"]
    new = result["reg"]
    renumber_note = []
    for op in ops:
        t = op.get("type")
        if t == "replace":
            f, r = op["find"], op["repl"]
            where = "전체" if op.get("all") else _loc_of(old, op)
            if op.get("all"):
                scope = old.get(op["article"]).label if op.get("article") and old.get(op["article"]) else entry.short
                lines.append(f"{scope} 중 \"{f}\"{'을' if drafting.has_batchim(f) else '를'} 각각 \"{r}\"{'로' if (not drafting.has_batchim(r) or drafting.rieul(r)) else '으로'} 한다.")
            else:
                lines.append(f"{where} 중 \"{f}\"{'을' if drafting.has_batchim(f) else '를'} \"{r}\"{'로' if (not drafting.has_batchim(r) or drafting.rieul(r)) else '으로'} 한다.")
        elif t == "retitle":
            a = old.get(op["article"])
            lines.append(f"{a.label}의 제목 \"{a.title}\"{'을' if drafting.has_batchim(a.title) else '를'} \"{op['title']}\"{'로' if (not drafting.has_batchim(op['title']) or drafting.rieul(op['title'])) else '으로'} 한다.")
        elif t == "add_item" and op.get("_new_item"):
            a = old.get(op["article"])
            pl = f"제{op['_para_no']}항" if op.get("_para_no") else ""
            lines.append(f"{a.label}{pl}에 제{op['_new_item']}호를 다음과 같이 신설한다.")
            lines.append(f"  {op['_new_item']}. {op['text']}")
        elif t == "add_para" and op.get("_new_para"):
            a = old.get(op["article"])
            lines.append(f"{a.label}에 제{op['_new_para']}항을 다음과 같이 신설한다.")
            lines.append(f"  {regparse.CIRCLED[op['_new_para'] - 1]} {op['text']}")
        elif t == "insert" and op.get("_new_key"):
            na = new.get(op["_new_key"])
            if mode == "renumber":
                renumber_note.append(na)
            else:
                lines.append(f"{na.label}를 다음과 같이 신설한다.")
                lines.append(na.to_text())
        elif t == "delete":
            a = old.get(op["article"])
            lines.append(f"{a.label}를 삭제한다.")
        elif t == "appendix_replace" or t == "appendix_add":
            if "별표를 별지와 같이 한다." not in lines:
                lines.append("별표를 별지와 같이 한다.")
    if renumber_note:
        moved = [(k, v) for k, v in mapping.items() if v and v != k]
        if moved:
            olds = [regparse.alabel(*regparse.parse_key(k)) for k, _ in moved]
            news = [regparse.alabel(*regparse.parse_key(v)) for _, v in moved]
            lines.append(f"{olds[0]}부터 {olds[-1]}까지를 각각 {news[0]}부터 {news[-1]}까지로 하고, " +
                         ", ".join(a.label for a in renumber_note) + "를 다음과 같이 신설한다.")
        else:
            lines.append(", ".join(a.label for a in renumber_note) + "를 다음과 같이 신설한다.")
        for a in renumber_note:
            lines.append(a.to_text())
    for ch in result["changes"]:
        for fx in ch["fixes"]:
            if fx.get("new"):
                lines.append(f"{ch['loc']}{'(종전의 ' + regparse.alabel(*regparse.parse_key(ch['orig_article'])) + ')' if ch.get('orig_article') and ch['loc'].split('제')[1:2] and not ch['loc'].startswith(regparse.alabel(*regparse.parse_key(ch['orig_article']))) else ''} 중 \"{fx['old']}\"를 \"{fx['new']}\"로 한다.")
    lines += ["", "부칙", f"{drafting.josa('이 ' + entry.self_word, '은/는')} {(effective + '부터') if effective else '공포한 날부터'} 시행한다."]
    return lines


def describe(op, reg):
    t = op.get("type")
    if t == "replace":
        return f"{op.get('loc') or '규정 전체'}: '{op['find']}' → '{op['repl']}'"
    if t == "retitle":
        return f"제{op['article']}조 제목 → '{op['title']}'"
    if t == "add_item":
        return f"제{op['article']}조에 호 추가: {op['text'][:40]}"
    if t == "add_para":
        return f"제{op['article']}조에 항 추가: {op['text'][:40]}"
    if t == "insert":
        return f"제{op['after']}조 다음에 '{op['title']}' 조문 신설"
    if t == "delete":
        a = reg.get(op["article"])
        return f"{a.header if a else '제' + op['article'] + '조'} 삭제"
    if t == "appendix_replace":
        return f"별표: '{op['find']}' → '{op['repl']}'"
    if t == "appendix_add":
        return f"별표에 줄 추가: {op['text'][:40]}"
    return t


def deep(o):
    return copy.deepcopy(o)


def compare_rows(old_reg, new_reg, mapping):
    """신구조문대비표 행. 번호만 바뀐 조문이 이어지면 한 행으로 묶는다."""
    rows = regparse.compare(old_reg, new_reg, mapping)
    pure = set()
    for k, v in mapping.items():
        if v and v != k:
            oa, na = old_reg.get(k), new_reg.get(v)
            if oa and na and oa.title == na.title and _paras_text(oa) == _paras_text(na):
                pure.add(k)
    out, run = [], []

    def flush():
        if not run:
            return
        a, b = run[0], run[-1]
        ol = regparse.alabel(*regparse.parse_key(a["key_old"]))
        nl = regparse.alabel(*regparse.parse_key(a["key_new"]))
        if len(run) > 1:
            ol += " ~ " + regparse.alabel(*regparse.parse_key(b["key_old"]))
            nl += " ~ " + regparse.alabel(*regparse.parse_key(b["key_new"]))
        out.append({"kind": "번호 이동", "key_old": a["key_old"], "key_new": a["key_new"],
                    "old": f"{ol}\n(생 략)", "new": f"__{nl}__\n(현행 {drafting.josa(ol, '과/와')} 같음)"})
        run.clear()
    for r in rows:
        if r["kind"] == "변경" and r["key_old"] in pure:
            run.append(r)
            continue
        flush()
        out.append(r)
    flush()
    if old_reg.appendices != new_reg.appendices:
        o, n = regparse.mark_diff("\n".join(old_reg.appendices), "\n".join(new_reg.appendices))
        out.append({"kind": "별표", "key_old": None, "key_new": None, "old": o, "new": n})
    return out


def _paras_text(a):
    return "\n".join(a.body_lines())
