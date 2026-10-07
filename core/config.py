"""설정 파일(config.json) 읽기·저장.

모델 연결 정보는 설정 파일 한 곳에서만 바꾸면 되도록 한다.
API 키는 파일에 쓰지 않고 환경변수 이름만 적는다.
"""
import json
import os
from pathlib import Path

DEFAULT = {
    "app_title": "",
    "host": "127.0.0.1",
    "port": 8000,
    # 산출물·작업 데이터 위치(원본 자료와 분리)
    "data_dir": "data",
    "llm": {
        # none: 모델 없이 규칙 기반으로 동작
        # openai: OpenAI 호환 API (vLLM, Ollama /v1, LM Studio, 행정안전부 AI 공통기반 등)
        # ollama: Ollama 기본 API
        "provider": "none",
        "base_url": "http://localhost:11434/v1",
        "model": "gemma3:4b",
        "api_key_env": "LLM_API_KEY",
        "temperature": 0.1,
        "timeout": 120,
    },
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, app_dir, defaults=None):
        self.app_dir = Path(app_dir)
        self.path = self.app_dir / "config.json"
        base = _merge(DEFAULT, defaults or {})
        data = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
        self.data = _merge(base, data)
        # 환경변수로 덮어쓰기 (배포 환경에서 파일 수정 없이 모델 교체)
        env_map = {
            "LLM_PROVIDER": "provider",
            "LLM_BASE_URL": "base_url",
            "LLM_MODEL": "model",
        }
        for env, key in env_map.items():
            if os.environ.get(env):
                self.data["llm"][key] = os.environ[env]
        if os.environ.get("APP_PORT"):
            self.data["port"] = int(os.environ["APP_PORT"])
        if os.environ.get("APP_DATA_DIR"):
            self.data["data_dir"] = os.environ["APP_DATA_DIR"]
        if os.environ.get("APP_HOST"):
            self.data["host"] = os.environ["APP_HOST"]

    def __getitem__(self, key):
        return self.data[key]

    def get(self, key, default=None):
        return self.data.get(key, default)

    @property
    def data_dir(self):
        p = Path(self.data["data_dir"])
        if not p.is_absolute():
            p = self.app_dir / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    def update_llm(self, values):
        allowed = {"provider", "base_url", "model", "api_key_env", "temperature", "timeout"}
        for k, v in values.items():
            if k in allowed:
                self.data["llm"][k] = v
        saved = {}
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        saved["llm"] = self.data["llm"]
        self.path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
