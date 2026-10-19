"""翻译：两种方式产出同一种格式的 translation.json。

translation.json：
{
  "__meta__": {"translator": "谁翻的（模型名或人）"},
  "__sections__": {"英文节标题": "中文节标题"},
  "<片段 id>": {"sentences": [{"zh": "译文", "conf": "high|mid|low", "note": "为什么没把握"}, ...]}
}

1. 人工 / Agent 翻译：用 `tasks` 命令导出待译清单（含上下文和相关术语），译好后写成上面的格式。
2. 自动翻译：OpenAI 兼容接口，角色 translator，提示词见 prompts/translator.md，模型配置见 roles.py。
"""
import json
import re
import time
import urllib.error
import urllib.request

from .glossary import terms_in
from .roles import extra_body, load_prompt, model_config, payload_translator


def build_task(seg, prev_en, doc_title, terms, hedges):
    found = terms_in(seg["en"], terms)
    return {
        "id": seg["id"],
        "kind": seg["kind"],
        "paper": doc_title,
        "section": seg.get("section", ""),
        "subsection": seg.get("subsection", ""),
        "previous_paragraph": prev_en,
        "terms": {t: "/".join(terms[t]["zh"]) for t in found},
        "sentences": seg["sentences"],
    }


def appendix_sections(doc):
    """附录的节名集合：以 Appendix 开头的节；或者节编号和前面重复的节及其之后的所有节
    （罗马数字编号的论文，附录会从 I 重新编号，节名里没有 Appendix）。"""
    seen, out, in_app = set(), set(), False
    for s in doc["segments"]:
        sec = s.get("section", "")
        if not sec or sec in seen or sec in out:
            continue
        key = sec.split()[0]
        if sec.startswith("Appendix ") or (key in {x.split()[0] for x in seen} and re.fullmatch(r"[IVXLC\d]+", key)):
            in_app = True
        (out if in_app else seen).add(sec)
    return out


def select_segments(doc, sections=None):
    """sections 例：["abs", "2", "3", "4", "A"]；["body"] 表示只要正文（摘要和所有非附录的节）；None 表示全部。
    选中的正文引用到的图、表、脚注也会一起选上，即使它们在 HTML 里属于别的节（图常浮动到前面）。"""
    if not sections or sections == ["all"]:
        return [s for s in doc["segments"] if s["kind"] != "equation"]
    from .extract import referenced_floats
    chosen = set()
    app = appendix_sections(doc) if "body" in sections else set()
    for s in doc["segments"]:
        if s["kind"] == "equation":
            continue
        sec = s.get("section", "")
        if "body" in sections and sec not in app:
            chosen.add(s["id"])
            continue
        key = "abs" if s["kind"] == "abstract" else (sec.split()[0] if sec else "")
        if sec.startswith("Appendix "):
            key = sec.split()[1]
        if key in sections:
            chosen.add(s["id"])
    chosen |= referenced_floats(doc["segments"], chosen)
    return [s for s in doc["segments"] if s["id"] in chosen]


def make_tasks(doc, terms, hedges, sections=None):
    tasks, prev = [], ""
    chosen = {s["id"] for s in select_segments(doc, sections)}
    for s in doc["segments"]:
        if s["id"] in chosen:
            tasks.append(build_task(s, prev, doc["title"], terms, hedges))
        if s["kind"] in ("para", "abstract", "item"):
            prev = s["en"]
    return tasks


class ApiError(RuntimeError):
    """接口返回的错误。retryable 为 False 时（密钥错、模型名错、余额不足等）重试也没用，直接报出来。"""

    def __init__(self, msg, status=None, retryable=True):
        super().__init__(msg)
        self.status, self.retryable = status, retryable


def extract_json(text):
    """从模型回复里取出 JSON 对象：去掉推理模型的 <think> 段和 ```json 包裹，
    还不行就取第一个 { 到最后一个 } 之间的内容。"""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text).strip()
    try:
        return json.loads(text)
    except ValueError:
        a, b = text.find("{"), text.rfind("}")
        if a < 0 or b <= a:
            raise
        return json.loads(text[a:b + 1])


class OpenAICompatible:
    """最小实现，只用标准库。每个实例绑定一个角色，模型配置按角色从环境变量读取（见 roles.py）。
    每次 chat 都是全新的一段对话，不保留历史。
    接口不支持 response_format（JSON 模式）时自动退回普通模式，靠提示词和 extract_json 取 JSON。"""

    def __init__(self, role="translator", base_url=None, model=None, api_key=None, temperature=0.1, timeout=180):
        cfg = model_config(role)
        self.role = role
        self.base_url = (base_url or cfg[0]).rstrip("/")
        self.model = model or cfg[1]
        self.api_key = api_key or cfg[2]
        self.temperature = temperature
        self.timeout = timeout
        self.extra = extra_body(role)
        self.json_mode = True
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}
        if not (self.base_url and self.model and self.api_key):
            raise SystemExit(f"角色 {role} 缺少接口配置，请设置环境变量（见 roles.py 的说明）")

    @property
    def name(self):
        """写进译者记录的名字：模型名，带附加参数时注明（例如关掉思考模式）。"""
        if not self.extra:
            return self.model
        if self.extra == {"thinking": {"type": "disabled"}}:
            return f"{self.model}（关思考）"
        return f"{self.model}（{json.dumps(self.extra, ensure_ascii=False, separators=(',', ':'))}）"

    def _post(self, body):
        req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            raise ApiError(f"HTTP {e.code}：{detail}", e.code, retryable=e.code in (408, 409, 429) or e.code >= 500)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise ApiError(f"连接失败：{e}")

    def _chat(self, messages):
        body = {"model": self.model, "messages": messages, **self.extra}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        t0, waits = time.time(), 0
        while True:
            try:
                data = self._post(body)
                break
            except ApiError as e:
                if e.status == 429 and waits < 6:  # 限流（例如 Kimi 免费档每分钟 3 次）：等一会儿再发，不算出错重试
                    waits += 1
                    m = re.search(r"after (\d+(?:\.\d+)?) ?s", str(e))
                    time.sleep(max(float(m.group(1)) if m else 0, 20 * waits))
                    continue
                # 接口不接受某个可选参数（JSON 模式；或像 kimi-k2.6 只允许 temperature=1）：
                # 去掉它、用服务端默认值再发，之后这个实例都不再带它
                bad = [k for k in ("response_format", "temperature") if k in body and k in str(e)]
                if e.status != 400 or not bad:
                    raise
                body.pop(bad[0])
                if bad[0] == "response_format":
                    self.json_mode = False
                else:
                    self.temperature = None
        self.usage["calls"] += 1
        self.usage["seconds"] += time.time() - t0
        u = data.get("usage") or {}
        # 缓存命中的输入 token：DeepSeek 叫 prompt_cache_hit_tokens，OpenAI 和 Kimi 放在 prompt_tokens_details 里
        cached = (u.get("prompt_cache_hit_tokens") or (u.get("prompt_tokens_details") or {}).get("cached_tokens")
                  or u.get("cached_tokens") or 0)
        got = {"prompt_tokens": u.get("prompt_tokens") or 0, "completion_tokens": u.get("completion_tokens") or 0,
               "cached_tokens": cached}
        # 北京时间工作日 9–12、14–18 点是 DeepSeek 的高峰时段（价格翻倍），单独记一笔，方便算钱
        bj = time.gmtime(time.time() + 8 * 3600)
        if bj.tm_wday < 5 and (9 <= bj.tm_hour < 12 or 14 <= bj.tm_hour < 18):
            got.update({f"peak_{k}": v for k, v in list(got.items())})
        for k, v in got.items():
            self.usage[k] = self.usage.get(k, 0) + v
        if "choices" not in data:
            raise ApiError(f"返回里没有 choices：{json.dumps(data, ensure_ascii=False)[:300]}")
        return data["choices"][0]["message"].get("content") or ""

    def ask_json(self, system, payload, key, n, retries=2):
        """发一段新对话，要求返回 {key: [...]} 且条数为 n；不对就带着错误原因重试。"""
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        err = ""
        for attempt in range(retries + 1):
            if err:
                messages.append({"role": "user", "content": f"上一次输出有问题：{err}。请重新输出，条数必须是 {n}。"})
            try:
                out = extract_json(self._chat(messages))[key]
                if len(out) == n:
                    return out
                err = f"条数 {len(out)} 与输入 {n} 不一致"
            except ApiError as e:
                if not e.retryable:
                    raise RuntimeError(str(e))
                err = str(e)
                time.sleep(2 * (attempt + 1))
            except Exception as e:  # 解析错误：稍等后重试
                err = f"{type(e).__name__}: {e}"
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(err)

    def translate(self, task, retries=2):
        n = len(task["sentences"])
        try:
            out = self.ask_json(load_prompt("translator"), payload_translator(task), "sentences", n, retries)
        except RuntimeError as e:
            return {"sentences": [{"zh": "", "conf": "low", "note": f"自动翻译失败：{e}"} for _ in range(n)]}
        return {"sentences": [{"zh": o.get("zh", ""), "conf": o.get("conf", "mid"), "note": o.get("note", "")}
                              for o in out]}
