"""제·개정 문서 세트: 조문안·개정문, 신구조문대비표, 이유서, 행정예고문, 심의 안건, 협의 요청서, 검토 요청서 등."""
import re
from datetime import date, timedelta

from core import regparse

import drafting
from procedure import DOC_NAMES


def _lines(text):
    return [l for l in (text or "").split("\n")]


def _reg_blocks(text):
    out = []
    for l in _lines(text):
        if not l.strip():
            continue
        if re.match(r"^제\d+장", l.strip()):
            out.append(("h3", l.strip()))
        elif re.match(r"^부\s*칙", l.strip()):
            out.append(("h3", l.strip()))
        else:
            out.append(("p", l.rstrip()))
    return out


def main_points(case):
    """주요 내용(가. 나. 다.) 문장 목록."""
    pts = []
    if case["kind"] == "enact":
        for a in case["enact"]["articles"]:
            if a["matter"] in ("목적", "정의", "세부"):
                continue
            pts.append(f"{a['title']}에 관한 사항을 정함(안 제{a['key']}조).")
        return pts
    old = regparse.parse_text(case["old_text"])
    new = regparse.parse_text(case["new_text"])
    mp = case.get("mapping") or {}
    for op in case.get("ops", []):
        t = op.get("type")
        a = old.get(op.get("article") or op.get("after") or "")
        nk = mp.get(a.key) if a else None
        nl = regparse.alabel(*regparse.parse_key(nk)) if nk else ""
        if t == "replace":
            where = op.get("loc") or (a.label if a else "")
            new_where = where.replace(a.label, nl, 1) if a and nl else where
            if op.get("all"):
                pts.append(f"용어 {drafting.qj(op['find'], '을/를')} {drafting.qj(op['repl'], '으로/로')} 정비함.")
            elif re.search(r"제\d+조", op["find"]):
                pts.append(f"상위 법령 조 번호 변경에 따라 인용 조문을 정비함(안 {new_where}).")
            else:
                pts.append(f"{a.title if a else ''} 관련 {drafting.qj(op['find'], '을/를')} {drafting.qj(op['repl'], '으로/로')} 변경함(안 {new_where}).")
        elif t == "insert":
            pts.append(f"{op['title']}에 관한 규정을 신설함(안 {regparse.alabel(*regparse.parse_key(op.get('_new_key') or op['after']))}).")
        elif t == "delete":
            pts.append(f"{a.title if a else ''} 규정을 삭제함(현행 {a.label if a else ''}).")
        elif t == "retitle":
            pts.append(f"{a.label if a else ''}의 제목을 {drafting.qj(op['title'], '으로/로')} 변경함.")
        elif t in ("add_item", "add_para"):
            pts.append(f"{a.title if a else ''}에 '{op['text'][:40]}' 내용을 추가함(안 {nl}).")
        elif t == "appendix_replace":
            pts.append(f"별표의 {drafting.qj(op['find'], '을/를')} {drafting.qj(op['repl'], '으로/로')} 변경함.")
        elif t == "appendix_add":
            pts.append(f"별표에 {drafting.qj(op['text'][:50], '을/를')} 추가함.")
    if case.get("changes"):
        pts.append("조 번호 이동에 따라 이 규정 안의 인용 조문을 정비함.")
    # 같은 문장 중복 제거
    seen, out = set(), []
    for p in pts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def reason_text(case):
    if case.get("reason"):
        return case["reason"]
    if case["kind"] == "enact":
        return f"{case['enact']['core']}에 관한 기준이 없어 업무 처리에 혼선이 있으므로, {case['enact']['purpose_phrase']}에 관하여 필요한 사항을 정하려는 것임."
    titles = []
    old = regparse.parse_text(case["old_text"])
    for op in case.get("ops", []):
        a = old.get(op.get("article") or op.get("after") or "")
        t = op.get("title") if op.get("type") == "insert" else (a.title if a else "")
        if t and t not in titles:
            titles.append(t)
    if case.get("source") == "impact":
        return f"{drafting.josa('「' + case.get('law', '상위 법령') + '」', '이/가')} 개정됨에 따라 인용 조문과 관련 내용을 상위 법령에 맞게 정비하려는 것임."
    return f"{case['short']}의 {', '.join(titles[:4]) or '일부 조문'}에 관한 사항을 현실에 맞게 정비하려는 것임."


def _ganada(items):
    g = "가나다라마바사아자차카타파하"
    return [f"{g[i]}. {x}" if i < len(g) else f"- {x}" for i, x in enumerate(items)]


def build(case, kind, settings, today=None, proc_steps=None):
    today = today or date.today()
    org = settings.get("org_name", "")
    head = settings.get("head", "이사장")
    dept = settings.get("dept", "【주관부서】")
    is_enact = case["kind"] == "enact"
    word = "제정" if is_enact else "일부개정"
    title = case["title"]
    doc_title = f"{title} {word}(안)"
    B = []
    if kind == "draft":
        B.append(("title", doc_title))
        if not is_enact:
            B.append(("h2", "개정문"))
            B += [("p", l) for l in case["amendment"] if l.strip()]
            B.append(("h2", "개정 후 전문"))
        B += _reg_blocks(case["new_text"])
    elif kind == "compare":
        B.append(("title", f"{title} 신구조문대비표"))
        rows = [[r["old"], r["new"]] for r in case.get("compare", [])]
        B.append(("table", ["현행", "개정안"], rows, [1, 1]))
        B.append(("note", "밑줄은 바뀐 부분입니다."))
    elif kind == "reason":
        B.append(("title", f"{title} {'제정' if is_enact else '개정'} 이유서"))
        B.append(("h2", f"1. {'제정' if is_enact else '개정'} 이유"))
        B.append(("p", reason_text(case)))
        B.append(("h2", "2. 주요 내용"))
        B += [("p", p) for p in _ganada(main_points(case))]
        if is_enact and case["enact"].get("delegation", {}).get("text"):
            dl = case["enact"]["delegation"]
            B.append(("h2", "3. 위임 근거"))
            B.append(("p", f"「{dl.get('law', '')}」 {dl.get('label', '')}: {dl['text']}"))
        fixes = case.get("propagate", {}).get("fixes", [])
        if fixes:
            B.append(("h2", "3. 관계 규정 정비"))
            B += [("bullet", f"{f['doc']} {f['loc']}: {f['old_ref']} → {f['new_ref']}") for f in fixes]
        B.append(("h2", "참고"))
        B.append(("p", "신구조문대비표 별첨" if not is_enact else "제정안 전문, 유사 규정 비교표 별첨"))
    elif kind == "notice":
        days = 10
        for s in (proc_steps or []):
            if "예고" in s["title"] and s.get("days"):
                days = s["days"]["n"]
        start = today + timedelta(days=1)
        end = start + timedelta(days=days)
        basis = next((f"「{s['basis']['reg']}」 {s['basis']['label']}" for s in (proc_steps or []) if "예고" in s["title"]), "관련 규정")
        B.append(("p", f"{org} 공고 제{today.year}-   호"))
        B.append(("title", f"{doc_title} 행정예고"))
        B.append(("p", f"{drafting.josa(title, '을/를')} {'제정' if is_enact else '개정'}함에 있어 그 취지와 주요 내용을 미리 알려 의견을 듣고자 {basis}에 따라 다음과 같이 공고합니다."))
        B.append(("p", f"{today.year}년 {today.month}월 {today.day}일"))
        B.append(("p", f"{org} {head}"))
        B.append(("h2", f"1. {'제정' if is_enact else '개정'} 이유"))
        B.append(("p", reason_text(case)))
        B.append(("h2", "2. 주요 내용"))
        B += [("p", p) for p in _ganada(main_points(case))]
        B.append(("h2", "3. 의견 제출"))
        B.append(("p", f"가. 예고 기간: {start.year}. {start.month}. {start.day}. ~ {end.year}. {end.month}. {end.day}. ({days}일 이상)"))
        B.append(("p", f"나. 제출 방법: 우편, 전자우편 또는 {org} 누리집 의견란"))
        B.append(("p", f"다. 제출처: {org} {dept} (전화 {settings.get('phone', '【연락처】')}, 전자우편 {settings.get('email', '【전자우편】')})"))
        B.append(("p", "라. 의견서에는 의견(찬반 여부와 그 사유), 성명(단체명), 연락처를 적어 주십시오."))
        B.append(("h2", "4. 그 밖의 사항"))
        B.append(("p", f"{'제정안' if is_enact else '개정안'} 전문은 {org} 누리집에서 볼 수 있습니다."))
    elif kind == "review":
        B.append(("title", "규정심의위원회 심의 안건"))
        B.append(("table", ["항목", "내용"], [
            ["안건명", doc_title], ["제안 부서", dept], ["제안 일자", f"{today.year}. {today.month}. {today.day}."],
            ["제안 이유", reason_text(case)], ["주요 내용", "\n".join(_ganada(main_points(case)))],
            ["관계부서 협의", "【협의 결과를 적으세요】"], ["행정예고 결과", "【제출 의견과 반영 여부를 적으세요】"],
            ["첨부", "1. " + ("제정안" if is_enact else "개정안·신구조문대비표") + "  2. 이유서"]], [1, 4]))
    elif kind == "consult":
        refs = case.get("propagate", {})
        related = sorted({f["doc"] for f in refs.get("fixes", []) + refs.get("reviews", [])})
        due = today + timedelta(days=next((s["days"]["n"] for s in (proc_steps or []) if "협의" in s["title"] and s.get("days")), 5))
        B.append(("title", f"{doc_title} 관계부서 협의 요청"))
        B.append(("table", ["항목", "내용"], [
            ["수신", "관계부서장 【부서명】"], ["발신", f"{dept}"],
            ["제목", f"{doc_title} 의견 조회"],
            ["내용", f"{drafting.josa(title, '을/를')} {'제정' if is_enact else '개정'}하려고 하니 검토 후 의견을 {due.year}. {due.month}. {due.day}.까지 회신하여 주시기 바랍니다."],
            ["주요 내용", "\n".join(_ganada(main_points(case)))],
            ["함께 바뀌는 규정·서식", "\n".join(related) or "없음"],
            ["붙임", "1. " + ("제정안" if is_enact else "개정안, 신구조문대비표") + "  2. 이유서"]], [1, 4]))
    elif kind == "check":
        B.append(("title", f"{doc_title} 법무 검토 요청"))
        B.append(("p", "상위 법령 위반 여부와 다른 규정과의 충돌 여부에 대한 검토를 요청합니다. 아래는 도구가 미리 점검한 결과입니다."))
        if is_enact:
            B.append(("h2", "위임 범위 검토"))
            B.append(("table", ["구분", "항목", "결과"], [[i["level"], i["item"], i["result"]] for i in case["enact"]["delegation_check"]["items"]], [1, 2, 5]))
        lint_rows = case.get("lint", [])
        B.append(("h2", "자동 점검 결과(조문 인용·번호·용어·충돌)"))
        if lint_rows:
            B.append(("table", ["수준", "종류", "위치", "내용"], [[i["level"], i["kind"], i["loc"], i["message"]] for i in lint_rows], [1, 1, 1, 5]))
        else:
            B.append(("p", "발견된 문제 없음"))
        if case.get("upper_refs"):
            B.append(("h2", "인용하는 상위 법령"))
            B += [("bullet", r) for r in case["upper_refs"]]
    elif kind == "promulgate":
        B.append(("p", f"{org} 규정 제    호"))
        B.append(("title", doc_title.replace("(안)", "")))
        B.append(("p", f"{drafting.josa(title, '을/를')} 다음과 같이 {'제정' if is_enact else '개정'}하여 공포한다."))
        B.append(("p", f"{today.year}년  월  일"))
        B.append(("p", f"{org} {head}"))
        if not is_enact:
            B += [("p", l) for l in case["amendment"] if l.strip()]
        else:
            B += _reg_blocks(case["new_text"])
    elif kind == "refs":
        p = case.get("propagate", {})
        B.append(("title", f"{title} 관계 규정·서식 정비 목록"))
        rows = [[f["doc"], f["loc"], f"{f['old_ref']} → {f['new_ref']}", f.get("new_text", "")] for f in p.get("fixes", [])]
        rows += [[r["doc"], r["loc"], "확인 필요", r["note"] + (f"\n제안: {r['suggest']}" if r.get("suggest") else "")] for r in p.get("reviews", [])]
        rows += [["이 규정", c["loc"], ", ".join(f"{x['old']} → {x['new']}" for x in c["fixes"] if x.get("new")), c["new"]] for c in case.get("changes", [])]
        B.append(("table", ["문서", "위치", "정비 내용", "고친 문장"], rows or [["-", "-", "정비할 곳 없음", ""]], [2, 1, 2, 5]))
    elif kind == "similar":
        s = case["enact"]["similar"]
        B.append(("title", f"{title} 유사 규정 비교표"))
        header = ["초안 조문"] + [r["title"] for r in s["regs"]]
        rows = []
        for r in s["rows"]:
            rows.append([f"{r['title']}\n{r['draft']}"] + [(f"{c['label']}({c['title']})\n{c['text']}" if c else "해당 없음") for c in r["cells"]])
        B.append(("table", header, rows, [3] + [2] * len(s["regs"])))
    elif kind == "delegation":
        B.append(("title", f"{title} 위임 범위 검토표"))
        B.append(("table", ["구분", "항목", "결과"], [[i["level"], i["item"], i["result"]] for i in case["enact"]["delegation_check"]["items"]], [1, 2, 5]))
    elif kind == "structure":
        B.append(("title", f"{title} 조문 체계표"))
        rows = [[a.get("chapter") or "-", f"제{a['key']}조", a["title"], (a["ref"]["reg"] + " " + a["ref"]["label"]) if a.get("ref") else "", (a["upper"]["label"] + " " + a["upper"]["title"]) if a.get("upper") else ""] for a in case["enact"]["articles"]]
        B.append(("table", ["장", "조", "제목", "참고한 유사 조문", "상위 근거"], rows, [2, 1, 2, 2, 2]))
    else:
        raise KeyError(kind)
    return B


def available(case):
    kinds = ["draft", "reason", "notice", "review", "consult", "check", "promulgate"]
    if case["kind"] == "amend":
        kinds[1:1] = ["compare"]
        kinds.append("refs")
    else:
        kinds += ["structure", "similar", "delegation"]
    return [{"kind": k, "name": DOC_NAMES[k]} for k in kinds]
