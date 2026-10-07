"""모델 연결 계층. 특정 모델에 묶이지 않도록 공급자를 설정으로만 고른다.

- none   : 모델 없이 규칙 기반으로 동작 (각 앱이 대체 로직을 가짐)
- openai : OpenAI 호환 /chat/completions (vLLM, Ollama의 /v1, LM Studio, AI 공통기반 등)
- ollama : Ollama 기본 /api/chat

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


class LLM:
    def __init__(self, cfg):
        self.cfg = cfg  # Config.data["llm"] 를 가리키는 dict

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

    def chat(self, messages, temperature=None, max_tokens=2048, json_mode=False):
        if not self.enabled:
            raise LLMError("모델이 설정되지 않았습니다")
        base = self.cfg.get("base_url", "").rstrip("/")
        temp = self.cfg.get("temperature", 0.1) if temperature is None else temperature
        if self.provider == "ollama":
            url = base.removesuffix("/v1") + "/api/chat"
            payload = {"model": self.cfg["model"], "messages": messages, "stream": False,
                       "options": {"temperature": temp, "num_predict": max_tokens}}
            if json_mode:
                payload["format"] = "json"
            data = self._post(url, payload)
            return data.get("message", {}).get("content", "")
        url = base + "/chat/completions"
        payload = {"model": self.cfg["model"], "messages": messages,
                   "temperature": temp, "max_tokens": max_tokens}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            data = self._post(url, payload)
        except LLMError as e:
            # response_format 을 지원하지 않는 서버를 위해 한 번 더 시도
            if json_mode and ("response_format" in str(e) or " 400" in str(e)):
                payload.pop("response_format", None)
                data = self._post(url, payload)
            else:
                raise
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError):
            raise LLMError(f"예상하지 못한 응답 형식: {str(data)[:200]}")

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
            out = self.chat([{"role": "user", "content": "'확인'이라고만 답하세요."}], max_tokens=10)
            return {"ok": True, "label": self.label, "detail": out.strip()[:50],
                    "seconds": round(time.time() - t, 2)}
        except LLMError as e:
            return {"ok": False, "label": self.label, "detail": str(e)}


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
