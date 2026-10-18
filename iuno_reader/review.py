"""独立审校：检查译文的意思是否和原文一致（规则查不出来的那一层）。

结果写进 data/<id>/review.<name>.json：
{
  "__meta__": {"reviewer": "审校者名字", "mode": "blind|backtranslate",
               "models": {"reviewer": "...", "backtranslator": "...", "bt_judge": "..."},
               "translator": "翻译者", "independence": "different-model|same-model|unknown"},
  "<片段 id>": {"reviewer": "...", "mode": "...",
                "reviews": [{"verdict": "ok|minor|major", "category": [...], "issue": "中文说明",
                             "suggestion": "建议译文", "backtranslation": "（回译模式）",
                             "zh_reviewed": "审的是哪一版译文"}]}
}

各角色的提示词在 prompts/，能看到的输入由 roles.py 的白名单决定，每次调用都是新对话：
- 盲审：reviewer 只看到原文和译文。
- 回译比对：backtranslator 只看到中文；bt_judge 只看到原文 A 和回译 B。两者是两个独立的引擎。
"""
from .roles import (INDEPENDENCE_ZH, independence, load_prompt, payload_backtranslator,  # noqa: F401
                    payload_bt_judge, payload_reviewer)
from .translate import OpenAICompatible

CATEGORIES = {
    "negation": "否定", "quantity": "数量或数字", "hedge": "限定词或语气", "scope": "范围或修饰对象",
    "agent": "主语或施受关系", "causal": "因果或逻辑关系", "omission": "漏译", "addition": "增译",
    "term": "术语", "other": "其他",
}


def make_review_tasks(doc, translation):
    """导出审校清单：只含原文、译文和节标题，不含翻译者的置信度和备注。"""
    tasks = []
    for seg in doc["segments"]:
        tr = translation.get(seg["id"])
        if not tr or seg["kind"] == "equation" or seg["id"].startswith("__"):
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
        if not t or sid.startswith("__"):
            continue
        for item, pair in zip(seg_rv.get("reviews", []), t["pairs"]):
            item.setdefault("zh_reviewed", pair["zh"])
    return review


class ApiReviewer:
    """用 OpenAI 兼容接口审校。三个角色各用一个独立引擎（模型配置见 roles.py）。"""

    def __init__(self, mode):
        self.mode = mode
        if mode == "blind":
            self.reviewer = OpenAICompatible(role="reviewer")
        else:
            self.backtranslator = OpenAICompatible(role="backtranslator")
            self.judge = OpenAICompatible(role="bt_judge")

    def models(self):
        if self.mode == "blind":
            return {"reviewer": self.reviewer.model}
        return {"backtranslator": self.backtranslator.model, "bt_judge": self.judge.model}

    def name(self):
        return "+".join(dict.fromkeys(self.models().values()))

    def review(self, task):
        n = len(task["pairs"])
        if self.mode == "blind":
            out = self.reviewer.ask_json(load_prompt("reviewer"), payload_reviewer(task), "reviews", n)
        else:
            bt = self.backtranslator.ask_json(load_prompt("backtranslator"), payload_backtranslator(task),
                                              "sentences", n)
            out = self.judge.ask_json(load_prompt("bt_judge"), payload_bt_judge(task, bt), "reviews", n)
            for o, b in zip(out, bt):
                o["backtranslation"] = b
        for o, p in zip(out, task["pairs"]):
            o["zh_reviewed"] = p["zh"]
        return {"reviewer": self.name(), "mode": self.mode, "reviews": out}


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
