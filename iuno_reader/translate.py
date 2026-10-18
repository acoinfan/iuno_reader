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
import urllib.request

from .glossary import terms_in
from .roles import load_prompt, model_config, payload_translator


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


def select_segments(doc, sections=None):
    """sections 例：["abs", "2", "3", "4", "A"]；None 表示全部。
    选中的正文引用到的图、表、脚注也会一起选上，即使它们在 HTML 里属于别的节（图常浮动到前面）。"""
    if not sections:
        return [s for s in doc["segments"] if s["kind"] != "equation"]
    from .extract import referenced_floats
    chosen = set()
    for s in doc["segments"]:
        if s["kind"] == "equation":
            continue
        sec = s.get("section", "")
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


class OpenAICompatible:
    """最小实现，只用标准库。每个实例绑定一个角色，模型配置按角色从环境变量读取（见 roles.py）。
    每次 chat 都是全新的一段对话，不保留历史。"""

    def __init__(self, role="translator", base_url=None, model=None, api_key=None, temperature=0.1, timeout=120):
        cfg = model_config(role)
        self.role = role
        self.base_url = (base_url or cfg[0]).rstrip("/")
        self.model = model or cfg[1]
        self.api_key = api_key or cfg[2]
        self.temperature = temperature
        self.timeout = timeout
        if not (self.base_url and self.model and self.api_key):
            raise SystemExit(f"角色 {role} 缺少接口配置，请设置环境变量（见 roles.py 的说明）")

    def _chat(self, messages):
        body = json.dumps({"model": self.model, "messages": messages, "temperature": self.temperature,
                           "response_format": {"type": "json_object"}}).encode()
        req = urllib.request.Request(self.base_url + "/chat/completions", data=body, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.load(r)
        return data["choices"][0]["message"]["content"]

    def ask_json(self, system, payload, key, n, retries=2):
        """发一段新对话，要求返回 {key: [...]} 且条数为 n；不对就带着错误原因重试。"""
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        err = ""
        for attempt in range(retries + 1):
            if err:
                messages.append({"role": "user", "content": f"上一次输出有问题：{err}。请重新输出，条数必须是 {n}。"})
            try:
                content = re.sub(r"^```(?:json)?|```$", "", self._chat(messages).strip()).strip()
                out = json.loads(content)[key]
                if len(out) == n:
                    return out
                err = f"条数 {len(out)} 与输入 {n} 不一致"
            except Exception as e:  # 网络或解析错误：稍等后重试
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
