"""규정 제·개정 에이전트 웹 서버."""
import base64
import io
import json
import re
import shutil
import time
import uuid
import zipfile
from datetime import date
from pathlib import Path

from core import regparse, webcommon
from core.config import Config
from core.export import export, to_hwpx
from core.llm import LLM
from core.loaders import SUPPORTED, load_bytes
from core.server import App, HttpError, file_response
from core.store import Store

import amend
import docs as doclib
import enact
import impact
import lint
import procedure
from library import LAYERS, Library, norm_name

VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_data"

cfg = Config(HERE, {"app_title": "규정 제·개정 에이전트", "port": 8103})
llm = LLM(cfg["llm"])
store = Store(cfg.data_dir)
LIB_ROOT = store.path("library")
app = App(HERE / "static")
webcommon.register(app, cfg, llm, "규정 제·개정 에이전트", VERSION)

DEFAULT_SETTINGS = {"org_name": "가람구시설관리공단", "head": "이사장", "dept": "기획예산팀",
                    "phone": "【연락처】", "email": "【전자우편】", "terms": lint.DEFAULT_TERMS}
STATE = {"lib": None, "impact": None}


def settings():
    s = dict(DEFAULT_SETTINGS)
    s.update(cfg.get("org") or {})
    s.update(store.read("settings.json", {}))
    return s


def today():
    t = cfg.get("today")
    return date.fromisoformat(t) if t else date.today()


def log(action, detail=""):
    store.append_log("history.jsonl", {"action": action, "detail": detail})


def lib(reload=False):
    if STATE["lib"] is None or reload:
        if not any(p.is_file() for p in LIB_ROOT.rglob("*")) if LIB_ROOT.exists() else True:
            load_sample()
        STATE["lib"] = Library(LIB_ROOT, settings()["org_name"]).load()
    return STATE["lib"]


def load_sample():
    for layer in LAYERS:
        src = SAMPLE / "library" / layer
        if src.exists():
            shutil.copytree(src, LIB_ROOT / layer, dirs_exist_ok=True)
    log("시연용 모의 규정집 불러오기", str(SAMPLE / "library"))


def entry_or_404(eid):
    e = lib().get(eid)
    if not e:
        raise HttpError(404, "규정집에 없는 문서입니다")
    return e


def _archive(path):
    """규정집 사본을 바꾸기 전에 이전 판을 보관한다."""
    if path.exists():
        dst = store.path(f"archive/{time.strftime('%Y%m%d-%H%M%S')}/{path.relative_to(LIB_ROOT)}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
        return dst
    return None


# ------------------------------------------------------------ 규정집

@app.get("/api/library")
def library_list(req):
    L = lib()
    return {"entries": [e.summary() for e in L.entries], "layers": LAYERS, "numbering": procedure.numbering_default(L),
            "settings": settings(), "today": today().isoformat()}


@app.get("/api/library/<eid>")
def library_get(req):
    return entry_or_404(req.params["eid"]).detail()


@app.get("/api/library/<eid>/file")
def library_file(req):
    e = entry_or_404(req.params["eid"])
    return file_response((LIB_ROOT / e.layer / e.rel).read_bytes(), Path(e.rel).name)


@app.post("/api/library/sample")
def library_sample(req):
    load_sample()
    lib(reload=True)
    return library_list(req)


@app.post("/api/library/<layer>/upload")
def library_upload(req):
    layer = req.params["layer"]
    if layer not in LAYERS:
        raise HttpError(400, "층 이름이 올바르지 않습니다")
    saved, skipped = [], []
    for f in req.json.get("files", []):
        name = Path(f["path"]).name
        if Path(name).suffix.lower() not in SUPPORTED:
            skipped.append(name)
            continue
        dst = LIB_ROOT / layer / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        _archive(dst)
        dst.write_bytes(base64.b64decode(f["b64"]))  # 원본이 아닌 작업 폴더 사본에만 쓴다
        saved.append(name)
    lib(reload=True)
    log("규정집 등록", f"{LAYERS[layer]}: {', '.join(saved)}")
    return {"saved": saved, "skipped": skipped}


@app.post("/api/library/<layer>/text")
def library_text(req):
    """법령·조례 본문을 붙여넣어 등록(국가법령정보센터 등에서 복사한 글)."""
    layer = req.params["layer"]
    name = re.sub(r"[\\/:*?\"<>|]", "", req.json.get("name", "").strip())
    text = req.json.get("text", "")
    if layer not in LAYERS or not name or not text.strip():
        raise HttpError(400, "이름과 본문을 넣어 주세요")
    if not text.strip().startswith(name):
        text = name + "\n" + text
    dst = LIB_ROOT / layer / f"{name}.txt"
    dst.parent.mkdir(parents=True, exist_ok=True)
    _archive(dst)
    dst.write_text(text, encoding="utf-8")
    lib(reload=True)
    log("규정집 등록(붙여넣기)", name)
    return {"saved": dst.name}


@app.delete("/api/library/<eid>")
def library_delete(req):
    e = entry_or_404(req.params["eid"])
    p = LIB_ROOT / e.layer / e.rel
    _archive(p)
    p.unlink()
    lib(reload=True)
    log("규정집에서 빼기(사본)", e.rel)
    return {"ok": True}


@app.get("/api/settings")
def get_settings(req):
    return settings()


@app.post("/api/settings")
def set_settings(req):
    s = {k: req.json.get(k, v) for k, v in DEFAULT_SETTINGS.items()}
    store.write("settings.json", s)
    lib(reload=True)
    log("기관 설정 저장", s["org_name"])
    return s


@app.get("/api/procedure")
def get_procedure(req):
    L = lib()
    return {**procedure.extract(L), "numbering": procedure.numbering_default(L), "doc_names": procedure.DOC_NAMES}


# ------------------------------------------------------------ 제정

def _delegation(body):
    d = body.get("delegation") or {}
    if d.get("entry_id") and d.get("key"):
        e = entry_or_404(d["entry_id"])
        a = e.reg.get(d["key"])
        if not a:
            raise HttpError(400, "위임 조항을 찾지 못했습니다")
        text, label = a.plain, a.label
        if d.get("para"):
            p = next((p for p in a.paras if p.no == int(d["para"])), None)
            if p:
                text, label = p.text, f"{a.label}제{p.no}항"
        return {"entry_id": e.id, "key": a.key, "para": d.get("para"), "law": e.short, "label": label, "text": text}
    if d.get("text"):
        return {"law": d.get("law", ""), "label": d.get("label", ""), "text": d["text"]}
    return None


@app.post("/api/enact/draft")
def enact_draft(req):
    b = req.json
    if not b.get("title") or not b.get("purpose"):
        raise HttpError(400, "규정 이름과 제정 목적을 넣어 주세요")
    s = settings()
    deleg = _delegation(b)
    d = enact.make(lib(), b["title"].strip(), b["purpose"].strip(), deleg, b.get("matters"), s["head"], s["org_name"],
                   llm if b.get("use_llm", True) else None)
    d["delegation"] = deleg or {}
    d["purpose"] = b["purpose"].strip()
    d["purpose_phrase"] = enact.purpose_phrase(b["purpose"])
    d["matters_all"] = [{"key": k, "title": t.format(core=d["core"].split()[-1]), "on": k in d["matters"]} for k, t, _, _ in enact.MATTERS]
    log("제정 초안 작성", f"{d['title']} ({d['engine']})")
    return d


@app.post("/api/enact/rebuild")
def enact_rebuild(req):
    d = req.json["draft"]
    enact.rebuild_text(d)
    d["similar"] = enact.compare_similar(lib(), d)
    d["delegation_check"] = enact.check_delegation(lib(), d, d.get("delegation"))
    return d


@app.get("/api/enact/matters")
def enact_matters(req):
    purpose = req.query.get("purpose", "")
    title = req.query.get("title", "")
    on = enact.suggest_matters(purpose, title)
    core = enact.core_of(title, settings()["org_name"]).split()[-1] if title else "대상"
    return {"matters": [{"key": k, "title": t.format(core=core), "on": k in on} for k, t, _, _ in enact.MATTERS]}


# ------------------------------------------------------------ 개정

@app.post("/api/amend/plan")
def amend_plan(req):
    b = req.json
    e = entry_or_404(b["entry_id"])
    if not (b.get("intent") or "").strip():
        raise HttpError(400, "개정 의도를 적어 주세요")
    p = amend.plan(b["intent"], e, lib(), llm if b.get("use_llm", True) else None, settings()["head"])
    for op in p["ops"]:
        op["desc"] = amend.describe(op, e.reg)
    log("개정 의도 해석", f"{e.short}: {b['intent'][:60]} → 작업 {len(p['ops'])}개 ({p['engine']})")
    return p


def _preview(e, ops, mode, effective):
    L = lib()
    ops = amend.deep(ops)
    res = amend.apply(e, ops, mode, effective, L)
    prop = amend.propagate(L, e, res["mapping"], ops)
    rows = amend.compare_rows(e.reg, res["reg"], res["mapping"])
    text = amend.amendment_text(e, res, ops, mode, effective)
    for op in ops:
        op["desc"] = amend.describe(op, e.reg)
    for f in prop["fixes"] + prop["reviews"]:
        f.pop("entry", None)
    # 개정 후 규정에 남는 점검 문제(이 규정만)
    tmp = _TmpEntry(e, res["reg"])
    issues = [i for i in lint._numbering(tmp) + lint._internal_refs(tmp)]
    return {"entry_id": e.id, "title": e.title, "short": e.short, "mode": mode, "effective": effective,
            "ops": ops, "errors": res["errors"], "mapping": res["mapping"], "changes": res["changes"],
            "propagate": prop, "compare": rows, "amendment": text,
            "old_text": e.reg.to_text(), "new_text": res["reg"].to_text(), "lint": issues}


class _TmpEntry:
    def __init__(self, e, reg):
        self.reg, self.short, self.id, self.aliases, self.title = reg, e.short, e.id, e.aliases, e.title


@app.post("/api/amend/preview")
def amend_preview(req):
    b = req.json
    e = entry_or_404(b["entry_id"])
    return _preview(e, b.get("ops", []), b.get("mode", "branch"), b.get("effective") or None)


# ------------------------------------------------------------ 진행 관리(건)

def _cases():
    return sorted((store.read(f"cases/{n}", {}) for n in store.list("cases") if n.endswith(".json")),
                  key=lambda c: c.get("created", ""), reverse=True)


def _save_case(c):
    store.write(f"cases/{c['id']}.json", c)


def _get_case(cid):
    c = store.read(f"cases/{cid}.json")
    if not c:
        raise HttpError(404, "없는 건입니다")
    return c


def _upper_refs(e):
    L = lib()
    out = set()
    for loc, akey, text in L.units(e):
        for r in L.refs_in(e, text):
            if r["law"] and L.by_name(r["law"]) and L.by_name(r["law"]).layer == "upper":
                out.add(f"{loc}: 「{r['law']}」 {regparse.alabel(*regparse.parse_key(r['key']))}")
    return sorted(out)


def create_case(kind, payload, source="manual", law=None, reason=None):
    L = lib()
    proc = procedure.extract(L)
    cid = time.strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6]
    c = {"id": cid, "kind": kind, "created": time.strftime("%Y-%m-%d %H:%M"), "source": source, "law": law,
         "steps": procedure.new_case_steps(proc, kind), "history": [], "docs": [], "proc_source": proc["source"]}
    if reason:
        c["reason"] = reason
    if kind == "enact":
        d = payload
        c.update({"title": d["title"], "short": d["title"].replace(settings()["org_name"], "").strip(), "enact": d,
                  "new_text": d["reg_text"], "lint": [], "upper_refs": [f"「{d['delegation']['law']}」 {d['delegation']['label']}"] if d.get("delegation", {}).get("law") else []})
    else:
        p = payload
        e = entry_or_404(p["entry_id"])
        c.update({k: p[k] for k in ("entry_id", "title", "short", "mode", "effective", "ops", "mapping", "changes",
                                     "propagate", "compare", "amendment", "old_text", "new_text", "lint")})
        c["form_fixes"] = p.get("form_fixes", {})
        c["upper_refs"] = _upper_refs(e)
    for s in c["steps"]:
        if s["status"] == "doing":
            procedure.set_step(c, s["id"], "doing", today=today())
    c["history"].append({"at": time.strftime("%Y-%m-%d %H:%M"), "what": "건 등록", "detail": f"{'제정' if kind == 'enact' else '개정'}: {c['title']}"})
    _save_case(c)
    log("제·개정 건 등록", f"{c['title']} ({'제정' if kind == 'enact' else '개정'})")
    return c


def _case_view(c):
    v = dict(c)
    v["next"] = procedure.next_action(c)
    v["available_docs"] = doclib.available(c)
    return v


@app.get("/api/cases")
def cases_list(req):
    return {"cases": [{"id": c["id"], "kind": c["kind"], "title": c["title"], "created": c["created"],
                       "next": (procedure.next_action(c) or {}).get("text"), "done": all(s["status"] in ("done", "skipped") for s in c["steps"]),
                       "published": c.get("published")} for c in _cases()]}


@app.post("/api/cases")
def cases_create(req):
    b = req.json
    if b.get("kind") == "enact":
        c = create_case("enact", b["draft"])
    else:
        e = entry_or_404(b["entry_id"])
        p = _preview(e, b.get("ops", []), b.get("mode", "branch"), b.get("effective") or None)
        p["form_fixes"] = b.get("form_fixes", {})
        c = create_case("amend", p, b.get("source", "manual"), b.get("law"), b.get("reason"))
    return _case_view(c)


@app.get("/api/cases/<cid>")
def cases_get(req):
    return _case_view(_get_case(req.params["cid"]))


@app.delete("/api/cases/<cid>")
def cases_delete(req):
    c = _get_case(req.params["cid"])
    store.path(f"cases/{c['id']}.json").unlink()
    log("건 삭제", c["title"])
    return {"ok": True}


@app.post("/api/cases/<cid>/step")
def cases_step(req):
    c = _get_case(req.params["cid"])
    b = req.json
    s = procedure.set_step(c, b["step_id"], b["status"], b.get("date"), b.get("note"), today())
    c["history"].append({"at": time.strftime("%Y-%m-%d %H:%M"), "what": f"{s['title']} {({'done': '완료', 'skipped': '생략', 'doing': '시작', 'todo': '되돌림'})[b['status']]}",
                         "detail": b.get("note") or ""})
    _save_case(c)
    log("절차 단계 처리", f"{c['title']}: {s['title']} → {b['status']}")
    return _case_view(c)


@app.post("/api/cases/<cid>/doc/<kind>")
def cases_doc(req):
    c = _get_case(req.params["cid"])
    kind = req.params["kind"]
    fmt = req.query.get("fmt", "hwpx")
    try:
        blocks = doclib.build(c, kind, settings(), today(), c["steps"])
    except KeyError:
        raise HttpError(400, "이 건에서 만들 수 없는 문서입니다")
    data, ctype = export(blocks, fmt)
    name = f"{c['short']}_{procedure.DOC_NAMES[kind].split('(')[0]}.{fmt}"
    out = store.path(f"outputs/{c['id']}/{name}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    c["docs"].append({"kind": kind, "name": name, "at": time.strftime("%Y-%m-%d %H:%M")})
    c["history"].append({"at": time.strftime("%Y-%m-%d %H:%M"), "what": "문서 작성", "detail": name})
    _save_case(c)
    log("문서 작성", name)
    return file_response(data, name, ctype)


@app.get("/api/cases/<cid>/preview/<kind>")
def cases_doc_preview(req):
    c = _get_case(req.params["cid"])
    return {"blocks": doclib.build(c, req.params["kind"], settings(), today(), c["steps"])}


def _form_copies(c):
    """서식 정정 사본(원본 서식은 그대로)."""
    L = lib()
    out = []
    repl_by = {}
    for f in c.get("propagate", {}).get("fixes", []):
        if f["layer"] == "forms" and f.get("new_text"):
            repl_by.setdefault(f["entry_id"], []).append((f["text"], f["new_text"]))
    for eid, pairs in (c.get("form_fixes") or {}).items():
        repl_by.setdefault(eid, []).extend([tuple(p) for p in pairs])
    for eid, pairs in repl_by.items():
        e = L.get(eid)
        if not e or e.ext not in (".hwpx", ".docx"):
            continue
        data, n = amend.fix_form_file((LIB_ROOT / e.layer / e.rel).read_bytes(), pairs)
        if n:
            out.append((e, data))
    return out


@app.post("/api/cases/<cid>/bundle")
def cases_bundle(req):
    c = _get_case(req.params["cid"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for d in doclib.available(c):
            data, _ = export(doclib.build(c, d["kind"], settings(), today(), c["steps"]), "hwpx")
            z.writestr(f"{d['name'].split('(')[0]}.hwpx", data)
        z.writestr(f"{c['short']}(전문).hwpx", to_hwpx([("p", l) for l in c["new_text"].split("\n") if l.strip()]))
        for e, data in _form_copies(c):
            z.writestr(f"서식 정정본/{Path(e.rel).name}", data)
    name = f"{c['short']}_{'제정' if c['kind'] == 'enact' else '개정'}문서세트.zip"
    out = store.path(f"outputs/{c['id']}/{name}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())
    log("문서 세트 내려받기", name)
    return file_response(buf.getvalue(), name, "application/zip")


@app.post("/api/cases/<cid>/publish")
def cases_publish(req):
    """공포된 규정을 규정집(작업 폴더 사본)에 반영한다. 이전 판은 보관 폴더로 옮겨 둔다."""
    c = _get_case(req.params["cid"])
    L = lib()
    if c["kind"] == "amend":
        e = entry_or_404(c["entry_id"])
        old = LIB_ROOT / e.layer / e.rel
        _archive(old)
        new_path = old.with_suffix(".hwpx")
        new_path.write_bytes(to_hwpx([("p", l) for l in c["new_text"].split("\n") if l.strip()]))
        if new_path != old:
            old.unlink()
    else:
        name = re.sub(r"[\\/:*?\"<>|]", "", c["short"]) + ".hwpx"
        new_path = LIB_ROOT / "org" / name
        _archive(new_path)
        new_path.write_bytes(to_hwpx([("p", l) for l in c["new_text"].split("\n") if l.strip()]))
    fixed = []
    for e, data in _form_copies(c):
        p = LIB_ROOT / e.layer / e.rel
        _archive(p)
        p.write_bytes(data)
        fixed.append(e.short)
    c["published"] = time.strftime("%Y-%m-%d %H:%M")
    c["history"].append({"at": c["published"], "what": "규정집 반영", "detail": new_path.name + (f", 서식 정정: {', '.join(fixed)}" if fixed else "")})
    _save_case(c)
    lib(reload=True)
    log("규정집 반영", f"{c['title']} (이전 판 보관)")
    return {"ok": True, "file": new_path.name, "forms": fixed}


@app.post("/api/cases/<cid>/related")
def cases_related(req):
    """조 번호 이동으로 인용을 고쳐야 하는 다른 규정마다 개정 건을 만든다."""
    c = _get_case(req.params["cid"])
    made = []
    by = {}
    for f in c.get("propagate", {}).get("fixes", []):
        if f["layer"] == "org" and f.get("article"):
            by.setdefault(f["entry_id"], []).append(f)
    for eid, fixes in by.items():
        e = entry_or_404(eid)
        ops = []
        for f in fixes:
            a = e.reg.get(f["article"])
            for path, loc, text in amend.walk(a):
                rf = amend.RefFinder(e.aliases)
                names = {norm_name(c["title"], lib().org_name), norm_name(c["short"], lib().org_name)}
                moved = {k: v for k, v in c["mapping"].items() if v != k}
                new, ch = amend.fix_refs(text, rf, moved, names)
                if ch and new != text:
                    ops.append({"type": "replace", "article": a.key, "path": list(path), "find": text, "repl": new, "anchor": text,
                                "all": False, "loc": a.label + loc})
        if ops:
            p = _preview(e, ops, procedure.numbering_default(lib())["mode"], None)
            nc = create_case("amend", p, "related", reason=f"「{c['short']}」 개정에 따른 조 번호 이동으로 인용 조문을 정비하려는 것임.")
            made.append({"id": nc["id"], "title": nc["title"]})
    return {"made": made}


# ------------------------------------------------------------ 상위법 영향 분석

@app.post("/api/impact")
def impact_run(req):
    b = req.json
    law = entry_or_404(b["law_id"])
    if b.get("sample"):
        files = sorted((SAMPLE / "revision").glob("*"))
        f = next((p for p in files if norm_name(law.short) in norm_name(p.stem)), None)
        if not f:
            raise HttpError(404, "이 법령의 시연용 개정본이 없습니다")
        data, name = f.read_bytes(), f.name
    elif b.get("text"):
        data, name = b["text"].encode("utf-8"), "붙여넣은 개정본.txt"
    elif b.get("file"):
        data, name = base64.b64decode(b["file"]["b64"]), b["file"]["path"]
    else:
        raise HttpError(400, "개정 후 법령 파일이나 본문을 넣어 주세요")
    doc = load_bytes(data, name)
    if doc.error:
        raise HttpError(400, f"개정본을 읽지 못했습니다: {doc.error}")
    new = regparse.parse([(x.loc, x.text) for x in doc.blocks])
    if not new.articles:
        raise HttpError(400, "개정본에서 조문을 찾지 못했습니다")
    r = impact.analyze(lib(), law, new)
    STATE["impact"] = {"law_id": law.id, "law": law.short, "new_name": name, "new_data": base64.b64encode(data).decode(),
                       "candidates": r["candidates"], "new_title": new.title}
    log("상위법 영향 분석", f"{law.short} ← {name}: 후보 {len(r['candidates'])}건")
    return {"law": law.short, "new_name": name, "changes": r["diff"]["changes"], "candidates": r["candidates"]}


@app.post("/api/impact/cases")
def impact_cases(req):
    st = STATE["impact"]
    if not st:
        raise HttpError(400, "먼저 영향 분석을 실행하세요")
    ids = set(req.json.get("ids", []))
    regs, forms = impact.ops_for(st["candidates"], ids)
    L = lib()
    made = []
    mode = procedure.numbering_default(L)["mode"]
    for eid, ops in regs.items():
        e = entry_or_404(eid)
        p = _preview(e, ops, mode, None)
        sn = norm_name(e.short, L.org_name)
        p["form_fixes"] = {fid: pairs for fid, pairs in forms.items() if L.get(fid) and L.get(fid).form_of and norm_name(L.get(fid).form_of, L.org_name) == sn}
        c = create_case("amend", p, "impact", st["law"])
        made.append({"id": c["id"], "title": c["title"], "ops": len(ops)})
    log("영향 분석 결과로 개정 건 등록", ", ".join(m["title"] for m in made))
    return {"made": made}


@app.post("/api/impact/replace_law")
def impact_replace(req):
    st = STATE["impact"]
    if not st:
        raise HttpError(400, "먼저 영향 분석을 실행하세요")
    law = entry_or_404(st["law_id"])
    p = LIB_ROOT / law.layer / law.rel
    _archive(p)
    data = base64.b64decode(st["new_data"])
    newp = p.with_suffix(Path(st["new_name"]).suffix.lower())
    newp.write_bytes(data)
    if newp != p:
        p.unlink()
    lib(reload=True)
    log("상위 법령 새 판으로 교체", f"{law.short} (이전 판 보관)")
    return {"ok": True}


# ------------------------------------------------------------ 전체 점검

@app.get("/api/lint")
def lint_run(req):
    r = lint.check(lib(), settings().get("terms"))
    log("규정집 전체 점검", json.dumps(r["summary"], ensure_ascii=False))
    return r


@app.post("/api/lint/export")
def lint_export(req):
    r = lint.check(lib(), settings().get("terms"))
    fmt = req.query.get("fmt", "hwpx")
    blocks = [("title", "규정집 전체 점검 결과"),
              ("p", f"점검일: {today().isoformat()} · 문서 {r['n_docs']}건 · 규정 {r['n_regs']}건 · 조문 {r['n_articles']}개"),
              ("p", " · ".join(f"{k} {v}건" for k, v in r["summary"].items()) or "문제 없음"),
              ("table", ["수준", "종류", "문서", "위치", "내용"], [[i["level"], i["kind"], i["doc"], i["loc"], i["message"]] for i in r["issues"]], [1, 1, 2, 1, 5])]
    data, ctype = export(blocks, fmt)
    return file_response(data, f"규정집점검결과.{fmt}", ctype)


@app.post("/api/lint/fix")
def lint_fix(req):
    """표기 정정(규정명·용어)을 개정 건으로 만든다."""
    b = req.json
    e = entry_or_404(b["entry_id"])
    f = b["fix"]
    op = {"type": "replace", "article": None, "find": f["find"], "repl": f["repl"], "all": True}
    m = re.match(r"제(\d+)조(?:의(\d+))?$", b.get("loc", ""))
    if m:
        op["article"] = regparse.akey(int(m.group(1)), int(m.group(2) or 0))
    p = _preview(e, [op], procedure.numbering_default(lib())["mode"], None)
    c = create_case("amend", p, "lint", reason=f"{e.short}의 표기를 정비하려는 것임.")
    return _case_view(c)


# ------------------------------------------------------------ 에이전트(자연어 한 줄로 시작)

RX_TITLE = re.compile(r"「([^」]+)」|([가-힣A-Za-z0-9·]+(?:\s+[가-힣A-Za-z0-9·]+){0,5}\s*(?:지침|규정|세칙|요령|기준|규칙))")
RX_DELEG = re.compile(r"(「[^」]+」|[가-힣]+\s*조례|조례|[가-힣]+법)\s*제(\d+)조(?:의(\d+))?(?:\s*제(\d+)항)?")


@app.post("/api/agent")
def agent(req):
    text = (req.json.get("text") or "").strip()
    if not text:
        raise HttpError(400, "무엇을 할지 적어 주세요")
    L = lib()
    steps = []
    t = re.sub(r"\s", "", text)
    if re.search(r"점검|검사|오류찾|오류를찾|확인해줘|전체확인", t) and not re.search(r"제정|개정안|바꾸|신설", t):
        r = lint.check(L, settings().get("terms"))
        steps.append({"title": "규정집 전체 점검", "detail": f"문서 {r['n_docs']}건, 조문 {r['n_articles']}개를 점검했습니다. " + (" · ".join(f"{k} {v}건" for k, v in r["summary"].items()) or "문제 없음")})
        return {"route": "lint", "steps": steps, "result": r}
    if re.search(r"상위법|영향|조례가개정|조례개정|법령개정|법이개정|개정됐|개정되었", t):
        law = None
        for e in L.regs("upper"):
            if norm_name(e.short) in t:
                law = e
        law = law or (L.regs("upper")[0] if L.regs("upper") else None)
        if not law:
            raise HttpError(400, "규정집에 상위 법령이 없습니다. 규정집 화면에서 먼저 등록하세요")
        steps.append({"title": "상위 법령 확인", "detail": f"{law.short}"})
        return {"route": "impact", "steps": steps, "law_id": law.id,
                "message": "개정 후 법령 파일을 넣거나(상위법 영향 화면) 시연용 개정본으로 바로 분석할 수 있습니다."}
    if re.search(r"제정|새로만들|새로제정|신규|만들려|만들어야|만들고싶", t):
        m = RX_TITLE.search(text)
        title = (m.group(1) or m.group(2)).strip() if m else ""
        title = re.sub(r"^(?:새로|새|신규)\s*", "", title)
        deleg = None
        dm = RX_DELEG.search(text)
        if dm:
            law_name = dm.group(1).strip("「」 ")
            law = L.by_name(law_name) or next((e for e in L.regs("upper") if law_name in e.short or (law_name == "조례" and e.kind == "조례")), None)
            if law and law.reg.get(regparse.akey(int(dm.group(2)), int(dm.group(3) or 0))):
                deleg = {"entry_id": law.id, "key": regparse.akey(int(dm.group(2)), int(dm.group(3) or 0)), "para": dm.group(4)}
        m2 = re.search(r"목적은|목적:", text)
        purpose = text[m2.end():].strip() if m2 else text
        purpose = re.sub(r"\s*(?:위해서|위해|위함)\s*[.!]?$", "", purpose) or text
        if not title:
            raise HttpError(400, "만들 규정의 이름을 알려 주세요. 예: \"체육시설 정기대관 운영 지침을 새로 만들려고 해\"")
        steps.append({"title": "제정 대상 확인", "detail": f"{title}" + (f" · 위임 조항 {dm.group(0)}" if deleg else " · 위임 조항 미지정")})
        b = {"title": title, "purpose": purpose, "delegation": deleg or {}}
        d = enact.make(L, title, purpose, _delegation(b), None, settings()["head"], settings()["org_name"], llm)
        d["delegation"] = _delegation(b) or {}
        d["purpose"] = purpose
        d["purpose_phrase"] = enact.purpose_phrase(purpose)
        d["matters_all"] = [{"key": k, "title": tt.format(core=d["core"].split()[-1]), "on": k in d["matters"]} for k, tt, _, _ in enact.MATTERS]
        steps.append({"title": "유사 규정 찾기", "detail": ", ".join(r["title"] for r in d["similar"]["regs"]) or "찾지 못함"})
        steps.append({"title": "조문 체계·표준 조문 초안", "detail": f"{len(d['articles'])}개 조 ({d['engine']})"})
        steps.append({"title": "위임 범위 검토", "detail": " / ".join(f"{i['item']}: {i['level']}" for i in d["delegation_check"]["items"][:4])})
        c = create_case("enact", d)
        nxt = procedure.next_action(c)
        steps.append({"title": "절차 안내", "detail": nxt["text"] if nxt else ""})
        return {"route": "enact", "steps": steps, "draft": d, "case": _case_view(c)}
    # 개정
    e = L.guess(text)
    if not e:
        raise HttpError(400, "어느 규정을 고칠지 알려 주세요. 예: \"체육시설 운영 규정에서 대관 사용료 납부 기한을 7일에서 5일로 줄여줘\"")
    steps.append({"title": "개정 대상 규정 찾기", "detail": e.short})
    p = amend.plan(text, e, L, llm, settings()["head"])
    if not p["ops"]:
        return {"route": "amend", "steps": steps + [{"title": "개정 의도 해석", "detail": " ".join(p["notes"]) or "개정 내용을 찾지 못했습니다"}],
                "entry_id": e.id, "plan": p}
    steps.append({"title": "개정 의도 해석", "detail": f"작업 {len(p['ops'])}개 ({p['engine']}): " + "; ".join(amend.describe(o, e.reg) for o in p["ops"])})
    mode = procedure.numbering_default(L)
    pv = _preview(e, p["ops"], mode["mode"], p["effective"])
    steps.append({"title": "개정안·신구조문대비표 작성", "detail": f"번호 방식: {'가지 번호' if mode['mode'] == 'branch' else '번호 이동'} ({mode['basis']}) · 대비표 {len(pv['compare'])}행"})
    pr = pv["propagate"]
    steps.append({"title": "인용하는 다른 규정·별표·서식 찾기", "detail": f"자동 정정 {len(pr['fixes'])}곳, 확인 필요 {len(pr['reviews'])}곳"})
    c = create_case("amend", pv)
    nxt = procedure.next_action(c)
    steps.append({"title": "절차 안내", "detail": nxt["text"] if nxt else ""})
    return {"route": "amend", "steps": steps, "plan": p, "preview": pv, "case": _case_view(c)}


@app.get("/api/history")
def history(req):
    return {"history": store.read_log("history.jsonl")[::-1][:300]}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="규정 제·개정 에이전트 실행")
    ap.add_argument("--port", type=int, default=cfg["port"])
    ap.add_argument("--host", default=cfg["host"])
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    lib()
    app.run(a.host, a.port, not a.no_browser, "규정 제·개정 에이전트")


if __name__ == "__main__":
    main()
