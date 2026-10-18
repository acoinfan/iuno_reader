"""角色隔离测试：python tests/test_roles.py
保证每个角色只看得到白名单里的东西，提示词各自独立，独立性判断正确。"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from iuno_reader import roles  # noqa: E402
from iuno_reader.review import make_review_tasks  # noqa: E402

EN = ["Models do not reliably use these representations.", "Results suggest they are largely inert."]
ZH = ["模型不能可靠地使用这些表示。", "结果表明它们大多是惰性的。"]
NOTE = "译者秘密备注XYZ"
doc = {"segments": [{"id": "p1", "kind": "para", "section": "3 Experiment", "subsection": "Analysis",
                     "en": " ".join(EN), "sentences": EN}]}
tr = {"p1": {"sentences": [{"zh": ZH[0], "conf": "low", "note": NOTE}, {"zh": ZH[1], "conf": "high", "note": ""}]}}
task = make_review_tasks(doc, tr)[0]
tt = {"paper": "P", "section": "3 Experiment", "subsection": "Analysis", "previous_paragraph": "prev",
      "terms": {"inert": "惰性的"}, "sentences": EN, "zh_secret": ZH[0], "note": NOTE}


def s(x):
    return json.dumps(x, ensure_ascii=False)


# 翻译：看得到英文，看不到任何中文译文或备注
p = s(roles.payload_translator(tt))
assert EN[0] in p and ZH[0] not in p and NOTE not in p, p
# 盲审：看得到原文和译文，看不到置信度和备注
p = s(roles.payload_reviewer(task))
assert EN[0] in p and ZH[0] in p and NOTE not in p and "conf" not in p, p
# 回译：只看得到中文，看不到任何英文原文
p = s(roles.payload_backtranslator(task))
assert ZH[0] in p and not any(e in p for e in EN) and not re.search(r"[A-Za-z]{4,}", p.replace("sentences", "")), p
# 回译比对：只看得到英文 A 和 B，看不到中文和备注
p = s(roles.payload_bt_judge(task, ["Models cannot reliably use them.", "Results show they are inert."]))
assert EN[0] in p and not re.search(r"[一-鿿]", p) and NOTE not in p, p
print("白名单：四个角色的输入隔离 通过")

# 提示词：四份各自独立、互不相同，并各自写明了能看到和看不到什么
texts = {r: roles.load_prompt(r) for r in roles.ROLES}
assert len(set(texts.values())) == 4
for r, t in texts.items():
    assert "【你能看到】" in t and "【你看不到】" in t, r
assert "英文原文" in texts["backtranslator"].split("【你看不到】")[1].split("\n")[0]
assert "中文译文" in texts["bt_judge"].split("【你看不到】")[1].split("\n")[0]
assert "置信度和备注" in texts["reviewer"].split("【你看不到】")[1].split("\n")[0]
print("提示词：四份独立、写明可见范围 通过")

# 模型配置：各角色按自己的前缀取，互不串用
for k in list(os.environ):
    if k.startswith("IUNO_"):
        del os.environ[k]
os.environ.update(IUNO_READER_BASE_URL="u0", IUNO_READER_MODEL="m-translate", IUNO_READER_API_KEY="k",
                  IUNO_REVIEW_BASE_URL="u1", IUNO_REVIEW_MODEL="m-review", IUNO_REVIEW_API_KEY="k",
                  IUNO_BT_BASE_URL="u2", IUNO_BT_MODEL="m-bt", IUNO_BT_API_KEY="k")
assert roles.model_config("translator")[1] == "m-translate"
assert roles.model_config("reviewer")[1] == "m-review"
assert roles.model_config("backtranslator")[1] == "m-bt"
assert roles.model_config("bt_judge")[1] == "m-review"  # 没设 IUNO_JUDGE_* 时退回 IUNO_REVIEW_*
print("模型配置：按角色分开 通过")

# 独立性判断
assert roles.independence("deepseek-chat", "gpt-4o") == "different-model"
assert roles.independence("deepseek-chat", "deepseek-chat") == "same-model"
assert roles.independence("claude-opus-5.5", "claude-opus-5.5（子代理）") == "same-model"
assert roles.independence("", "gpt-4o") == "unknown"
print("独立性判断 通过")
print("角色隔离测试全部通过")
