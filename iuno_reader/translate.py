"""翻译：两种方式产出同一种格式的 translation.json。

translation.json：
{
  "<片段 id>": {
    "zh_title": "（可选）所在小节的中文标题",
    "sentences": [{"zh": "译文", "conf": "high|mid|low", "note": "为什么没把握"}, ...]
  }
}

1. 人工 / Agent 翻译：用 `tasks` 命令导出待译清单（含上下文和相关术语），译好后写成上面的格式。
2. 自动翻译：OpenAI 兼容接口（DeepSeek、OpenAI 等）。配置见 README。
"""
import json
import os
import re
import time
import urllib.request

from .glossary import terms_in

RULES = """你是严谨的学术翻译，把英文论文逐句译成中文，供研究者对照原文精读。必须遵守：
1. 逐句翻译，输出的句数必须与输入完全相同，顺序一一对应，不合并、不拆分、不省略。
2. 准确优先于通顺：不增加原文没有的信息，不省略任何修饰语、限定条件和转折。
3. 限定词按强弱译，不能加强或减弱语气：suggest/indicate 译「表明」「提示」，不能译「证明」；may/might 译「可能」；largely 译「大多」「很大程度上」；partially 译「部分」；somewhat 译「有些」「一定程度上」；approximately 译「约」。
4. 否定词、数字、比较级、范围（at least、up to、more than）必须完整保留。
5. 公式 $...$ 和 $$...$$、反引号里的代码和专名、脚注标记 {fn1} 等占位符，原样照抄，不翻译、不改动。
6. 引用（如 Park et al. (2025a)）保留原文，不翻译人名和年份。
7. 术语按给定术语表翻译；关键术语第一次出现时可写成「中文（English）」。
8. 每句给出置信度 conf：high＝确定；mid＝基本确定但有可商榷之处；low＝有歧义、句子结构复杂、术语没把握或可能误读。conf 不是 high 时，用一句中文写明原因 note。
只输出 JSON：{"sentences": [{"zh": "...", "conf": "high|mid|low", "note": "..."}]}"""


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
    """sections 例：["abs", "2", "3", "4", "A"]；None 表示全部。"""
    if not sections:
        return [s for s in doc["segments"] if s["kind"] != "equation"]
    out = []
    for s in doc["segments"]:
        if s["kind"] == "equation":
            continue
        sec = s.get("section", "")
        key = "abs" if s["kind"] == "abstract" else (sec.split()[0] if sec else "")
        if sec.startswith("Appendix "):
            key = sec.split()[1]
        if key in sections:
            out.append(s)
    return out


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
    """最小实现，只用标准库。环境变量：
    IUNO_READER_BASE_URL（例 https://api.deepseek.com/v1）
    IUNO_READER_MODEL（例 deepseek-chat）
    IUNO_READER_API_KEY
    """

    def __init__(self, base_url=None, model=None, api_key=None, temperature=0.1, timeout=120):
        self.base_url = (base_url or os.environ.get("IUNO_READER_BASE_URL", "")).rstrip("/")
        self.model = model or os.environ.get("IUNO_READER_MODEL", "")
        self.api_key = api_key or os.environ.get("IUNO_READER_API_KEY", "")
        self.temperature = temperature
        self.timeout = timeout
        if not (self.base_url and self.model and self.api_key):
            raise SystemExit("缺少接口配置：请设置 IUNO_READER_BASE_URL、IUNO_READER_MODEL、IUNO_READER_API_KEY")

    def _chat(self, messages):
        body = json.dumps({"model": self.model, "messages": messages, "temperature": self.temperature,
                           "response_format": {"type": "json_object"}}).encode()
        req = urllib.request.Request(self.base_url + "/chat/completions", data=body, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.load(r)
        return data["choices"][0]["message"]["content"]

    def translate(self, task, retries=2):
        user = json.dumps({k: task[k] for k in ("paper", "section", "subsection", "previous_paragraph",
                                                 "terms", "sentences")}, ensure_ascii=False)
        messages = [{"role": "system", "content": RULES}, {"role": "user", "content": user}]
        last_err = ""
        for attempt in range(retries + 1):
            if last_err:
                messages.append({"role": "user", "content": f"上一次输出有问题：{last_err}。请重新输出，句数必须是 {len(task['sentences'])}。"})
            try:
                content = self._chat(messages)
                content = re.sub(r"^```(?:json)?|```$", "", content.strip()).strip()
                out = json.loads(content)["sentences"]
                if len(out) != len(task["sentences"]):
                    last_err = f"句数 {len(out)} 与输入 {len(task['sentences'])} 不一致"
                    continue
                return {"sentences": [{"zh": o.get("zh", ""), "conf": o.get("conf", "mid"),
                                       "note": o.get("note", "")} for o in out]}
            except Exception as e:  # 网络或解析错误：稍等后重试
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(2 * (attempt + 1))
        return {"sentences": [{"zh": "", "conf": "low", "note": f"自动翻译失败：{last_err}"}
                              for _ in task["sentences"]]}
