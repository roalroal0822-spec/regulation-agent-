"""상위법 개정 영향 분석: 상위 법령 개정 전후 조문을 비교하고 영향받는 기관 규정 조문과 개정 후보를 찾는다."""
import difflib
import re

from core import regparse
from core.textutil import similarity

from drafting import josa, qj
from library import RefFinder, norm_name


def repr_q(s):
    return f"'{s}'"


def _ns(t):
    return re.sub(r"\s|의", "", t or "")


def match_articles(old, new):
    """옛 조 key -> 새 조 key. 제목이 같으면 같은 조, 아니면 내용이 가장 비슷한 조."""
    mapping, used = {}, set()
    new_by_title = {}
    for a in new.articles:
        new_by_title.setdefault(_ns(a.title), []).append(a)
    for a in old.articles:
        c = [x for x in new_by_title.get(_ns(a.title), []) if x.key not in used]
        if a.title and len(c) == 1:
            mapping[a.key] = c[0].key
            used.add(c[0].key)
    for a in old.articles:
        if a.key in mapping:
            continue
        best = max(((similarity(a.plain, b.plain), b) for b in new.articles if b.key not in used), default=(0, None), key=lambda x: x[0])
        if best[1] is not None and best[0] >= 0.5:
            mapping[a.key] = best[1].key
            used.add(best[1].key)
        else:
            mapping[a.key] = None
    return mapping


def _edits(old_text, new_text):
    """바뀐 글자 조각 [(옛 조각, 새 조각, 옛 문장 속 위치)] — 숫자 뒤 단위까지 넓혀 잡는다."""
    a, b = regparse._tok(old_text), regparse._tok(new_text)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    out = []
    pos = [0]
    for t in a:
        pos.append(pos[-1] + len(t))
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "replace":
            continue
        o, n = "".join(a[i1:i2]), "".join(b[j1:j2])
        k = i2
        while k < len(a) and not a[k].isspace() and a[k] not in ",.":
            o += a[k]
            n += a[k]
            k += 1
        out.append((o.strip(), n.strip(), pos[i1]))
    return out


def diff_law(old, new):
    mapping = match_articles(old, new)
    changes = []
    for oa in old.articles:
        nk = mapping.get(oa.key)
        na = new.get(nk) if nk else None
        if na is None:
            changes.append({"kind": "삭제", "old_key": oa.key, "new_key": None, "old": oa.to_text(), "new": "", "title": oa.title})
            continue
        ob, nb = "\n".join(oa.body_lines()), "\n".join(na.body_lines())
        moved = nk != oa.key
        content = ob != nb or oa.title != na.title
        if not moved and not content:
            continue
        mo, mn = regparse.mark_diff(oa.to_text(), na.to_text())
        added = [l.strip() for l in nb.split("\n") if l.strip() and l.strip() not in [x.strip() for x in ob.split("\n")]
                 and not any(similarity(l, x) > 0.75 for x in ob.split("\n"))]
        changes.append({"kind": "번호 이동·내용 변경" if moved and content else ("번호 이동" if moved else "내용 변경"),
                        "old_key": oa.key, "new_key": nk, "title": na.title, "old": mo, "new": mn,
                        "edits": _edits(ob, nb) if content else [], "added_lines": added,
                        "old_body": ob, "new_body": nb})
    used = set(v for v in mapping.values() if v)
    for na in new.articles:
        if na.key not in used:
            changes.append({"kind": "신설", "old_key": None, "new_key": na.key, "title": na.title, "old": "", "new": regparse._wrap(na.to_text()),
                            "new_body": "\n".join(na.body_lines())})
    order = {a.key: i for i, a in enumerate(new.articles)}
    changes.sort(key=lambda c: order.get(c["new_key"], 999))
    return {"mapping": mapping, "changes": changes}


def _ctx(text, seg, pos, words=2):
    """바뀐 조각 앞 두 어절을 붙여 기관 규정에서 찾을 구절을 만든다."""
    start = pos
    for _ in range(words):
        s = text.rfind(" ", 0, max(0, start - 1))
        if s < 0 or "\n" in text[s:start]:
            start = max(text.rfind("\n", 0, start) + 1, 0)
            break
        start = s + 1
    return text[start: pos + len(seg)].strip()


def analyze(lib, law_entry, new_reg):
    old = law_entry.reg
    d = diff_law(old, new_reg)
    by_old = {c["old_key"]: c for c in d["changes"] if c["old_key"]}
    names = {norm_name(law_entry.title, lib.org_name), norm_name(law_entry.short, lib.org_name)}
    cands = []

    def add(**kw):
        kw["id"] = f"c{len(cands) + 1}"
        cands.append(kw)
    for e in lib.entries:
        if e is law_entry:
            continue
        rf = RefFinder(e.aliases)
        for loc, akey, text in lib.units(e):
            refs = [r for r in rf.find(text) if r["law"] and norm_name(r["law"], lib.org_name) in names]
            for r in refs:
                ch = by_old.get(r["key"])
                if not ch:
                    continue
                if ch["kind"] == "삭제":
                    add(entry_id=e.id, doc=e.short, layer=e.layer, loc=loc, article=akey, type="삭제된 조 인용",
                        reason=f"인용한 {law_entry.short} {regparse.alabel(*regparse.parse_key(r['key']))}가 삭제됩니다. 인용 문장을 고쳐야 합니다.",
                        law_old=ch["old"], law_new="<삭 제>", text=text, op=None)
                    continue
                if ch["new_key"] != r["key"]:
                    new_lab = regparse.alabel(*regparse.parse_key(ch["new_key"]))
                    raw = r["raw"]
                    new_raw = raw[: r["num_start"] - r["start"]] + new_lab + raw[r["num_end"] - r["start"]:]
                    if e.layer == "forms":
                        op = {"type": "form_replace", "find": raw, "repl": new_raw}
                    elif akey:
                        op = {"type": "replace", "article": akey, "find": raw, "repl": new_raw, "anchor": raw, "all": False, "loc": loc}
                    else:
                        op = {"type": "appendix_replace", "find": raw, "repl": new_raw}
                    add(entry_id=e.id, doc=e.short, layer=e.layer, loc=loc, article=akey, type="인용 번호 정정",
                        reason=f"{law_entry.short} {regparse.alabel(*regparse.parse_key(r['key']))}({ch['title']})가 {new_lab}로 옮겨집니다.",
                        law_old=ch["old"], law_new=ch["new"], text=text, op=op, suggest=text.replace(raw, new_raw))
                if ch.get("edits") or ch.get("added_lines"):
                    _content_cands(add, e, loc, akey, text, ch, law_entry, cited=True)
        # 인용은 없지만 같은 구절(기한·금액 등)이나 같은 목록을 옮겨 적은 곳
    for ch in d["changes"]:
        if not (ch.get("edits") or ch.get("added_lines")):
            continue
        for e in lib.entries:
            if e is law_entry or e.layer == "upper":
                continue
            for loc, akey, text in lib.units(e):
                if any(c["entry_id"] == e.id and c["loc"] == loc and c["type"] in ("내용 반영", "목록 추가") and c.get("law_key") == ch["old_key"] for c in cands):
                    continue
                _content_cands(add, e, loc, akey, text, ch, law_entry, cited=False)
    # 신설 조문과 비슷한 기관 규정 조문(정책 판단 필요)
    for ch in d["changes"]:
        if ch["kind"] != "신설":
            continue
        hits = lib.search_articles(ch["title"] + " " + ch["new_body"], k=3, layers=("org",))
        for e, a, s in hits:
            sim = similarity(ch["new_body"], a.plain)
            if sim < 0.18:
                continue
            add(entry_id=e.id, doc=e.short, layer=e.layer, loc=a.label, article=a.key, type="신설 조문 검토",
                reason=f"{law_entry.short}에 {regparse.alabel(*regparse.parse_key(ch['new_key']))}({ch['title']})가 신설됩니다. 이 조문과 내용이 겹치거나 어긋나는지 확인하세요.",
                law_old="", law_new=ch["new"], text=a.to_text(), op=None)
    # 같은 위치 중복 정리
    seen, out = set(), []
    for c in cands:
        k = (c["entry_id"], c["loc"], c["type"], (c.get("op") or {}).get("find"), (c.get("op") or {}).get("text"))
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return {"law": law_entry.title, "diff": d, "candidates": out}


def _content_cands(add, e, loc, akey, text, ch, law_entry, cited):
    for o, n, pos in ch.get("edits", []):
        if not re.search(r"\d", o):
            continue
        ctx = _ctx(ch["old_body"], o, pos)
        if len(ctx) < len(o) + 2 or ctx not in text:
            continue
        new_ctx = ctx[: len(ctx) - len(o)] + n
        if e.layer == "forms":
            op = {"type": "form_replace", "find": ctx, "repl": new_ctx}
        elif akey:
            op = {"type": "replace", "article": akey, "find": o, "repl": n, "anchor": ctx, "all": False, "loc": loc}
        else:
            op = {"type": "appendix_replace", "find": ctx, "repl": new_ctx}
        add(entry_id=e.id, doc=e.short, layer=e.layer, loc=loc, article=akey, type="내용 반영", law_key=ch["old_key"],
            reason=f"{law_entry.short} {regparse.alabel(*regparse.parse_key(ch['new_key']))}({ch['title']})의 {josa(repr_q(ctx), '이/가')} {josa(repr_q(new_ctx), '으로/로')} 바뀝니다." +
            ("" if cited else " (이 조문은 상위 조문을 직접 인용하지 않지만 같은 구절이 있습니다)"),
            law_old=ch["old"], law_new=ch["new"], text=text, op=op, suggest=text.replace(ctx, new_ctx, 1))
    if ch.get("added_lines"):
        old_lines = [l.strip() for l in ch["old_body"].split("\n") if l.strip()]
        tl = text.split("\n")
        hit_lines = [l for l in tl if any(similarity(l, x) > 0.7 for x in old_lines)]
        if not hit_lines:
            return
        last = hit_lines[-1].strip()
        for add_line in ch["added_lines"]:
            if add_line in text:
                continue
            nm = re.match(r"^(\d+)\.", add_line)
            if e.layer == "forms":
                op = None
            elif akey:
                op = {"type": "add_item", "article": akey, "para": None, "text": re.sub(r"^\d+\.\s*", "", add_line)}
            else:
                op = {"type": "appendix_add", "after_find": last, "text": add_line}
            add(entry_id=e.id, doc=e.short, layer=e.layer, loc=loc, article=akey, type="목록 추가", law_key=ch["old_key"],
                reason=f"{law_entry.short} {regparse.alabel(*regparse.parse_key(ch['new_key']))}({ch['title']})에 {qj(add_line, '이/가')} 추가됩니다. 같은 목록을 옮겨 적은 곳입니다.",
                law_old=ch["old"], law_new=ch["new"], text=text, op=op, suggest=(text + "\n" + add_line) if not akey else None)


def ops_for(cands, ids):
    """선택한 후보로 문서별 작업 목록을 만든다. 반환: (규정별 개정 작업, 서식별 고칠 문장)"""
    regs, forms = {}, {}
    for c in cands:
        if c["id"] not in ids or not c.get("op"):
            continue
        if c["op"]["type"] == "form_replace":
            forms.setdefault(c["entry_id"], []).append((c["op"]["find"], c["op"]["repl"]))
        else:
            regs.setdefault(c["entry_id"], []).append(dict(c["op"]))
    return regs, forms
