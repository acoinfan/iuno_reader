"""网页前端：python -m iuno_reader serve [--port 8780]，浏览器打开 http://localhost:8780

只用标准库。只监听本机（127.0.0.1），密钥不经过网页。
任务队列存在 out/jobs.json，日志在 out/jobs/<编号>.log。每篇论文按方案依次跑：
  1. run：下载、拆分、翻译 + 审校一（各自的并发数）
  2. review --name <审校二>：换一家模型再审一遍（方案里有审校二时）
  3. run：不调用接口，只重新核对和渲染（让审校二的意见显示出来）
  4. 设置了「完成后复制到」时，把双语网页和 Markdown 复制过去，命名为 <编号>-zh.html / .md
每一步都是子进程，「暂停」就是结束子进程；流水线可以断点续跑，「继续」时已完成的段落不会重复花钱。
进度不靠子进程汇报，而是直接数数据文件里翻好、审好的段数，所以页面关掉、服务重启都不影响。
"""
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import providers as pv
from .roles import ROOT

OUT = os.path.join(ROOT, "out")
JOBS = os.path.join(OUT, "jobs.json")
LOGS = os.path.join(OUT, "jobs")
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
PID = r"([A-Za-z0-9][A-Za-z0-9._-]{1,99})"  # arXiv 编号，或者来自 PDF 的论文编号（如 Li2024-StateAbstractionProbe）


def _to_arxiv(texts):
    """输入框或拖进来的文字 → 流水线编号（见 pipeline.to_pids）：DOI、论文页面链接、标题先找 arXiv 版本，
    找不到但有开放获取 PDF 的改用 PDF；都不行的连同原因返回。"""
    from .pipeline import to_pids
    from .resolve import find_inputs
    return to_pids(ROOT, [x for t in texts for x in find_inputs(t)], log=lambda s: None)


def _why(r):
    name = f"《{r['title'][:50]}》" if r.get("title") else r["input"][:60]
    return f"{name}没有 arXiv 版本，也没有能下载的 PDF，翻不了（{r.get('error', '')}）"


def _json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


r2_name = pv.r2_name


# ---------- 进度与花费：直接数数据文件 ----------

_sel_cache = {}


def _selection(pid, scope):
    """这篇论文这次要处理的段落 id（正文或全文）。doc.json 还没生成时返回 None。"""
    path = os.path.join(ROOT, "data", pid, "doc.json")
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None, ""
    key = (pid, scope, mt)
    if key not in _sel_cache:
        from .translate import select_segments
        doc = _json(path, {"segments": []})
        segs = select_segments(doc, [scope])
        _sel_cache[key] = ({s["id"] for s in segs}, doc.get("title", ""))
    return _sel_cache[key]


def paper_cost(pid):
    """这篇论文到目前为止的花费：翻译 + 每份审校。返回 (合计元, 明细, 是否为上限)。"""
    d = os.path.join(ROOT, "data", pid)
    tr = (_json(os.path.join(d, "translation.json"), {}) or {}).get("__meta__") or {}
    c, upper = pv.cost(tr.get("usage"), pv.model_of(tr.get("translator", "")))
    parts = {"翻译": round(c, 2)}
    for f in sorted(glob.glob(os.path.join(d, "review.*.json"))):
        meta = (_json(f, {}) or {}).get("__meta__") or {}
        models = list((meta.get("models") or {}).values())
        rc, up = pv.cost(meta.get("usage"), models[0] if models else "")
        if meta.get("usage"):
            parts["审校 " + (models[0] if models else os.path.basename(f))] = round(rc, 2)
        c, upper = c + rc, upper or up
    return round(c, 2), parts, upper


def progress(job):
    pid, p = job["pid"], job["preset"]
    sel, title = _selection(pid, p.get("scope", "body"))
    d = os.path.join(ROOT, "data", pid)
    out = {"title": title, "total": len(sel) if sel is not None else None, "bars": []}
    if sel is None:
        return out

    def count(name):
        return len(sel & set(_json(os.path.join(d, name), {}) or {}))

    out["bars"].append({"name": "翻译", "done": count("translation.json")})
    if p.get("reviewer"):
        out["bars"].append({"name": "审校一", "done": count("review.blind.json")})
    if p.get("reviewer2"):
        out["bars"].append({"name": "审校二", "done": count(f"review.{r2_name(p['reviewer2'])}.json")})
    out["cost"], out["cost_parts"], out["cost_upper"] = paper_cost(pid)
    m = _json(os.path.join(OUT, pid, "manifest.json"), {}) or {}
    flags = ((m.get("stages") or {}).get("render") or {}).get("flags")
    if flags:
        out["flags"] = {k: flags.get(k, 0) for k in ("red", "orange", "yellow")}
    out["html"] = os.path.exists(os.path.join(OUT, pid, "paper.zh.html"))
    return out


# ---------- 任务队列 ----------

class Queue:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = _json(JOBS, []) or []
        self.procs = {}
        for j in self.jobs:  # 服务重启时，原来在跑的接着排队
            if j["status"] in ("running", "waiting"):
                j["status"] = "queued"
        self._save()
        threading.Thread(target=self._loop, daemon=True).start()

    def _save(self):
        os.makedirs(OUT, exist_ok=True)
        with open(JOBS + ".tmp", "w", encoding="utf-8") as f:
            json.dump(self.jobs, f, ensure_ascii=False, indent=1)
        os.replace(JOBS + ".tmp", JOBS)

    def find(self, n):
        return next((j for j in self.jobs if j["n"] == n), None)

    def add(self, pids, preset_name):
        p = pv.preset(preset_name)
        if not p:
            raise ValueError(f"没有叫「{preset_name}」的方案")
        with self.lock:
            n0 = max([j["n"] for j in self.jobs] + [0])
            for i, pid in enumerate(pids, 1):
                self.jobs.append({"n": n0 + i, "pid": pid, "preset": p, "status": "queued", "step": "排队中",
                                  "error": "", "created": time.time()})
            self._save()

    def action(self, n, act):
        with self.lock:
            j = self.find(n)
            if not j:
                raise ValueError("没有这个任务")
            if act in ("pause", "delete"):
                proc = self.procs.get(n)
                if proc and proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                j["status"], j["step"] = "paused", "已暂停"
                if act == "delete":
                    self.jobs.remove(j)
            elif act in ("resume", "retry"):
                j["status"], j["step"], j["error"] = "queued", "排队中", ""
            self._save()

    def _loop(self):
        while True:
            time.sleep(2)
            with self.lock:
                limit = int(settings().get("max_jobs", 2))
                running = sum(j["status"] == "running" for j in self.jobs)
                for j in self.jobs:
                    if running >= limit:
                        break
                    if j["status"] not in ("queued", "waiting"):
                        continue
                    if j["preset"].get("offpeak_only") and pv.is_peak():
                        j["status"], j["step"] = "waiting", "等空闲时段（DeepSeek 高峰价翻倍）"
                        continue
                    j["status"], j["step"], j["started"] = "running", "开始", time.time()
                    running += 1
                    threading.Thread(target=self._work, args=(j,), daemon=True).start()
                self._save()

    def _step(self, j, label, args, env_extra):
        """跑一步子进程；返回 True 表示成功。被暂停或删除时返回 False。"""
        with self.lock:
            if j["status"] != "running":
                return False
            j["step"] = label
            self._save()
        env = {k: v for k, v in os.environ.items() if not k.startswith("IUNO_")} | env_extra
        env["PYTHONUNBUFFERED"] = "1"
        os.makedirs(LOGS, exist_ok=True)
        log = os.path.join(LOGS, f"{j['n']}.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n== {time.strftime('%m-%d %H:%M:%S')} {label}\n")
            f.flush()
            proc = subprocess.Popen([sys.executable, "-m", "iuno_reader", *args], cwd=ROOT, env=env,
                                    stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
            self.procs[j["n"]] = proc
            rc = proc.wait()
        with self.lock:
            if j["status"] != "running":
                return False
            if rc != 0:
                tail = open(log, encoding="utf-8").read().strip().splitlines()[-3:]
                j["status"], j["step"], j["error"] = "error", f"{label}失败", " / ".join(tail)[-400:]
                self._save()
                return False
        return True

    def _work(self, j):
        p, pid = j["preset"], j["pid"]
        try:
            steps = pv.preset_steps(pid, p)
        except ValueError as e:
            with self.lock:
                j["status"], j["step"], j["error"] = "error", "接口配置有问题", str(e)
                self._save()
            return
        for label, args, env in steps:
            if not self._step(j, label, args, env):
                return
        dest = settings().get("export_dir", "").strip()
        if dest:
            try:
                os.makedirs(dest, exist_ok=True)
                for ext in ("html", "md"):
                    shutil.copyfile(os.path.join(OUT, pid, f"paper.zh.{ext}"), os.path.join(dest, f"{pid}-zh.{ext}"))
            except OSError as e:
                with self.lock:
                    j["status"], j["step"], j["error"] = "error", "复制到导出目录失败", str(e)
                    self._save()
                return
        with self.lock:
            j["status"], j["step"], j["finished"] = "done", "完成", time.time()
            self._save()


def vocab_md(vocab):
    """生词本 → Markdown（和网页里「导出 Markdown」同样的格式），按收录时间排序。"""
    vs = sorted((v for v in vocab.values() if not v.get("deleted")), key=lambda v: v.get("time", ""))
    out = ["# 生词本", "", f"> iuno_reader 双语网页里收的生词，共 {len(vs)} 个；每个词附上它出现的句子和出处。"
           "这是生成文件，在网页里增删生词会整个重写。", ""]
    for v in vs:
        out.append(f"## {v.get('w', '')}" + (f"  /{v['p']}/" if v.get("p") else ""))
        out.append("")
        out += [f"- {x}" for x in (v.get("t") or "").split("\n") if x]
        if v.get("d"):
            out.append(f"- *{v['d']}*")
        for c in v.get("ctx") or []:
            out += ["", f"> {c.get('en', '')}", f"> {c.get('zh', '')}",
                    f"> —— {c.get('title', '')}（arXiv {c.get('pid', '')}）· {c.get('time', '')}"]
        out.append("")
    return "\n".join(out) + "\n"


def settings():
    return {"max_jobs": 2, "export_dir": "", "library_dir": ""} | (pv._load("settings.json", {}) or {})


# ---------- 测试接口 ----------

def list_models(prov):
    req = urllib.request.Request(prov["base"] + "/models", headers={"Authorization": "Bearer " + prov["key"]})
    with urllib.request.urlopen(req, timeout=20) as r:
        return sorted(m["id"] for m in json.load(r).get("data", []))


def test_provider(pid, model, thinking, role):
    from .probe import probe_reviewer, probe_translator
    from .translate import OpenAICompatible
    p = pv.provider(pid)
    if not p:
        raise ValueError("没有这个接口")
    eng = OpenAICompatible(role="translator" if role == "translator" else "reviewer",
                           base_url=p["base"], model=model, api_key=p["key"])
    eng.extra = pv.THINKING.get(thinking or "default", {})
    res = probe_translator(eng) if role == "translator" else probe_reviewer(eng)
    res["cost"] = round(pv.cost(res.get("usage"), model)[0], 4)
    return res


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _route(self, method):
        url = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(url.path)
        q = urllib.parse.parse_qs(url.query)
        try:
            if method == "GET" and path in ("/", "/index.html"):
                return self._send(200, open(os.path.join(WEB, "index.html"), "rb").read(), "text/html; charset=utf-8")
            if method == "GET" and path.startswith("/out/"):
                m = re.fullmatch(rf"/out/{PID}/(paper\.zh\.(?:html|md)|flags\.md)", path)
                if not m:
                    return self._send(404, {"error": "没有这个文件"})
                f = os.path.join(OUT, m.group(1), m.group(2))
                ctype = "text/html; charset=utf-8" if f.endswith(".html") else "text/plain; charset=utf-8"
                return self._send(200, open(f, "rb").read(), ctype)
            if method == "GET" and path == "/api/state":
                with Q.lock:
                    jobs = [dict(j) for j in Q.jobs]
                for j in jobs:
                    j["progress"] = progress(j)
                return self._send(200, {"peak": pv.is_peak(), "settings": settings(), "presets": pv.presets(),
                                        "providers": pv.providers(), "vendors": pv.VENDORS,
                                        "thinking": list(pv.THINKING), "jobs": jobs})
            if method == "GET" and path == "/api/cost":
                rows = []
                for d in sorted(glob.glob(os.path.join(ROOT, "data", "*", "doc.json"))):
                    pid = os.path.basename(os.path.dirname(d))
                    sel, title = _selection(pid, "body")
                    doc = _json(d, {"segments": []})
                    n = sum(len(s["sentences"]) for s in doc["segments"] if s["id"] in (sel or set()))
                    c, parts, upper = paper_cost(pid)
                    if c:
                        rows.append({"pid": pid, "title": title, "sentences": n, "total": c, "parts": parts,
                                     "upper": upper})
                return self._send(200, {"rows": rows, "prices": pv.prices()})
            m = re.fullmatch(rf"/api/notes/{PID}", path)
            if m:  # 双语网页里的标签批注
                pid = m.group(1)
                f = os.path.join(ROOT, "data", pid, "notes.json")
                if method == "GET":
                    return self._send(200, _json(f, {"notes": []}) or {"notes": []})
                if method == "POST":
                    b = self._body()
                    os.makedirs(os.path.dirname(f), exist_ok=True)
                    with open(f, "w", encoding="utf-8") as fh:
                        json.dump({"notes": b.get("notes", [])}, fh, ensure_ascii=False, indent=1)
                    md = str(b.get("markdown", ""))
                    targets = [os.path.join(OUT, pid, "notes.md")]
                    if settings().get("export_dir", "").strip():
                        targets.append(os.path.join(settings()["export_dir"].strip(), f"{pid}-批注.md"))
                    for t in targets:
                        os.makedirs(os.path.dirname(t), exist_ok=True)
                        with open(t, "w", encoding="utf-8") as fh:
                            fh.write(md)
                    return self._send(200, {"ok": True})
            if path == "/api/vocab":  # 双语网页里的生词本（不分论文）
                f = os.path.join(ROOT, "data", "vocab.json")
                if method == "GET":
                    return self._send(200, _json(f, {"vocab": {}}) or {"vocab": {}})
                if method == "POST":
                    b = self._body()
                    old = (_json(f, {"vocab": {}}) or {}).get("vocab", {})
                    new = b.get("vocab", {}) or {}
                    for k, v in new.items():  # 以较新的为准，例句合并去重
                        o = old.get(k)
                        if not o or str(v.get("time", "")) >= str(o.get("time", "")):
                            ctx = (o or {}).get("ctx", []) + v.get("ctx", [])
                            v["ctx"] = [c for i, c in enumerate(ctx) if c.get("en") not in [x.get("en") for x in ctx[:i]]]
                            old[k] = v
                    os.makedirs(os.path.dirname(f), exist_ok=True)
                    with open(f, "w", encoding="utf-8") as fh:
                        json.dump({"vocab": old}, fh, ensure_ascii=False, indent=1)
                    lib = settings().get("library_dir", "").strip()
                    if lib and os.path.isdir(lib):
                        os.makedirs(os.path.join(lib, "vocab"), exist_ok=True)
                        with open(os.path.join(lib, "vocab", "生词本.md"), "w", encoding="utf-8") as fh:
                            fh.write(vocab_md(old))
                    return self._send(200, {"ok": True})
            if method == "GET" and path == "/api/models":
                p = pv.provider(q.get("provider", [""])[0])
                return self._send(200, {"models": list_models(p) if p else []})
            if method == "POST" and path == "/api/jobs":
                b = self._body()
                ids, bad = _to_arxiv(b.get("ids", []))
                if not ids:
                    msg = "；".join(_why(r) for r in bad) or "没认出论文（可以填 arXiv 编号、DOI、论文页面链接或标题）"
                    return self._send(400, {"error": msg, "unresolved": bad})
                Q.add(ids, b.get("preset", ""))
                return self._send(200, {"added": ids, "unresolved": [_why(r) for r in bad]})
            m = re.fullmatch(r"/api/jobs/(\d+)/(pause|resume|retry|delete)", path)
            if method == "POST" and m:
                Q.action(int(m.group(1)), m.group(2))
                return self._send(200, {"ok": True})
            if method == "POST" and path == "/api/providers":
                b = self._body()
                if not (b.get("name") and b.get("base") and b.get("key")):
                    return self._send(400, {"error": "名称、地址、密钥都要填"})
                return self._send(200, {"id": pv.add_provider(b["name"], b["base"], b["key"], b.get("model", ""))})
            m = re.fullmatch(r"/api/providers/(.+)", path)
            if method == "DELETE" and m:
                pv.delete_provider(m.group(1))
                return self._send(200, {"ok": True})
            if method == "POST" and path == "/api/test":
                b = self._body()
                return self._send(200, test_provider(b["provider"], b["model"], b.get("thinking"), b.get("role")))
            if method == "POST" and path == "/api/presets":
                pv.save_preset(self._body())
                return self._send(200, {"ok": True})
            m = re.fullmatch(r"/api/presets/(.+)", path)
            if method == "DELETE" and m:
                pv.delete_preset(m.group(1))
                return self._send(200, {"ok": True})
            if method == "POST" and path == "/api/settings":
                b = self._body()
                pv._save("settings.json", {"max_jobs": max(1, int(b.get("max_jobs", 2))),
                                           "export_dir": str(b.get("export_dir", "")),
                                           "library_dir": str(b.get("library_dir", ""))})
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "没有这个地址"})
        except (ValueError, KeyError, OSError) as e:
            return self._send(400, {"error": f"{type(e).__name__}: {e}"})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")


def handle(method, path, body=None):
    """不经过网络，直接在进程内调用一次接口：返回 (状态码, Content-Type, 内容)。测试和截图用。"""
    import io

    class _Fake(Handler):
        def __init__(self):  # 不走 BaseHTTPRequestHandler 的套接字初始化
            data = json.dumps(body).encode() if body is not None else b""
            self.path, self.rfile, self.wfile = path, io.BytesIO(data), io.BytesIO()
            self.headers = {"Content-Length": str(len(data))}
            self.out = {}

        def send_response(self, code, message=None):
            self.out["code"] = code

        def send_header(self, k, v):
            self.out[k] = v

        def end_headers(self):
            pass

    h = _Fake()
    h._route(method)
    return h.out.get("code"), h.out.get("Content-Type", ""), h.wfile.getvalue()


Q = None


def start_queue(run_loop=True):
    """建任务队列（serve 和测试用）。run_loop=False 时不自动开跑任务。"""
    global Q
    if not run_loop:
        Queue._loop = lambda self: None
    Q = Queue()
    return Q


def serve(port=8780, host="127.0.0.1"):
    start_queue()
    print(f"iuno_reader 前端：http://localhost:{port}（只监听 {host}；Ctrl+C 结束，任务会在下次启动时接着排队）")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
