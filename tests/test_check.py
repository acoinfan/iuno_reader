"""核对规则的单元测试：python tests/test_check.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from iuno_reader.check import check_sentence  # noqa: E402
from iuno_reader.extract import split_sentences  # noqa: E402
from iuno_reader.glossary import load_hedges  # noqa: E402

H = load_hedges()
T = {"inert": {"zh": ["惰性的"], "note": ""}, "few-shot": {"zh": ["少量示例"], "note": ""}}


def codes(en, zh, conf="high"):
    return {f["code"] for f in check_sentence(en, zh, T, H, conf)}


cases = [
    # (英文, 中文, 应该出现的代码, 不应该出现的代码)
    ("Models do not use these representations.", "模型使用这些表示。", {"negation"}, set()),
    ("Models do not use these representations.", "模型没有使用这些表示。", set(), {"negation"}),
    ("We evaluate on 1000 token assignments.", "我们在许多种分配上评估。", {"number"}, set()),
    ("These results suggest that the representations are inert.", "这些结果证明这些表示是惰性的。", {"hedge_strengthened"}, set()),
    ("These results suggest that the representations are inert.", "这些结果表明这些表示是惰性的。", set(), {"hedge_strengthened", "hedge_missing"}),
    ("The representations are largely inert.", "这些表示是惰性的。", {"hedge_missing"}, set()),
    # 被否定的禁用词不算语气加强；没被否定的仍然要标
    ("This suggests, but does not prove, that it fails.", "这表明——但并不证明——它失败了。", set(), {"hedge_mixed", "hedge_strengthened"}),
    ("This suggests that it fails.", "这表明并证明了它失败了。", {"hedge_mixed"}, set()),
    ("The rule maps $s_{i,j}$ to $s_{i+2,j}$.", "该规则把 $s_{i,j}$ 映射到另一个状态。", {"math"}, set()),
    ("We use prefilled responses{fn3} here.", "这里我们使用预填充的回复。", {"placeholder"}, set()),
    ("as shown by Park et al. (2025a).", "如 Park 等人所示。", {"citation"}, set()),
    ("push adjacent words to be similar to one another.", "把相邻的词推得彼此相似。", set(), {"number_word"}),
    ("One might worry that this is wrong.", "有人可能担心这是错的。", set(), {"number_word"}),
    ("The prompt consists of two components.", "提示词由若干部分组成。", {"number_word"}, set()),
    ("We use few-shot examples.", "我们使用 few-shot 例子。", set(), {"term"}),
    ("We use few-shot examples.", "我们使用一些例子。", {"term"}, set()),
    ("It works.", "", {"missing"}, set()),
]

fail = 0
for en, zh, must, mustnot in cases:
    got = codes(en, zh)
    ok = must <= got and not (mustnot & got)
    if not ok:
        fail += 1
    print("通过" if ok else "失败", "|", en[:50], "|", zh[:30], "| 得到", sorted(got))

# 分句：编号标签并入下一句；缩写不切
s = split_sentences("1. We introduce a task. Park et al. (2025a) showed this, e.g. on grids. (a) First item here.")
print(s)
assert s[0].startswith("1. We introduce"), s
assert any("et al. (2025a) showed this, e.g. on grids." in x for x in s), s
assert s[-1].startswith("(a) First"), s

print("失败数", fail)
sys.exit(1 if fail else 0)
