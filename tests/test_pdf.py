"""PDF 拆分（不联网）：现场生成一份两栏的合成 PDF，检查标题、摘要、节标题、跨栏拼段、行尾断词、图注、页码、参考文献和覆盖率。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pymupdf  # noqa: E402

from iuno_reader.pdfextract import extract_pdf  # noqa: E402

W, H = 612, 792
doc = pymupdf.open()
p1 = doc.new_page(width=W, height=H)
p1.insert_text((150, 80), "A Synthetic Paper About Columns", fontsize=16, fontname="hebo")
p1.insert_text((230, 105), "Ann Author and Bob Writer", fontsize=10)
p1.insert_text((280, 140), "Abstract", fontsize=11, fontname="hebo")
p1.insert_textbox(pymupdf.Rect(110, 150, 500, 210),
                  "We study how papers are laid out in two columns. The abstract is a single paragraph "
                  "that spans the full width of the page.", fontsize=10)
left, right = pymupdf.Rect(60, 230, 296, 700), pymupdf.Rect(316, 230, 552, 700)
p1.insert_text((60, 240), "1 Introduction", fontsize=11, fontname="hebo")
p1.insert_textbox(pymupdf.Rect(60, 250, 296, 330),
                  "Reading order matters. This paragraph starts in the left column and continues in the right "
                  "column without finishing its", fontsize=10)
p1.insert_textbox(pymupdf.Rect(60, 340, 296, 380), "Figure 1: A caption under a figure.", fontsize=9)
p1.insert_text((60, 420), "2 Method", fontsize=11, fontname="hebo")
p1.insert_textbox(pymupdf.Rect(60, 430, 296, 500), "Our method is simple and the repre-", fontsize=10)
p1.insert_textbox(pymupdf.Rect(316, 230, 552, 300), "sentation is linear. It works well.", fontsize=10)
p1.insert_text((316, 330), "1 N", fontsize=10, fontname="hebo")  # 表格里的粗体，不是节标题
p1.insert_text((316, 360), "3 Conclusion", fontsize=11, fontname="hebo")
p1.insert_textbox(pymupdf.Rect(316, 370, 552, 430), "Columns are fine. We conclude here.", fontsize=10)
p1.insert_text((300, 770), "1", fontsize=9)  # 页码
p2 = doc.new_page(width=W, height=H)
p2.insert_text((60, 80), "References", fontsize=11, fontname="hebo")
p2.insert_textbox(pymupdf.Rect(60, 90, 552, 120), "[1] A. Smith. Deep columns for reading. In Proc. ICML, 2021.", fontsize=9)
p2.insert_textbox(pymupdf.Rect(60, 125, 552, 155), "[2] B. Jones. Another paper. arXiv:2101.00001, 2021.", fontsize=9)
p2.insert_text((300, 770), "2", fontsize=9)
path = os.path.join(tempfile.mkdtemp(), "Synthetic2026-Columns.pdf")
doc.save(path)

d = extract_pdf(path, "Synthetic2026-Columns")
segs = d["segments"]
by_kind = {}
for s in segs:
    by_kind.setdefault(s["kind"], []).append(s)
assert d["title"] == "A Synthetic Paper About Columns", d["title"]
assert d["source"] == "pdf" and by_kind["abstract"][0]["en"].startswith("We study how papers"), by_kind.get("abstract")
secs = list(dict.fromkeys(s["section"] for s in segs))
assert secs == ["Abstract", "1 Introduction", "2 Method", "3 Conclusion"], secs
paras = [s["en"] for s in by_kind["para"]]
assert any("Our method is simple and the representation is linear." in p for p in paras), paras  # 跨栏拼段 + 断词
assert not any(p.strip() in ("1", "2") for p in paras), paras  # 页码去掉了
assert by_kind["caption"][0]["en"].startswith("Figure 1: A caption"), by_kind["caption"]
print("标题、摘要、节标题（含假标题「1 N」）、跨栏拼段、断词、图注、页码 通过")
bib = list(d["bib"].values())
assert len(bib) == 2 and bib[0]["title"] == "Deep columns for reading" and bib[1]["arxiv"] == "2101.00001", bib
print("参考文献 通过")
assert d["pdf"]["coverage"] >= 0.9, d["pdf"]
print("覆盖率", d["pdf"]["coverage"], "通过")
print("PDF 拆分测试全部通过")
