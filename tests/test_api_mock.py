"""用假的模型回复测试接口相关代码的流程（不联网）：python tests/test_api_mock.py
覆盖：JSON 解析（含 ```json 包裹）、条数不对时的重试、回译 + 比对两步。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update(IUNO_READER_BASE_URL="http://mock", IUNO_READER_MODEL="mock-model", IUNO_READER_API_KEY="x")

from iuno_reader import translate as T  # noqa: E402
from iuno_reader.review import ApiReviewer  # noqa: E402

replies = []


def fake_chat(self, messages):
    return replies.pop(0)


T.OpenAICompatible._chat = fake_chat

# 1. 翻译：第一次条数不对，第二次用 ```json 包裹且条数正确
eng = T.OpenAICompatible()
task = {"paper": "P", "section": "S", "subsection": "", "previous_paragraph": "", "terms": {},
        "sentences": ["A is good.", "B is bad."]}
replies[:] = [json.dumps({"sentences": [{"zh": "A 好。", "conf": "high", "note": ""}]}),
              "```json\n" + json.dumps({"sentences": [{"zh": "A 好。", "conf": "high", "note": ""},
                                                     {"zh": "B 坏。", "conf": "mid", "note": "措辞"}]}) + "\n```"]
out = eng.translate(task)
assert [s["zh"] for s in out["sentences"]] == ["A 好。", "B 坏。"], out
assert not replies
print("翻译：重试与解析 通过")

# 2. 翻译彻底失败时返回空译文并标低置信，不抛异常
replies[:] = ["不是 JSON"] * 3
out = eng.translate(task, retries=2)
assert all(s["zh"] == "" and s["conf"] == "low" for s in out["sentences"]), out
print("翻译：失败兜底 通过")

# 3. 回译审校：先回译，再比对
rv = ApiReviewer()
rtask = {"section": "S", "pairs": [{"en": "Models do not use it.", "zh": "模型使用它。"},
                                   {"en": "It works.", "zh": "它有效。"}]}
replies[:] = [json.dumps({"sentences": ["Models use it.", "It works."]}),
              json.dumps({"reviews": [{"verdict": "major", "category": ["negation"], "issue": "否定丢失"},
                                      {"verdict": "ok", "category": [], "issue": ""}]})]
r = rv.backtranslate(rtask)
assert r["mode"] == "backtranslate" and r["reviews"][0]["backtranslation"] == "Models use it.", r
assert r["reviews"][0]["verdict"] == "major" and r["reviews"][1]["verdict"] == "ok"
print("回译审校：两步流程 通过")

# 4. 盲审
replies[:] = [json.dumps({"reviews": [{"verdict": "minor", "category": ["hedge"], "issue": "语气", "suggestion": "x"},
                                      {"verdict": "ok", "category": [], "issue": "", "suggestion": ""}]})]
r = rv.blind(rtask)
assert r["mode"] == "blind" and r["reviews"][0]["verdict"] == "minor"
print("盲审：通过")
print("接口流程（模拟）测试全部通过")
