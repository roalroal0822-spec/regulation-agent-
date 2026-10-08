"""모델 교체 검증: 같은 기능을 여러 모델로 돌려 정상 동작하는지 확인하고 보고서를 남긴다.

예)
  # Ollama 에 모델 두 개를 받아 둔 경우
  python tools/verify_models.py --base-url http://localhost:11434/v1 --models gemma3:4b exaone3.5:7.8b
  # 행정안전부 AI 공통기반 등 OpenAI 호환 주소(키는 환경변수 LLM_API_KEY)
  python tools/verify_models.py --base-url https://.../v1 --models 모델A 모델B
  # 모델 없이 연결 경로만 시험(가짜 모델 서버 자동 실행)
  python tools/verify_models.py --mock

결과: verify_report.md (모델별·기능별 통과 여부, 걸린 시간, 모델 제안 중 검증에서 걸러진 수)
'통과'는 오류 없이 응답했다는 뜻만이 아니라, 결과가 모의자료의 정답(출장지·날짜, 원문 전화번호,
개정 후 조문 내용, 제정 조문 수)과 맞는다는 뜻이다.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def app_dir(name):
    """개발 폴더(apps/<name>)와 배포 저장소(최상위) 구조를 모두 지원."""
    for p in (ROOT / "apps" / name, ROOT):
        if (p / {"baton": "engine.py", "seomu": "assistant.py", "gyujeong": "amend.py"}[name]).exists():
            return p
    return None


def make_llm(provider, base, model):
    from core.llm import LLM
    return LLM({"provider": provider, "base_url": base, "model": model, "api_key_env": "LLM_API_KEY",
                "temperature": 0.1, "timeout": 300})


# ------------------------------------------------------------ 기능별 시험

def check_baton(llm):
    d = app_dir("baton")
    sys.path.insert(0, str(d))
    import engine
    import qa
    from core.loaders import load_folder
    from dataclasses import asdict
    src = next((d / "sample_data").iterdir())
    docs, _ = load_folder(src)
    res = []
    t = time.time()
    draft = engine.analyze(docs, "김도윤", llm)
    by_model = sum(1 for sec in draft["sections"].values() for it in sec if it.get("by") == "model")
    # 모델이 보탠 항목도 원문 대조 검증을 거친 것만 남는다(엔진이 내용 불일치 항목을 버림)
    ok = "모델" in draft["engine"] and not draft["llm_errors"] and by_model > 0
    res.append(("업무바통: 인수인계서 초안(모델 추출 + 근거 검증)", ok, time.time() - t,
                f"모델이 추가한 항목 {by_model}개, 오류 {len(draft['llm_errors'])}건" + (f": {draft['llm_errors'][0][:80]}" if draft["llm_errors"] else "")))
    t = time.time()
    blocks = [asdict(b) for x in docs for b in x.blocks]
    a = qa.answer(draft, blocks, "정민재 연락처 알려줘", llm)
    # 답 품질: 답에 나온 전화번호가 원문에서 '정민재'와 같은 문단에 실제로 있어야 통과
    import re
    phones = set(re.findall(r"0\d{1,2}-\d{3,4}-\d{4}", a["answer"]))
    real = {p for b in blocks if "정민재" in (b.get("text") or "") for p in re.findall(r"0\d{1,2}-\d{3,4}-\d{4}", b["text"])}
    ok_ans = a["mode"] != "규칙 기반" and bool(phones) and phones <= real
    res.append(("업무바통: 후임자 질문 답변(번호가 원문과 일치)", ok_ans, time.time() - t,
                a["answer"][:60].replace("\n", " ") + ("" if ok_ans else f" / 답의 번호 {sorted(phones) or '없음'}, 원문 번호 {sorted(real)}")))
    sys.path.remove(str(d))
    return res


def check_seomu(llm):
    d = app_dir("seomu")
    sys.path.insert(0, str(d))
    import assistant
    from knowledge import Knowledge
    from datetime import date
    kb = Knowledge(d / "sample_data" / "layers").load()
    cards = kb.extract_procedures()
    t = time.time()
    r = assistant.respond(kb, cards, "어제 부산에서 1박 2일 출장 다녀왔어요. 정산 어떻게 해요?", date(2026, 10, 7), llm)
    sl = r.get("slots") or {}
    ok = (r.get("type") == "procedure" and "모델 응답 실패" not in r.get("text", "") and "출장" in r["card"]["name"]
          and sl.get("destination") == "부산" and sl.get("date") == "2026-10-06" and sl.get("stage") == "after")
    sys.path.remove(str(d))
    return [("서무비서: 상황 문장 → 절차 선택·정보 추출(출장지·날짜·단계 일치)", ok, time.time() - t,
             f"선택 절차: {r.get('card', {}).get('name')}, 출장지 {sl.get('destination')}, 날짜 {sl.get('date')}, 단계 {sl.get('stage')}")]


def check_gyujeong(llm):
    d = app_dir("gyujeong")
    sys.path.insert(0, str(d))
    import amend
    import enact
    from library import Library
    lib = Library(d / "sample_data" / "library", "공공기관").load()
    e = lib.by_name("체육시설 운영 규정")
    res = []
    t = time.time()
    p = amend.plan("대관 사용료 납부 기한을 7일에서 5일로 줄이고, 제8조 다음에 사용허가 취소에 관한 조문을 신설한다", e, lib, llm)
    r = amend.apply(e, p["ops"], "branch", None, lib)
    a8 = r["reg"].get("8")
    ok = p["engine"].startswith("모델") and a8 is not None and "5일 이내" in a8.plain and any(k.startswith("8의") for k in r["inserted"])
    dropped = sum(1 for n in p["notes"] if "제외" in n)
    res.append(("규정 제·개정: 개정 의도 → 개정 작업(현행 조문 대조 검증)", ok, time.time() - t,
                f"사용 결과: {p['engine']}, 작업 {len(p['ops'])}개, 검증에서 걸러진 제안 {dropped}개"))
    t = time.time()
    up = lib.regs("upper")[0]
    art = up.reg.get("4")
    deleg = {"law": up.short, "label": "제4조제2항", "text": art.paras[1].text}
    dr = enact.make(lib, "체육시설 정기대관 운영 지침", "정기대관 신청, 대상자 선정, 사용료 납부와 취소 기준을 정하려고 함", deleg, None, "이사장", "공공기관", llm)
    ok = dr["engine"] == "모델" and len(dr["articles"]) >= 5 and "제1조(목적)" in dr["reg_text"]
    res.append(("규정 제·개정: 제정 조문 본문 작성", ok, time.time() - t, f"사용 결과: {dr['engine']}, 조 {len(dr['articles'])}개"))
    sys.path.remove(str(d))
    return res


CHECKS = {"baton": check_baton, "seomu": check_seomu, "gyujeong": check_gyujeong}


def main():
    ap = argparse.ArgumentParser(description="여러 모델로 주요 기능을 돌려 보는 검증 도구")
    ap.add_argument("--provider", default="openai", choices=["openai", "ollama"])
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--models", nargs="+", default=[])
    ap.add_argument("--mock", action="store_true", help="가짜 모델 서버로 연결 경로만 시험")
    ap.add_argument("--out", default=str(ROOT / "verify_report.md"))
    a = ap.parse_args()
    proc = None
    if a.mock:
        port = 18080
        proc = subprocess.Popen([sys.executable, str(ROOT / "tools" / "mock_llm.py"), "--port", str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)
        a.base_url, a.models = f"http://127.0.0.1:{port}/v1", ["mock-a", "mock-b", "mock-gemma", "mock-exaone", "mock-gpt-oss"]
    if not a.models:
        ap.error("--models 로 시험할 모델 이름을 2개 이상 주세요 (또는 --mock)")
    apps = [k for k in CHECKS if app_dir(k)]
    rows = []
    try:
        for m in a.models:
            llm = make_llm(a.provider, a.base_url, m)
            h = llm.health()
            rows.append((m, "연결 확인" + (f" [{h['family']}]" if h.get("family") else ""), h["ok"], h.get("seconds", 0), h.get("detail", "")))
            if not h["ok"]:
                continue
            for k in apps:
                try:
                    for name, ok, sec, note in CHECKS[k](llm):
                        rows.append((m, name, ok, sec, note))
                except Exception as e:
                    rows.append((m, k, False, 0, f"{type(e).__name__}: {e}"))
    finally:
        if proc:
            proc.terminate()
    lines = ["# 모델 교체 검증 보고서", "",
             f"- 실행 시각: {time.strftime('%Y-%m-%d %H:%M')}",
             f"- 연결 방식: {a.provider} / 주소: {a.base_url}",
             f"- 모델: {', '.join(a.models)}" + (" (가짜 모델 서버: 연결 경로와 계열별 응답 차이 처리 시험용이며 실제 모델 성능과 무관)" if a.mock else ""), "",
             "| 모델 | 기능 | 결과 | 시간(초) | 비고 |", "|---|---|---|---|---|"]
    for m, name, ok, sec, note in rows:
        lines.append(f"| {m} | {name} | {'통과' if ok else '실패'} | {sec:.1f} | {str(note).replace('|', '/')} |")
    passed = sum(1 for r in rows if r[2])
    lines += ["", f"통과 {passed} / {len(rows)}"]
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n보고서: {a.out}")
    sys.exit(0 if passed == len(rows) else 1)


if __name__ == "__main__":
    main()
