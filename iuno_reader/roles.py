"""四个角色的隔离：各自的提示词、能看到的输入（白名单）、各自的模型配置。

| 角色 | 提示词 | 能看到 | 看不到 |
|---|---|---|---|
| translator 翻译 | prompts/translator.md | 英文句子、节标题、上一段原文、命中的术语 | 任何审校、回译 |
| reviewer 盲审 | prompts/reviewer_blind.md | 节标题、命中的术语及规定译法、逐句的英文原文与中文译文 | 译者身份、置信度和备注、其他审校 |
| backtranslator 回译 | prompts/backtranslator.md | 只有中文译文 | 英文原文、标题、备注、审校 |
| bt_judge 回译比对 | prompts/bt_judge.md | 英文原文 A、回译 B、命中术语的英文 | 中文译文（含术语的中文译法）、备注、审校 |

每次调用都是全新的一段对话，角色之间不共享对话历史。
模型配置（环境变量，没设就依次退回到后面的前缀）：
  translator:      IUNO_TRANSLATE_*  → IUNO_READER_*
  reviewer:        IUNO_REVIEW_*     → IUNO_READER_*
  backtranslator:  IUNO_BT_*         → IUNO_REVIEW_* → IUNO_READER_*
  bt_judge:        IUNO_JUDGE_*      → IUNO_REVIEW_* → IUNO_READER_*
  其中 * 为 BASE_URL / MODEL / API_KEY。
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROLES = {
    "translator": {"prompt": "translator.md", "env": ["IUNO_TRANSLATE", "IUNO_READER"]},
    "reviewer": {"prompt": "reviewer_blind.md", "env": ["IUNO_REVIEW", "IUNO_READER"]},
    "backtranslator": {"prompt": "backtranslator.md", "env": ["IUNO_BT", "IUNO_REVIEW", "IUNO_READER"]},
    "bt_judge": {"prompt": "bt_judge.md", "env": ["IUNO_JUDGE", "IUNO_REVIEW", "IUNO_READER"]},
}


def load_prompt(role):
    with open(os.path.join(ROOT, "prompts", ROLES[role]["prompt"]), encoding="utf-8") as f:
        return f.read().strip()


def extra_body(role):
    """角色的附加请求参数：环境变量 <前缀>_EXTRA 里的 JSON，原样并进请求体。
    例如关掉 DeepSeek 的思考模式：IUNO_TRANSLATE_EXTRA='{"thinking": {"type": "disabled"}}'"""
    for prefix in ROLES[role]["env"]:
        if os.environ.get(f"{prefix}_EXTRA"):
            return json.loads(os.environ[f"{prefix}_EXTRA"])
    return {}


def model_config(role):
    """返回 (base_url, model, api_key)，按角色的前缀顺序找第一个设置齐全的。"""
    for prefix in ROLES[role]["env"]:
        cfg = tuple(os.environ.get(f"{prefix}_{k}", "") for k in ("BASE_URL", "MODEL", "API_KEY"))
        if all(cfg):
            return cfg
    return ("", "", "")


# ---------- 白名单：每个角色只拿到这些字段 ----------

def payload_translator(task):
    return {k: task[k] for k in ("paper", "section", "subsection", "previous_paragraph", "terms", "sentences")}


def payload_reviewer(task):
    return {"section": task.get("section", ""), "terms": task.get("terms", {}),
            "pairs": [{"en": p["en"], "zh": p["zh"]} for p in task["pairs"]]}


def payload_backtranslator(task):
    return {"sentences": [p["zh"] for p in task["pairs"]]}


def payload_bt_judge(task, backtranslations):
    # 只给术语的英文（不给中文译法）：回译把这些术语说成别的英文不算偏差
    return {"terms": sorted(task.get("terms", {})),
            "pairs": [{"A": p["en"], "B": b} for p, b in zip(task["pairs"], backtranslations)]}


_VENDORS = [("deepseek", "deepseek"), ("gpt", "openai"), ("o1", "openai"), ("o3", "openai"), ("o4", "openai"),
            ("chatgpt", "openai"), ("claude", "anthropic"), ("gemini", "google"), ("gemma", "google"),
            ("qwen", "alibaba"), ("qwq", "alibaba"), ("kimi", "moonshot"), ("moonshot", "moonshot"),
            ("glm", "zhipu"), ("llama", "meta"), ("mistral", "mistral"), ("grok", "xai"), ("doubao", "bytedance")]


def vendor(model):
    """模型名 → 出自哪一家。认不出就取第一个词（例如 foo-7b → foo）。
    带供应商前缀的写法（openrouter 的 deepseek/deepseek-chat）只看斜杠后面的模型名。"""
    name = model.lower().split("/")[-1].strip()
    for prefix, v in _VENDORS:
        if name.startswith(prefix):
            return v
    return re.split(r"[-_:.\s（(]", name)[0]


def independence(translator_model, reviewer_model):
    """判断审校相对于翻译的独立性：same-model、same-vendor（同一家的不同型号，容易犯同样的错）、
    different-model、unknown。"""
    if not translator_model or not reviewer_model:
        return "unknown"
    if " + " in translator_model:  # 几个译者合译：按和审校最接近的那个算
        return worst_independence([independence(t, reviewer_model) for t in translator_model.split(" + ")])
    t, r = translator_model.lower(), reviewer_model.lower()
    if t == r or t in r or r in t:
        return "same-model"
    return "same-vendor" if vendor(t) == vendor(r) else "different-model"


_IND_ORDER = ["same-model", "same-vendor", "unknown", "different-model"]


def worst_independence(inds):
    """几个审校模型（例如回译 + 比对）里取独立性最差的那个。"""
    return min(inds, key=_IND_ORDER.index) if inds else "unknown"


INDEPENDENCE_ZH = {"different-model": "不同模型", "same-vendor": "同一家的不同模型（独立性不足）",
                   "same-model": "同一模型（独立性不足）", "unknown": "独立性未知"}
