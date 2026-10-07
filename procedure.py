"""제·개정 절차: 기관의 규정관리규정에서 절차 단계·기간·필요 문서를 읽는다(없으면 일반 절차)."""
import re
from datetime import date, timedelta

DOC_NAMES = {
    "draft": "조문안(제정안·개정안, 개정문)",
    "reason": "제정·개정 이유서",
    "compare": "신구조문대비표",
    "consult": "관계부서 협의 요청서",
    "check": "법무 검토 요청서(점검 결과)",
    "notice": "행정예고문",
    "review": "규정심의위원회 심의 안건",
    "promulgate": "공포문",
    "refs": "관계 규정·서식 정비 목록",
    "similar": "유사 규정 비교표",
    "delegation": "위임 범위 검토표",
    "structure": "조문 체계표",
}
DOC_RULES = [
    (r"제정·개정안|개정안|제정안|규정안", "draft"),
    (r"이유서", "reason"),
    (r"신구조문대비표", "compare"),
    (r"관계\s*부서|협의|의견을\s*들", "consult"),
    (r"검토", "check"),
    (r"예고", "notice"),
    (r"심의", "review"),
    (r"공포", "promulgate"),
]

GENERIC = [
    ("입안", "제정·개정안, 이유서, 신구조문대비표(개정)를 작성한다.", ["draft", "reason", "compare"], None),
    ("관계부서 협의", "관계부서 의견을 듣는다.", ["consult", "refs"], None),
    ("법무 검토", "상위 법령 위반·다른 규정과의 충돌 여부를 검토받는다.", ["check"], None),
    ("행정예고", "권리·의무와 관련되면 예고하고 의견을 받는다.", ["notice"], None),
    ("심의", "규정심의위원회 등 심의를 받는다.", ["review"], None),
    ("결재·공포", "기관장 결재 후 공포한다.", ["promulgate"], None),
    ("규정집 반영", "공포된 규정을 규정집에 반영하고 알린다.", [], None),
]


def find_mgmt(lib):
    for e in lib.regs("org"):
        if re.search(r"규정\s*관리|법규\s*관리|규정\s*운영|사규\s*관리", e.title):
            return e
    return None


def numbering_default(lib):
    e = find_mgmt(lib)
    if e:
        t = e.reg.to_text()
        if re.search(r"가지\s*번호|가지번호|가지\s*조문", t):
            m = next((a for a in e.reg.articles if re.search(r"가지\s*번호|가지번호|가지\s*조문", a.plain)), None)
            return {"mode": "branch", "basis": f"{e.short} {m.label}" if m else e.short}
        return {"mode": "renumber", "basis": e.short}
    return {"mode": "branch", "basis": "일반 관행(조 번호를 유지하는 가지 번호)"}


def extract(lib):
    e = find_mgmt(lib)
    steps = []
    if e:
        arts = [a for a in e.reg.articles if a.chapter and "절차" in a.chapter]
        if not arts:
            arts = [a for a in e.reg.articles if re.search(r"입안|협의|검토|예고|심의|공포|반영", a.title)]
        for a in arts:
            text = a.plain
            docs = []
            for rx, kind in DOC_RULES:
                if re.search(rx, a.title + " " + text) and kind not in docs:
                    docs.append(kind)
            if a.title and "입안" in a.title:
                docs = [d for d in ("draft", "reason", "compare") if d in docs or d == "draft"]
            if "협의" in a.title:
                docs = ["consult", "refs"]
            if "규정집" in a.title or "반영" in a.title:
                docs = []
            days = None
            m = re.search(r"(\d+)일\s*이상", text)
            if m:
                days = {"n": int(m.group(1)), "kind": "이상"}
            m = re.search(r"(\d+)일\s*이내", text)
            if m and not days:
                days = {"n": int(m.group(1)), "kind": "이내"}
            optional = None
            m = re.search(r"다만,?\s*([^.]*?생략할 수 있다)", text)
            if m:
                optional = m.group(1).strip()
            cond = None
            m = re.match(r"^\s*(?:①\s*)?(.{4,60}?경우에는)", text)
            if m:
                cond = m.group(1)
            steps.append({"id": f"s{len(steps) + 1}", "title": a.title, "action": re.split(r"(?<=다)\.\s", text)[0].strip().rstrip(".") + ".",
                          "basis": {"reg": e.short, "label": a.label}, "docs": docs, "days": days,
                          "optional": optional, "condition": cond})
    if not steps:
        for t, act, docs, days in GENERIC:
            steps.append({"id": f"s{len(steps) + 1}", "title": t, "action": act, "basis": {"reg": "일반 절차", "label": ""},
                          "docs": docs, "days": days, "optional": None, "condition": None})
    return {"source": e.short if e else None, "steps": steps}


def new_case_steps(proc, kind):
    out = []
    for s in proc["steps"]:
        s = dict(s)
        s["docs"] = [d for d in s["docs"] if not (d == "compare" and kind == "enact")]
        if kind == "enact" and s["docs"] and "draft" in s["docs"]:
            s["docs"] = s["docs"] + [d for d in ("structure", "similar", "delegation") if d not in s["docs"]]
        s.update({"status": "todo", "started": None, "done_at": None, "due": None, "note": ""})
        out.append(s)
    if out:
        out[0]["status"] = "doing"
    return out


def set_step(case, step_id, status, on=None, note=None, today=None):
    today = today or date.today()
    on = date.fromisoformat(on) if on else today
    for i, s in enumerate(case["steps"]):
        if s["id"] != step_id:
            continue
        if status == "doing":
            s["started"] = on.isoformat()
            if s.get("days"):
                s["due"] = (on + timedelta(days=s["days"]["n"])).isoformat()
        if status in ("done", "skipped"):
            s["done_at"] = on.isoformat()
            if not s.get("started"):
                s["started"] = on.isoformat()
            # 다음 단계 시작
            for nxt in case["steps"][i + 1:]:
                if nxt["status"] == "todo":
                    nxt["status"] = "doing"
                    nxt["started"] = on.isoformat()
                    if nxt.get("days"):
                        nxt["due"] = (on + timedelta(days=nxt["days"]["n"])).isoformat()
                    break
        s["status"] = status
        if note is not None:
            s["note"] = note
        return s
    raise KeyError(step_id)


def next_action(case):
    cur = next((s for s in case["steps"] if s["status"] == "doing"), None)
    if not cur:
        return None
    msg = f"{cur['title']}: {cur['action']}"
    if cur.get("days"):
        msg += f" (기간 {cur['days']['n']}일 {cur['days']['kind']}" + (f", {cur['due']}까지" if cur.get("due") else "") + ")"
    return {"step": cur, "text": msg, "docs": [{"kind": d, "name": DOC_NAMES[d]} for d in cur["docs"]]}
