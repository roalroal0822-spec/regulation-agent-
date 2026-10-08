"""세 앱이 공통으로 쓰는 웹 경로: 모델 설정, 상태 확인."""
import platform
import re
import sys

from .server import HttpError
from .llm import FAMILIES, LLM, LLMError


def _check_llm_values(b):
    """모델 주소는 http(s)만, API 키 환경변수 이름은 키·토큰용 이름만 허용한다
    (다른 환경변수 값이 모델 서버로 전송되는 것을 막음)."""
    url = (b.get("base_url") or "").strip()
    if url and not re.match(r"^https?://[^\s/]+", url):
        raise HttpError(400, "주소는 http:// 또는 https:// 로 시작해야 합니다")
    env = (b.get("api_key_env") or "").strip()
    if env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(KEY|TOKEN)[A-Za-z0-9_]*", env, re.I):
        raise HttpError(400, "API 키 환경변수 이름은 KEY 또는 TOKEN이 들어간 이름이어야 합니다(예: LLM_API_KEY)")


def register(app, cfg, llm, app_name, version):
    @app.get("/api/info")
    def info(req):
        return {"app": app_name, "version": version, "llm": llm.label,
                "llm_enabled": llm.enabled, "python": sys.version.split()[0],
                "os": platform.system()}

    @app.get("/api/llm")
    def get_llm(req):
        c = dict(cfg["llm"])
        fam = llm.family
        return {"config": c, "label": llm.label, "family": fam["key"] if fam else "",
                "families": [{k: f[k] for k in ("key", "name", "maker", "ollama")} for f in FAMILIES]}

    @app.post("/api/llm/models")
    def list_models(req):
        """저장 전 입력값(연결 방식·주소)으로 서버의 모델 목록을 받아 온다."""
        b = req.json or {}
        _check_llm_values(b)
        c = dict(cfg["llm"], **{k: b[k] for k in ("provider", "base_url", "api_key_env") if b.get(k)})
        if c.get("provider") == "none":
            c["provider"] = "openai"
        try:
            return {"models": LLM(c, saved_base=(cfg["llm"].get("base_url") or "").rstrip("/")).models()}
        except LLMError as e:
            raise HttpError(502, str(e))

    @app.post("/api/llm")
    def set_llm(req):
        body = req.json
        if body.get("provider") not in ("none", "openai", "ollama"):
            raise HttpError(400, "provider 는 none, openai, ollama 중 하나여야 합니다")
        _check_llm_values(body)
        cfg.update_llm(body)
        return {"config": cfg["llm"], "label": llm.label}

    @app.post("/api/llm/test")
    def test_llm(req):
        return llm.health()
