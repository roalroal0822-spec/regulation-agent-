"""세 앱이 공통으로 쓰는 웹 경로: 모델 설정, 상태 확인."""
import platform
import sys

from .server import HttpError


def register(app, cfg, llm, app_name, version):
    @app.get("/api/info")
    def info(req):
        return {"app": app_name, "version": version, "llm": llm.label,
                "llm_enabled": llm.enabled, "python": sys.version.split()[0],
                "os": platform.system()}

    @app.get("/api/llm")
    def get_llm(req):
        c = dict(cfg["llm"])
        return {"config": c, "label": llm.label}

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
