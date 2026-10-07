"""구동 점검: 서버를 임시 작업 폴더로 띄워 주요 기능을 차례로 호출한다(모델 없이 규칙 기반).

실행: python tests/smoke_test.py
원본(sample_data)은 읽기만 하며, 작업 데이터는 임시 폴더에 쓰고 끝나면 지운다.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import shutil
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARK = {"baton": "engine.py", "seomu": "assistant.py", "gyujeong": "amend.py"}


def app_dirs():
    out = []
    for name, mark in MARK.items():
        for p in (ROOT / "apps" / name, ROOT):
            if (p / mark).exists():
                out.append((name, p))
                break
    return out


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def call(self, method, path, body=None, raw=False):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            b = r.read()
            return b if raw else json.loads(b)


def check_baton(c):
    yield "화면 열기", c.call("GET", "/", raw=True)[:15].lower().startswith(b"<!doctype")
    h = c.call("POST", "/api/handovers", {"source": {"kind": "sample"}, "predecessor": "김도윤"})
    s = h["draft"]["sections"]
    yield f"인수인계서 초안(업무 {len(s['rnr'])}, 일정 {len(s['schedule'])}, 연락처 {len(s['contacts'])}, 현안 {len(s['issues'])}, 확인 필요 {len(h['draft']['flags'])})", all(len(s[k]) for k in s)
    a = c.call("POST", f"/api/handovers/{h['id']}/ask", {"q": "11월에 할 일 알려줘"})
    yield "후임자 질문", bool(a["answer"])
    for fmt in ("hwpx", "docx", "md"):
        d = c.call("POST", f"/api/handovers/{h['id']}/export/handover?fmt={fmt}", {}, raw=True)
        yield f"인수인계서 내보내기({fmt})", len(d) > 500
    m = c.call("POST", f"/api/handovers/{h['id']}/export/manual?fmt=hwpx", {}, raw=True)
    yield "업무 매뉴얼 내보내기", len(m) > 500


def check_seomu(c):
    yield "화면 열기", c.call("GET", "/", raw=True)[:15].lower().startswith(b"<!doctype")
    p = c.call("GET", "/api/procedures")
    yield f"업무별 절차 카드 {len(p['cards'])}개", len(p["cards"]) >= 4
    r = c.call("POST", "/api/chat", {"text": "어제 부산에서 1박 2일 출장 다녀왔어요. 정산 어떻게 해요?"})
    yield f"상황 안내({r.get('card', {}).get('name')})", r.get("type") == "procedure"
    s1 = c.call("POST", "/api/simulate", {"text": "출장비 정산을 10일 뒤에 제출하면 어떻게 돼?"})
    yield f"업무 시뮬레이터({s1['label']})", s1["verdict"] == "bad" and bool(s1["findings"][0]["basis"])
    s2 = c.call("POST", "/api/simulate", {"text": "점심 메뉴 추천해줘"})
    yield f"업무 시뮬레이터 근거 없음({s2['label']})", s2["verdict"] not in ("ok", "bad")
    pc = c.call("POST", "/api/precheck", {"kind": "goods", "fields": {"item": "노트북", "amount": "2500000", "quotes": 1, "purchased": True}})
    yield f"AI 사전점검({pc['label']})", pc["counts"]["bad"] >= 2
    d = c.call("POST", "/api/check/export?fmt=hwpx", {**pc, "title": "AI 사전점검", "question": "노트북"}, raw=True)
    yield "점검 결과 내보내기", len(d) > 500
    rv = c.call("GET", "/api/revision/sample")
    yield "규정 개정 시연본", bool(rv)


def check_gyujeong(c):
    yield "화면 열기", c.call("GET", "/", raw=True)[:15].lower().startswith(b"<!doctype")
    lib = c.call("GET", "/api/library")
    yield f"규정집 {len(lib['entries'])}건", len(lib["entries"]) >= 5
    r = c.call("POST", "/api/agent", {"text": "체육시설 운영 규정에서 대관 사용료 납부 기한을 7일에서 5일로 줄이고, 제8조 다음에 사용허가 취소에 관한 조문을 신설해줘"})
    yield f"개정(작업 {len(r['plan']['ops'])}개, 대비표 {len(r['preview']['compare'])}행)", len(r["plan"]["ops"]) == 2
    cid = r["case"]["id"]
    for d in r["case"]["available_docs"]:
        b = c.call("POST", f"/api/cases/{cid}/doc/{d['kind']}?fmt=hwpx", {}, raw=True)
        yield f"문서: {d['name']}", len(b) > 500
    z = c.call("POST", f"/api/cases/{cid}/bundle", {}, raw=True)
    yield "문서 세트 ZIP", z[:2] == b"PK"
    e = c.call("POST", "/api/agent", {"text": "체육시설 정기대관 운영 지침을 새로 만들려고 해. 조례 제4조제2항 위임이고, 목적은 정기대관 신청, 대상자 선정, 사용료 납부와 취소 기준을 정하기 위해"})
    yield f"제정 초안({len(e['draft']['articles'])}개 조)", len(e["draft"]["articles"]) >= 6
    upper = [x for x in lib["entries"] if x["layer"] == "upper"][0]
    im = c.call("POST", "/api/impact", {"law_id": upper["id"], "sample": True})
    yield f"상위법 영향 분석(후보 {len(im['candidates'])}곳)", len(im["candidates"]) >= 3
    ln = c.call("GET", "/api/lint")
    yield f"전체 점검({ln['summary']})", ln["summary"].get("오류", 0) >= 1


CHECKS = {"baton": check_baton, "seomu": check_seomu, "gyujeong": check_gyujeong}


def main():
    total = failed = 0
    for name, d in app_dirs():
        tmp = tempfile.mkdtemp(prefix=f"smoke-{name}-")
        port = free_port()
        env = dict(os.environ, APP_DATA_DIR=tmp, LLM_PROVIDER="none")
        proc = subprocess.Popen([sys.executable, "run.py", "--no-browser", "--port", str(port)], cwd=d, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            c = Client(port)
            for _ in range(60):
                try:
                    c.call("GET", "/api/info")
                    break
                except Exception:
                    time.sleep(0.3)
            print(f"\n[{name}] http://127.0.0.1:{port}")
            for label, ok in CHECKS[name](c):
                total += 1
                failed += 0 if ok else 1
                print(f"  {'통과' if ok else '실패'}  {label}")
        except Exception as e:
            total += 1
            failed += 1
            print(f"  실패  {type(e).__name__}: {e}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n결과: {total - failed}/{total} 통과")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
