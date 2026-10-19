"""词性与词形测试（需要 spaCy 和 en_core_web_sm）：python tests/test_nlp.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from iuno_reader import nlp  # noqa: E402

if not nlp.available():
    print("spaCy 不可用，跳过")
    sys.exit(0)

s = ("We find that open-weights LMs struggle to use in-context semantics, even when their latent "
     "representations encode $H^{l}(\\mathcal{T})$; LLMs reliably predict a valid next-word{fn2} (Park et al., 2025a).")
res = nlp.analyze(s)
words = {s[a:b]: (k, kw) for a, b, k, kw in res}
print([(s[a:b], k, "实词" if kw else "") for a, b, k, kw in res])

assert "mathcal" not in words and "H" not in words, "公式里的字母不应被当成词"
assert "fn" not in words, "脚注占位符不应被当成词"
assert words["representations"][0] == "representation", "复数应还原成原形"
assert words["LLMs"][0] == "llm" and words["LMs"][0] == "lm", "缩写复数应去掉 s"
assert words["representations"][1] and words["struggle"][1], "名词和动词应算实词"
assert not words["the"][1] if "the" in words else True
assert not words["that"][1] and not words["their"][1], "虚词不应算实词"
for a, b, _, _ in res:  # 位置要能对回原句
    assert s[a:b].isalpha()
print("术语键：", [nlp.term_key(t) for t in ["topology", "random walk", "in-context representation learning", "DE"]])
assert nlp.term_key("topology") == "topology" and nlp.term_key("random walk") == "random walk"
assert nlp.term_key("DE") == "de"
print("词性与词形测试通过")
