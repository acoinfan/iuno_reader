"""审校合并的单元测试：python tests/test_review.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from iuno_reader.check import check_doc  # noqa: E402
from iuno_reader.glossary import load_hedges  # noqa: E402
from iuno_reader.review import make_review_tasks  # noqa: E402

doc = {"segments": [
    {"id": "p1", "kind": "para", "section": "1 X", "subsection": "", "en": "A. B. C.",
     "sentences": ["Models fail here.", "It is fine.", "Results vary."]},
    {"id": "p2", "kind": "para", "section": "1 X", "subsection": "", "en": "D.", "sentences": ["Done."]},
]}
tr = {"p1": {"sentences": [{"zh": "模型在这里失败。", "conf": "high", "note": "秘密备注"},
                           {"zh": "没问题。", "conf": "low", "note": "秘密备注"},
                           {"zh": "结果不同。", "conf": "high", "note": ""}]},
      "p2": {"sentences": [{"zh": "完成。", "conf": "high", "note": ""}]}}

tasks = make_review_tasks(doc, tr)
assert "秘密备注" not in str(tasks) and "conf" not in str(tasks), "盲审清单泄露了翻译者备注"

review = {
    "p1": {"reviewer": "测试审校", "mode": "blind", "reviews": [
        {"verdict": "ok", "category": [], "issue": "", "suggestion": ""},
        {"verdict": "major", "category": ["omission"], "issue": "漏译", "suggestion": "这没问题。"},
        {"verdict": "minor", "category": ["hedge"], "issue": "语气", "suggestion": ""}]},
    "p2": {"reviewer": "测试审校", "mode": "blind", "reviews": [  # 条数不对：应被拒收
        {"verdict": "major", "category": [], "issue": "x", "suggestion": ""},
        {"verdict": "major", "category": [], "issue": "y", "suggestion": ""}]},
}
d, stats = check_doc(doc, tr, {}, load_hedges(), [review])
p1, p2 = d["segments"][0]["pairs"], d["segments"][1]["pairs"]
codes = [[f["code"] for f in p["flags"]] for p in p1]
print(codes, [p["level"] for p in p1], stats)
assert "review_major" in codes[1] and p1[1]["level"] == "red"
assert "review_minor" in codes[2] and p1[2]["level"] == "yellow"
assert not any(f["code"].startswith("review") for f in p1[0]["flags"])
assert not any(f["code"].startswith("review") for f in p2[0]["flags"]), "条数不对的审校应被拒收"
assert any("[测试审校]" in f["msg"] and "建议：这没问题。" in f["msg"] for f in p1[1]["flags"])
print("审校合并测试通过")
