"""파이썬 기본 라이브러리만으로 만든 작은 웹 서버.

외부 웹 프레임워크를 쓰지 않아 설치·유지관리 부담이 없다.
"""
import json
import mimetypes
import re
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CORE_STATIC = Path(__file__).parent / "static"


class Request:
    def __init__(self, handler, params):
        self.handler = handler
        self.method = handler.command
        u = urllib.parse.urlparse(handler.path)
        self.path = u.path
        self.query = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        self.params = params
        self._body = None

    @property
    def body(self):
        if self._body is None:
            n = int(self.handler.headers.get("Content-Length") or 0)
            self._body = self.handler.rfile.read(n) if n else b""
        return self._body

    @property
    def json(self):
        if not self.body:
            return {}
        return json.loads(self.body.decode("utf-8"))


class Response:
    def __init__(self, body=b"", status=200, ctype="application/json; charset=utf-8", headers=None):
        self.body = body
        self.status = status
        self.ctype = ctype
        self.headers = headers or {}


def json_response(obj, status=200):
    return Response(json.dumps(obj, ensure_ascii=False).encode("utf-8"), status)


def file_response(data, filename, ctype=None):
    ctype = ctype or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    quoted = urllib.parse.quote(filename)
    return Response(data, 200, ctype, {"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"})


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class App:
    def __init__(self, static_dir):
        self.routes = []
        self.static_dir = Path(static_dir)

    def route(self, method, pattern):
        rx = re.compile("^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", pattern) + "$")

        def deco(fn):
            self.routes.append((method, rx, fn))
            return fn
        return deco

    def get(self, pattern):
        return self.route("GET", pattern)

    def post(self, pattern):
        return self.route("POST", pattern)

    def delete(self, pattern):
        return self.route("DELETE", pattern)

    def _static(self, path):
        if path == "/":
            path = "/index.html"
        rel = path.lstrip("/")
        for base, prefix in ((self.static_dir, ""), (CORE_STATIC, "core/")):
            if prefix and not rel.startswith(prefix):
                continue
            target = (base / rel[len(prefix):]).resolve()
            if base.resolve() in target.parents and target.is_file():
                ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype in ("application/javascript",):
                    ctype += "; charset=utf-8"
                return Response(target.read_bytes(), 200, ctype)
        return None

    @staticmethod
    def _foreign(handler):
        """다른 웹사이트가 보낸 요청을 막는다(이 앱 화면에서 보낸 요청만 받음).
        브라우저는 다른 출처로 보내는 POST에 Origin을 붙이므로, 있으면 주소창 주소(Host)와 같아야 한다.
        본문이 있으면 JSON 형식이어야 한다(사전 확인 없이 보낼 수 있는 text/plain 요청 차단)."""
        if handler.command == "GET":
            return None
        origin = handler.headers.get("Origin")
        if origin and origin != "null":
            o = urllib.parse.urlparse(origin)
            if o.netloc.lower() != (handler.headers.get("Host") or "").lower():
                return "다른 웹사이트에서 보낸 요청은 처리하지 않습니다"
        elif origin == "null":
            return "출처를 알 수 없는 요청은 처리하지 않습니다"
        if int(handler.headers.get("Content-Length") or 0) > 0:
            ctype = (handler.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return "요청 형식은 JSON이어야 합니다"
        return None

    def handle(self, handler):
        u = urllib.parse.urlparse(handler.path)
        path = urllib.parse.unquote(u.path)
        bad = self._foreign(handler)
        if bad:
            return json_response({"error": bad}, 403)
        for method, rx, fn in self.routes:
            m = rx.match(path)
            if m and method == handler.command:
                try:
                    res = fn(Request(handler, {k: urllib.parse.unquote(v) for k, v in m.groupdict().items()}))
                    if not isinstance(res, Response):
                        res = json_response(res)
                    return res
                except HttpError as e:
                    return json_response({"error": e.message}, e.status)
                except json.JSONDecodeError:
                    return json_response({"error": "요청 내용을 읽을 수 없습니다(JSON 형식 오류)"}, 400)
                except Exception:
                    # 내부 오류 내용은 실행 창(서버 기록)에만 남기고 화면에는 일반 안내만 보낸다
                    traceback.print_exc()
                    return json_response({"error": "처리 중 오류가 발생했습니다. 입력 내용을 확인하거나 실행 창의 기록을 확인하세요."}, 500)
        if handler.command == "GET":
            res = self._static(path)
            if res:
                return res
        return json_response({"error": "없는 주소입니다"}, 404)

    def run(self, host, port, open_browser=True, title=""):
        app = self

        class Handler(BaseHTTPRequestHandler):
            def _do(self):
                res = app.handle(self)
                self.send_response(res.status)
                self.send_header("Content-Type", res.ctype)
                self.send_header("Content-Length", str(len(res.body)))
                self.send_header("Cache-Control", "no-store")
                for k, v in res.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(res.body)

            do_GET = do_POST = do_DELETE = do_PUT = _do

            def log_message(self, fmt, *args):
                if args and str(args[1])[:1] in "45":
                    super().log_message(fmt, *args)

        srv = ThreadingHTTPServer((host, port), Handler)
        url = f"http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}"
        print(f"\n  {title} 실행 중: {url}\n  종료하려면 Ctrl+C 를 누르세요.\n")
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\n종료합니다.")
        finally:
            srv.server_close()
