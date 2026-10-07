"""세 앱이 공통으로 쓰는 웹 경로: 모델 설정, 상태 확인."""
import platform
import sys

from .server import HttpError
from .llm import FAMILIES, LLM, LLMError


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
        c = dict(cfg["llm"], **{k: b[k] for k in ("provider", "base_url", "api_key_env") if b.get(k)})
        if c.get("provider") == "none":
            c["provider"] = "openai"
        try:
            return {"models": LLM(c).models()}
        except LLMError as e:
            raise HttpError(502, str(e))

    @app.post("/api/llm")
    def set_llm(req):
        body = req.json
        if body.get("provider") not in ("none", "openai", "ollama"):
            raise HttpError(400, "provider 는 none, openai, ollama 중 하나여야 합니다")
        cfg.update_llm(body)
        return {"config": cfg["llm"], "label": llm.label}

    @app.post("/api/llm/test")
    def test_llm(req):
        return llm.health()
