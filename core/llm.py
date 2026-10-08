"""모델 연결 계층. 특정 모델에 묶이지 않도록 공급자를 설정으로만 고른다.

- none   : 모델 없이 규칙 기반으로 동작 (각 앱이 대체 로직을 가짐)
- openai : OpenAI 호환 /chat/completions (vLLM, Ollama의 /v1, LM Studio, AI 공통기반 등)
- ollama : Ollama 기본 /api/chat

젬마·라마·GPT-OSS·엑사원·하이퍼클로바X·솔라 등 계열별 응답 차이(생각 과정 태그, system 역할 미지원,
추론형 모델의 토큰 소모)는 chat() 안에서 흡수한다.

모든 앱 기능은 chat()/json() 두 함수만 사용하므로 공급자를 추가해도 앱 코드는 바뀌지 않는다.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


# 행정안전부 AI 공통기반에서 지원(또는 지원 예정)하는 모델 계열.
# 앱 기능은 계열과 무관하게 같고, 아래는 계열별 응답 차이를 흡수하기 위한 정보다.
# ollama 이름은 예시이며, 실제 이름은 화면의 '모델 목록 불러오기'로 서버에서 받아 쓴다.
FAMILIES = [
    {"key": "gemma", "name": "젬마(Gemma)", "maker": "Google", "match": r"gemma", "ollama": ["gemma3:4b", "gemma3:12b"]},
    {"key": "llama", "name": "라마(Llama)", "maker": "Meta", "match": r"llama", "ollama": ["llama3.1:8b", "llama3.2:3b"]},
    {"key": "gpt-oss", "name": "GPT-OSS", "maker": "OpenAI", "match": r"gpt-oss|gpt_oss", "ollama": ["gpt-oss:20b"], "reasoning": True},
    {"key": "exaone", "name": "엑사원(EXAONE)", "maker": "LG AI연구원", "match": r"exaone", "ollama": ["exaone3.5:2.4b", "exaone3.5:7.8b"]},
    {"key": "hyperclovax", "name": "하이퍼클로바X(HyperCLOVA X)", "maker": "네이버", "match": r"hyperclova|hcx", "ollama": []},
    {"key": "solar", "name": "솔라(Solar)", "maker": "업스테이지", "match": r"solar", "ollama": ["solar-pro"]},
]

# 추론형 모델이 답 앞에 붙이는 생각 과정(<think>…</think>, <thought>…</thought>)
RX_THINK = re.compile(r"<(think|thought|reasoning)>.*?</\1>\s*", re.S | re.I)
# 길이 제한으로 닫는 태그 없이 잘린 생각 과정(여는 태그부터 끝까지)
RX_THINK_OPEN = re.compile(r"<(think|thought|reasoning)>.*\Z", re.S | re.I)


def strip_think(text):
    """생각 과정을 지운다. 닫히지 않은 생각 과정은 답이 아니므로 통째로 버린다."""
    return RX_THINK_OPEN.sub("", RX_THINK.sub("", text or "")).strip()


def family_of(model):
    m = (model or "").lower()
    for f in FAMILIES:
        if re.search(f["match"], m):
            return f
    return None


class LLM:
    def __init__(self, cfg, saved_base=None):
        self.cfg = cfg  # Config.data["llm"] 를 가리키는 dict
        self.saved_base = saved_base  # 목록 조회용 임시 객체일 때 저장된 주소(키 전송 허용 범위)

    @property
    def provider(self):
        return (self.cfg.get("provider") or "none").lower()

    @property
    def enabled(self):
        return self.provider != "none"

    @property
    def label(self):
        if not self.enabled:
            return "규칙 기반(모델 미사용)"
        return f"{self.provider}:{self.cfg.get('model')}"

    def _post(self, url, payload):
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(self.cfg.get("api_key_env") or "LLM_API_KEY", "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=float(self.cfg.get("timeout", 120))) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            raise LLMError(f"모델 서버 오류 {e.code}: {body}")
        except urllib.error.URLError as e:
            raise LLMError(f"모델 서버에 연결할 수 없습니다 ({self.cfg.get('base_url')}): {e.reason}")
        except TimeoutError:
            raise LLMError("모델 응답 시간이 초과되었습니다")

    @property
    def family(self):
        return family_of(self.cfg.get("model"))

    def chat(self, messages, temperature=None, max_tokens=2048, json_mode=False):
        if not self.enabled:
            raise LLMError("모델이 설정되지 않았습니다")
        fam = self.family or {}
        if fam.get("reasoning"):
            # 추론형 모델은 생각 과정에도 토큰을 쓰므로 여유를 둔다
            max_tokens = max(max_tokens, 1024)
        try:
            out = self._chat(messages, temperature, max_tokens, json_mode)
        except LLMError as e:
            # 일부 서버·모델(젬마 등)은 system 역할을 받지 않는다: 첫 사용자 메시지에 합쳐 다시 시도
            if _has_system(messages) and _system_rejected(str(e)):
                out = self._chat(_merge_system(messages), temperature, max_tokens, json_mode)
            else:
                raise
        text, finish = out
        if not strip_think(text) and (finish == "length" or text.strip()):
            # 생각 과정만 쓰다 길이 제한에 걸린 경우(빈 답·닫히지 않은 생각 과정) 한도를 늘려 한 번 더
            text, finish = self._chat(messages, temperature, min(max_tokens * 4, 8192), json_mode)
        return strip_think(text)

    def _chat(self, messages, temperature, max_tokens, json_mode):
        base = self.cfg.get("base_url", "").rstrip("/")
        temp = self.cfg.get("temperature", 0.1) if temperature is None else temperature
        if self.provider == "ollama":
            url = base.removesuffix("/v1") + "/api/chat"
            payload = {"model": self.cfg["model"], "messages": messages, "stream": False,
                       "options": {"temperature": temp, "num_predict": max_tokens}}
            if json_mode:
                payload["format"] = "json"
            data = self._post(url, payload)
            return data.get("message", {}).get("content", "") or "", data.get("done_reason", "")
        url = base + "/chat/completions"
        payload = {"model": self.cfg["model"], "messages": messages,
                   "temperature": temp, "max_tokens": max_tokens}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            data = self._post(url, payload)
        except LLMError as e:
            # response_format 을 지원하지 않는 서버를 위해 한 번 더 시도
            if json_mode and ("response_format" in str(e) or " 400" in str(e) or " 422" in str(e)):
                payload.pop("response_format", None)
                data = self._post(url, payload)
            else:
                raise
        try:
            ch = data["choices"][0]
            return ch["message"].get("content") or "", ch.get("finish_reason") or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"예상하지 못한 응답 형식: {str(data)[:200]}")

    def models(self):
        """연결한 서버가 제공하는 모델 이름 목록 (OpenAI 호환 /models, Ollama /api/tags)."""
        base = self.cfg.get("base_url", "").rstrip("/")
        headers = {}
        key = os.environ.get(self.cfg.get("api_key_env") or "LLM_API_KEY", "")
        # 키는 저장된(사용자가 정한) 주소로만 보낸다. 저장 전 새 주소로 목록을 볼 때는 키 없이 요청
        if key and base == (self.saved_base or base):
            headers["Authorization"] = f"Bearer {key}"
        urls = [base.removesuffix("/v1") + "/api/tags"] if self.provider == "ollama" else [base + "/models", base.removesuffix("/v1") + "/api/tags"]
        last = ""
        for url in urls:
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
                    data = json.loads(r.read().decode("utf-8"))
                names = [m.get("id") for m in data.get("data", [])] or [m.get("name") for m in data.get("models", [])]
                return [n for n in names if n]
            except Exception as e:  # 다음 경로 시도
                last = str(e)
        raise LLMError(f"모델 목록을 받지 못했습니다({base}): {last}")

    def json(self, system, user, retries=1, max_tokens=2048):
        """JSON 객체를 돌려받는다. 형식이 깨지면 한 번 다시 요청한다."""
        messages = [{"role": "system", "content": system + "\n반드시 JSON 객체 하나만 출력하세요. 설명 문장은 쓰지 마세요."},
                    {"role": "user", "content": user}]
        last = ""
        for _ in range(retries + 1):
            last = self.chat(messages, json_mode=True, max_tokens=max_tokens)
            obj = parse_json(last)
            if obj is not None:
                return obj
            messages = messages + [{"role": "assistant", "content": last},
                                   {"role": "user", "content": "JSON 형식이 올바르지 않습니다. JSON 객체만 다시 출력하세요."}]
        raise LLMError(f"모델이 올바른 JSON을 돌려주지 않았습니다: {last[:200]}")

    def health(self):
        """연결 확인: 짧은 요청을 보내 응답 시간과 결과를 돌려준다."""
        if not self.enabled:
            return {"ok": True, "label": self.label, "detail": "모델 없이 규칙 기반으로 동작합니다"}
        t = time.time()
        try:
            out = self.chat([{"role": "user", "content": "'확인'이라고만 답하세요."}], max_tokens=32)
            fam = self.family
            return {"ok": True, "label": self.label, "detail": out.strip()[:50],
                    "family": fam["name"] if fam else "",
                    "seconds": round(time.time() - t, 2)}
        except LLMError as e:
            return {"ok": False, "label": self.label, "detail": str(e)}


def _has_system(messages):
    return any(m.get("role") == "system" for m in messages)


def _system_rejected(err):
    e = err.lower()
    return ("system" in e and any(w in e for w in ("role", "not supported", "support", "alternate"))) or " 400" in e or " 422" in e


def _merge_system(messages):
    sys_text = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    rest = [dict(m) for m in messages if m.get("role") != "system"]
    for m in rest:
        if m.get("role") == "user":
            m["content"] = sys_text + "\n\n" + m["content"]
            break
    else:
        rest.insert(0, {"role": "user", "content": sys_text})
    return rest


def parse_json(text):
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        v = json.loads(text)
        return v if isinstance(v, (dict, list)) else None
    except json.JSONDecodeError:
        pass
    # 앞뒤 잡음이 섞인 경우 가장 바깥 {} 를 찾는다
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None
