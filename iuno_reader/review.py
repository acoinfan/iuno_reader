"""独立审校：检查译文的意思是否和原文一致（规则查不出来的那一层）。

两种做法，结果都写进 data/<id>/review.json：
{
  "<片段 id>": {"reviewer": "谁审的", "mode": "blind|backtranslate",
                "reviews": [{"verdict": "ok|minor|major", "category": ["negation", ...],
                             "issue": "中文说明", "suggestion": "建议译文（可空）", "backtranslation": "（回译模式）"}]}
}

1. 盲审（blind）：审校者只看到英文原文、中文译文和前后句，看不到翻译者的置信度和备注。
   用 `review-tasks` 导出清单，交给人、另一个模型或子代理审，审完写成上面的格式。
2. 回译比对（backtranslate）：另一个模型先只看中文译回英文（看不到原文），
   再在英文里比较原文与回译。用 `review --mode backtranslate`，需要 OpenAI 兼容接口。
"""
import json
import re

from .translate import OpenAICompatible

CATEGORIES = {
    "negation": "否定", "quantity": "数量或数字", "hedge": "限定词或语气", "scope": "范围或修饰对象",
    "agent": "主语或施受关系", "causal": "因果或逻辑关系", "omission": "漏译", "addition": "增译",
    "term": "术语", "other": "其他",
}

JUDGE_RULES = """你是独立的学术翻译审校，只负责找出中文译文和英文原文在意思上的偏差，供研究者精读时参考。
逐句比较，判断标准：
- ok：意思完全一致（措辞不同、语序调整都可以）。
- minor：意思基本一致，但有不影响理解的小偏差（语气轻重、措辞不够准确、轻微省略）。
- major：意思有实质偏差：否定或肯定弄反、数字或数量不同、限定词丢失或被加强（如 suggest 译成「证明」）、修饰对象或范围变了、主语或施受关系弄错、因果或转折关系不对、漏译或增译了实质内容。
category 从这些里选（可多选）：negation, quantity, hedge, scope, agent, causal, omission, addition, term, other。
issue 用一句中文说明问题；suggestion 给出改进后的译文（ok 时留空）。不要因为风格偏好判 minor 或 major。
只输出 JSON：{"reviews": [{"verdict": "ok|minor|major", "category": [], "issue": "", "suggestion": ""}]}，条数与输入句子数相同。"""

BT_RULES = """把下面的中文句子逐句译成英文。直译，尽量保留原句的每一个限定、否定、数量和逻辑关系，不要润色，不要补充。公式 $...$、反引号内容和 {fn1} 等占位符原样保留。
只输出 JSON：{"sentences": ["...", "..."]}，条数与输入相同。"""

BT_JUDGE_RULES = """比较两组英文句子：A 是论文原文，B 是把中文译文回译成英文的结果。判断 B 是否保留了 A 的意思（措辞不同没关系，只看意思）。
判断标准和输出格式：
- ok：意思一致；minor：小偏差（语气、措辞、轻微省略）；major：实质偏差（否定、数量、限定词、范围、主宾、因果、漏译、增译）。
category 从 negation, quantity, hedge, scope, agent, causal, omission, addition, term, other 里选；issue 用一句中文说明 A 和 B 的差别。
注意：B 经过了两次翻译，轻微的措辞变化很正常，不要判错；只有意思真的变了才判 minor 或 major。
只输出 JSON：{"reviews": [{"verdict": "ok|minor|major", "category": [], "issue": ""}]}，条数与输入相同。"""


def make_review_tasks(doc, translation):
    """导出盲审清单：只含原文、译文和前后句，不含翻译者的置信度和备注。"""
    tasks = []
    for seg in doc["segments"]:
        tr = translation.get(seg["id"])
        if not tr or seg["kind"] == "equation":
            continue
        zh = [s.get("zh", "") for s in tr.get("sentences", [])]
        tasks.append({"id": seg["id"], "kind": seg["kind"], "section": seg.get("section", ""),
                      "subsection": seg.get("subsection", ""),
                      "pairs": [{"en": en, "zh": zh[i] if i < len(zh) else ""} for i, en in enumerate(seg["sentences"])]})
    return tasks


def attach_reviewed_text(review, tasks):
    """给每条审校意见记下它审的是哪一版译文（zh_reviewed）。
    译文以后改了，旧意见就会被识别为过期，不再标到新译文上。"""
    by_id = {t["id"]: t for t in tasks}
    for sid, seg_rv in review.items():
        t = by_id.get(sid)
        if not t:
            continue
        for item, pair in zip(seg_rv.get("reviews", []), t["pairs"]):
            item.setdefault("zh_reviewed", pair["zh"])
    return review


def _parse(content, key):
    content = re.sub(r"^```(?:json)?|```$", "", content.strip()).strip()
    return json.loads(content)[key]


class ApiReviewer:
    """用 OpenAI 兼容接口审校。建议用和翻译不同的模型，环境变量：
    IUNO_REVIEW_BASE_URL / IUNO_REVIEW_MODEL / IUNO_REVIEW_API_KEY（没设就沿用 IUNO_READER_*）。"""

    def __init__(self):
        import os
        self.eng = OpenAICompatible(
            base_url=os.environ.get("IUNO_REVIEW_BASE_URL") or None,
            model=os.environ.get("IUNO_REVIEW_MODEL") or None,
            api_key=os.environ.get("IUNO_REVIEW_API_KEY") or None)

    def _ask(self, system, payload, key, n, retries=2):
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        err = ""
        for _ in range(retries + 1):
            if err:
                messages.append({"role": "user", "content": f"上一次输出有问题：{err}。请重新输出，条数必须是 {n}。"})
            try:
                out = _parse(self.eng._chat(messages), key)
                if len(out) == n:
                    return out
                err = f"条数 {len(out)} 与输入 {n} 不一致"
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
        raise RuntimeError(err)

    def blind(self, task):
        n = len(task["pairs"])
        out = self._ask(JUDGE_RULES, {"section": task["section"], "pairs": task["pairs"]}, "reviews", n)
        for o, p in zip(out, task["pairs"]):
            o["zh_reviewed"] = p["zh"]
        return {"reviewer": self.eng.model, "mode": "blind", "reviews": out}

    def backtranslate(self, task):
        n = len(task["pairs"])
        bt = self._ask(BT_RULES, {"sentences": [p["zh"] for p in task["pairs"]]}, "sentences", n)
        pairs = [{"A": p["en"], "B": b} for p, b in zip(task["pairs"], bt)]
        out = self._ask(BT_JUDGE_RULES, {"pairs": pairs}, "reviews", n)
        for o, b, p in zip(out, bt, task["pairs"]):
            o["backtranslation"] = b
            o["zh_reviewed"] = p["zh"]
        return {"reviewer": self.eng.model, "mode": "backtranslate", "reviews": out}


def review_flags(r):
    """把一条审校意见变成核对标记。"""
    v = (r or {}).get("verdict", "ok")
    if v not in ("minor", "major"):
        return []
    cats = "、".join(CATEGORIES.get(c, c) for c in r.get("category", []) or []) or "未分类"
    msg = f"审校（{cats}）：{r.get('issue', '').strip()}"
    if r.get("suggestion"):
        msg += f"｜建议：{r['suggestion'].strip()}"
    if r.get("backtranslation"):
        msg += f"｜回译：{r['backtranslation'].strip()}"
    return [{"level": "red" if v == "major" else "yellow", "code": f"review_{v}", "msg": msg}]
