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
ORIG_CHAT = T.OpenAICompatible._chat


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

# 3. 回译审校：先回译，再比对；回译那一步收到的内容里不能有英文原文
seen = []


def spy_chat(self, messages):
    seen.append((self.role, messages[-1]["content"]))
    return replies.pop(0)


T.OpenAICompatible._chat = spy_chat
rv = ApiReviewer("backtranslate")
rtask = {"section": "S", "pairs": [{"en": "Models do not use it.", "zh": "模型使用它。"},
                                   {"en": "It works.", "zh": "它有效。"}]}
replies[:] = [json.dumps({"sentences": ["Models use it.", "It works."]}),
              json.dumps({"reviews": [{"verdict": "major", "category": ["negation"], "issue": "否定丢失"},
                                      {"verdict": "ok", "category": [], "issue": ""}]})]
r = rv.review(rtask)
assert r["mode"] == "backtranslate" and r["reviews"][0]["backtranslation"] == "Models use it.", r
assert r["reviews"][0]["verdict"] == "major" and r["reviews"][1]["verdict"] == "ok"
assert r["reviews"][0]["zh_reviewed"] == "模型使用它。"
assert seen[0][0] == "backtranslator" and "Models do not use it." not in seen[0][1], seen[0]
assert seen[1][0] == "bt_judge" and "模型使用它" not in seen[1][1], seen[1]
print("回译审校：两步流程、回译看不到原文、比对看不到中文 通过")

# 4. 盲审
rv = ApiReviewer("blind")
replies[:] = [json.dumps({"reviews": [{"verdict": "minor", "category": ["hedge"], "issue": "语气", "suggestion": "x"},
                                      {"verdict": "ok", "category": [], "issue": "", "suggestion": ""}]})]
r = rv.review(rtask)
assert r["mode"] == "blind" and r["reviews"][0]["verdict"] == "minor"
assert rv.take_usage()["calls"] == 0 and rv.reviewer.usage["calls"] == 0  # 假回复不经过真实请求，用量为 0
print("盲审：通过")

# 5. 接口层：不支持 JSON 模式时自动退回；401 不重试；推理模型的 <think> 段和前后废话能去掉
T.OpenAICompatible._chat = ORIG_CHAT
bodies = []


def fake_post(self, body):
    bodies.append(dict(body))
    if "response_format" in body:
        raise T.ApiError('HTTP 400：{"error":"response_format is not supported"}', 400, retryable=False)
    content = "<think>先想想……{\"x\": 1}</think>好的，结果如下：\n```json\n" + \
        json.dumps({"sentences": [{"zh": "甲", "conf": "high"}, {"zh": "乙", "conf": "high"}]}) + "\n```"
    return {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


T.OpenAICompatible._post = fake_post
eng = T.OpenAICompatible()
out = eng.translate(task)
assert [s["zh"] for s in out["sentences"]] == ["甲", "乙"], out
assert not eng.json_mode and "response_format" not in bodies[-1] and eng.usage["prompt_tokens"] == 10
print("接口层：JSON 模式退回、去掉思考段 通过")

bodies.clear()


def temp_one(self, body):
    bodies.append(dict(body))
    if "temperature" in body:
        raise T.ApiError('HTTP 400：{"error":{"message":"invalid temperature: only 1 is allowed for this model"}}', 400,
                         retryable=False)
    return fake_post(self, body)


T.OpenAICompatible._post = temp_one
eng = T.OpenAICompatible()
out = eng.translate(task)
assert [s["zh"] for s in out["sentences"]] == ["甲", "乙"], out
assert eng.temperature is None and "temperature" not in bodies[-1] and not eng.json_mode, bodies
print("接口层：不接受 temperature 时去掉再发 通过")

hits, slept = [], []
T.time.sleep = slept.append  # 不真的等


def limited(self, body):
    hits.append(1)
    if len(hits) <= 2:
        raise T.ApiError("HTTP 429：request reached organization max RPM: 3, please try again after 1 seconds", 429)
    return fake_post(self, body)


T.OpenAICompatible._post = limited
out = T.OpenAICompatible().translate(task)
assert [s["zh"] for s in out["sentences"]] == ["甲", "乙"] and len(hits) == 4 and slept[:2] == [20, 40], (hits, slept)
print("接口层：限流时等待后重发 通过")

calls = []


def deny(self, body):
    calls.append(1)
    raise T.ApiError("HTTP 401：invalid api key", 401, retryable=False)


T.OpenAICompatible._post = deny
out = T.OpenAICompatible().translate(task, retries=2)
assert len(calls) == 1 and "401" in out["sentences"][0]["note"], (calls, out)
print("接口层：401 不重试 通过")
print("接口流程（模拟）测试全部通过")
