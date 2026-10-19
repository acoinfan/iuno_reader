"""论文标识识别（不联网）：从一段文字里找出 arXiv 编号、DOI、链接、PMC 编号和标题；论文页面标签；PDF 链接换成论文页面。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from iuno_reader.resolve import Finder, _landing, _meta, find_inputs  # noqa: E402

# 1. 找标识
assert find_inputs("2602.04212 2609.24352v2") == ["2602.04212", "2609.24352"]
assert find_inputs("doi:10.18653/v1/2026.acl-long.366") == ["10.18653/v1/2026.acl-long.366"]
got = find_inputs("看看 https://aclanthology.org/2026.acl-long.366.pdf 和 10.1038/s41586-024-07522-w，还有 PMC6203620")
assert got == ["https://aclanthology.org/2026.acl-long.366.pdf", "10.1038/s41586-024-07522-w", "PMC6203620"], got
assert find_inputs("Emergent World Representations: Exploring a Sequence Model") == ["Emergent World Representations: Exploring a Sequence Model"]
assert find_inputs("短") == []
print("找标识 通过")

# 2. 论文页面的标签：属性值带不带引号都认，HTML 实体要还原
page = ('<meta content="Investigating Motivated Inference in LLMs" name=citation_title>'
        "<meta name='citation_doi' content='10.18653/v1/2025.winlp-main.30'>"
        '<meta property="og:title" content="A &amp; B">')
m = _meta(page)
assert m["citation_title"] == "Investigating Motivated Inference in LLMs", m
assert m["citation_doi"] == "10.18653/v1/2025.winlp-main.30" and m["og:title"] == "A & B", m
print("页面标签 通过")

# 3. PDF 链接换成论文页面
assert _landing("https://aclanthology.org/2025.winlp-main.30.pdf") == "https://aclanthology.org/2025.winlp-main.30/"
assert _landing("https://openreview.net/pdf?id=BZ5a1r-kVsf") == "https://openreview.net/forum?id=BZ5a1r-kVsf"
assert _landing("https://proceedings.neurips.cc/paper_files/paper/2024/file/b1b1-Paper-Conference.pdf") == \
    "https://proceedings.neurips.cc/paper_files/paper/2024/hash/b1b1-Abstract-Conference.html"
print("PDF 链接换页面 通过")

# 4. arXiv 自己的标识不联网直接取编号
import tempfile  # noqa: E402
tmp = tempfile.mkdtemp()
f = Finder(tmp, lookup=False)
for s in ("https://arxiv.org/abs/2602.04212v3", "https://ar5iv.labs.arxiv.org/html/2609.17376",
          "https://doi.org/10.48550/arXiv.2602.04212", "2602.04212"):
    assert f.find(s)["arxiv"] in ("2602.04212", "2609.17376"), s
print("arXiv 标识直接取编号 通过")
print("标识识别测试全部通过")
