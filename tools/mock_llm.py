"""시험용 가짜 모델 서버 (OpenAI 호환 /v1/chat/completions, Ollama /api/chat).

실제 모델이 아니다. 앱의 모델 연결 경로(요청 형식, JSON 해석, 재시도, 근거 검증)가
'모델이 바뀌어도' 동작하는지 확인하려고, 출력 습관이 다른 두 가지 응답 방식을 흉내 낸다.
  mock-a : JSON 만 깔끔하게 돌려준다.
  mock-b : ```json 코드블록과 설명 문장을 섞고, 근거 없는 항목·틀린 조문을 일부러 섞는다.
실행: python tools/mock_llm.py --port 18080
"""
import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RX_LINE = re.compile(r"^\[([^\]]+)\]\s*(.+)$", re.M)
RX_PHONE = re.compile(r"0\d{1,2}-\d{3,4}-\d{4}")
RX_NAME = re.compile(r"([가-힣]{2,4})\s*(주무관|주임|팀장|과장|대리|사원|차장|부장)")


def baton_extract(user, bad):
    out = {"duties": [], "schedule": [], "contacts": [], "issues": []}
    for bid, text in RX_LINE.findall(user):
        ph = RX_PHONE.search(text)
        nm = RX_NAME.search(text)
        if ph and nm:
            out["contacts"].append({"name": nm.group(1), "title": nm.group(2), "org": "", "phones": [ph.group()],
                                    "emails": [], "topics": [], "block_ids": [bid]})
        m = re.search(r"(\d{1,2})월\s*(\d{1,2})?일?", text)
        if m and 1 <= int(m.group(1)) <= 12 and len(out["schedule"]) < 3:
            out["schedule"].append({"months": [int(m.group(1))], "day": int(m.group(2)) if m.group(2) else None,
                                    "recurring": "", "task": text[:40], "block_ids": [bid]})
    if bad:
        out["issues"].append({"title": "근거 없는 현안(시험용)", "state": "진행 중", "next_action": "", "due": "", "block_ids": ["없는-id"]})
    return out


def seomu_pick(user):
    ids = re.findall(r"id=(\S+)", user)
    text = user.split("상황:")[-1]
    return {"procedure_id": ids[0] if ids else "", "stage": "after" if re.search(r"다녀|마쳤|끝났|했어", text) else "before",
            "date": "", "end_date": "", "days": 0, "nights": 0, "destination": "", "purpose": "", "amount": 0,
            "reply": "(모델 안내) 규정에 따른 다음 단계를 확인하세요."}


def gyujeong_ops(user, bad):
    body, intent = user.split("[개정 의도]")[0], user.split("[개정 의도]")[-1]
    arts = re.findall(r"^제(\d+(?:조의\d+)?)조?\(([^)]*)\)\s*(.*?)(?=^제\d+조|\Z)", body, re.M | re.S)
    ops = []
    for a, b in re.findall(r"(\S+?)에서\s+(\S+?)(?:으로|로)\s", intent + " "):
        topic = intent.split(a)[0]
        best = None
        for no, title, text in arts:
            if a in text:
                score = sum(1 for w in re.findall(r"[가-힣]{2,}", topic) if w in text or w in title)
                if best is None or score > best[0]:
                    best = (score, no)
        if best:
            ops.append({"type": "replace", "article": best[1].replace("조의", "의"), "find": a, "repl": b})
    m = re.search(r"제(\d+)조\s*다음에\s*(.+?)에\s*관한\s*조문", intent)
    if m:
        ops.append({"type": "insert", "after": m.group(1), "title": m.group(2).strip(),
                    "text": f"① 이사장은 다음 각 호의 어느 하나에 해당하면 {m.group(2).strip()}을 할 수 있다.\n1. 거짓이나 그 밖의 부정한 방법으로 허가를 받은 경우\n2. 허가 조건을 위반한 경우"})
    if bad:
        ops.append({"type": "replace", "article": "99", "find": "없는 글자", "repl": "x"})
    return {"operations": ops, "effective": ""}


def gyujeong_enact(user):
    arts = re.findall(r"^제\d+조\(([^)]*)\) 초안: (.+?)(?=^제\d+조\(|\Z)", user, re.M | re.S)
    return {"articles": [{"title": t, "text": x.strip()} for t, x in arts]}


def qa_answer(user):
    """후임자 질문: 질문에 나온 이름이 있는 자료 줄에서 전화번호를 찾아 근거 번호와 함께 답한다."""
    q = user.split("[질문]")[-1]
    lines = [l for l in user.split("[질문]")[0].splitlines() if re.match(r"\s*\[\d+\]", l)]
    for nm in re.findall(r"[가-힣]{2,4}", q):
        for line in lines:
            ph = re.search(r"0\d{1,2}-\d{3,4}-\d{4}", line)
            if nm in line and ph:
                n = re.match(r"\s*\[(\d+)\]", line).group(1)
                return {"answer": f"{nm} 연락처는 {ph.group(0)}입니다 [{n}].", "used": [int(n)]}
    return {"answer": "자료에서 찾지 못했습니다.", "used": []}


def answer(messages, model):
    system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
    user = messages[-1]["content"] if messages else ""
    bad = model.endswith("b")
    if "'확인'이라고만" in user:
        return "확인"
    if "인수인계서 작성을 돕는" in system:
        obj = baton_extract(user, bad)
    elif "업무 개요" in system:
        obj = {"overview": "(모델 작성) 자료에 나온 담당업무와 현안을 바탕으로 정리한 인수인계 개요입니다."}
    elif "후임자의 질문" in system:
        obj = qa_answer(user)
    elif "서무 담당자를 돕는" in system:
        obj = seomu_pick(user)
    elif "규정 개정 실무자" in system:
        obj = gyujeong_ops(user, bad)
    elif "규정 입안 실무자" in system:
        obj = gyujeong_enact(user)
    else:
        obj = {"answer": "알 수 없는 요청"}
    s = json.dumps(obj, ensure_ascii=False)
    return f"요청하신 결과입니다.\n```json\n{s}\n```" if bad else s


class H(BaseHTTPRequestHandler):
    """모델 계열별 응답 차이를 흉내 낸다(실제 모델 아님).
    mock-gemma: system 역할 거부(400) / mock-exaone: 생각 과정 태그를 붙임 /
    mock-gpt-oss: 토큰 한도가 작으면 생각만 하다 빈 답(finish_reason=length)."""
    MODELS = ["mock-a", "mock-b", "mock-gemma", "mock-exaone", "mock-gpt-oss"]

    def _send(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.endswith("/models"):
            return self._send(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in self.MODELS]})
        if self.path.endswith("/api/tags"):
            return self._send(200, {"models": [{"name": m} for m in self.MODELS]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        model = body.get("model", "mock-a")
        msgs = body.get("messages", [])
        if model == "mock-gemma" and any(m.get("role") == "system" for m in msgs):
            return self._send(400, {"error": {"message": "System role not supported"}})
        if model == "mock-gemma":  # system 이 사용자 메시지에 합쳐져 온 경우
            u = msgs[0]["content"]
            msgs = [{"role": "system", "content": u}, *msgs[1:-1], {"role": "user", "content": msgs[-1]["content"] if len(msgs) > 1 else u}]
        content = answer(msgs, model)
        finish, reasoning = "stop", None
        if model == "mock-exaone":
            content = "<thought>\n요청을 검토합니다.\n</thought>\n" + content
        if model == "mock-gpt-oss":
            reasoning = "요청을 단계별로 검토합니다."
            if int(body.get("max_tokens") or body.get("options", {}).get("num_predict") or 0) < 2000:
                content, finish = "", "length"
        if self.path.endswith("/api/chat"):
            res = {"model": model, "message": {"role": "assistant", "content": content}, "done": True, "done_reason": finish}
        else:
            msg = {"role": "assistant", "content": content}
            if reasoning:
                msg["reasoning_content"] = reasoning
            res = {"id": "mock", "object": "chat.completion", "model": model,
                   "choices": [{"index": 0, "message": msg, "finish_reason": finish}]}
        self._send(200, res)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18080)
    a = ap.parse_args()
    print(f"가짜 모델 서버: http://127.0.0.1:{a.port}/v1 (모델 이름: mock-a, mock-b, mock-gemma, mock-exaone, mock-gpt-oss)")
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
