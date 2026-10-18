"""用 PDF 转出的纯文本独立检查抽取覆盖率：python tests/coverage_vs_pdf.py <id> <pdf 纯文本路径>

做法：把两边都规范成小写单词序列，取 PDF 正文（参考文献之前）的连续 6 词片段，
看有多少比例能在 HTML 抽取结果里找到。再列出 PDF 里连续缺失最长的几段，人工判断是不是漏抽。
图里的文字、页眉页脚、断词等会造成一部分「缺失」，所以覆盖率不会是 100%。"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pid, pdf_txt = sys.argv[1], sys.argv[2]


def words(s):
    s = re.sub(r"\$[^$]*\$", " ", s)
    s = re.sub(r"-\n", "", s)  # PDF 行尾断词
    return re.findall(r"[a-z]+", s.lower())


doc = json.load(open(os.path.join(ROOT, "data", pid, "doc.json"), encoding="utf-8"))
html_words = words(" ".join(s["en"] for s in doc["segments"]))
N = 6
html_grams = {tuple(html_words[i:i + N]) for i in range(len(html_words) - N + 1)}

raw = open(pdf_txt, encoding="utf-8").read()
cut = re.search(r"\n(References|REFERENCES)\n", raw)
body = raw[:cut.start()] if cut else raw
pw = words(body)
hit = [tuple(pw[i:i + N]) in html_grams for i in range(len(pw) - N + 1)]
print(f"PDF 正文 {len(pw)} 词，6 词片段覆盖率 {sum(hit) / max(1, len(hit)):.1%}")

# 找连续缺失最长的几段
runs, start = [], None
for i, h in enumerate(hit + [True]):
    if not h and start is None:
        start = i
    elif h and start is not None:
        runs.append((i - start, start))
        start = None
runs.sort(reverse=True)
print("连续缺失最长的 8 段（词数：开头内容）")
for n, s in runs[:8]:
    print(f"  {n:4d}：{' '.join(pw[s:s + min(n + N - 1, 28)])}")
