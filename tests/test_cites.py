"""引用和参考文献对应的单元测试（不联网）：python tests/test_cites.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from iuno_reader.extract import extract  # noqa: E402
from iuno_reader.library import Resolver, load_library  # noqa: E402
from iuno_reader.render import _cite_marks, _en_html, _zh_cite_marks, _zh_html  # noqa: E402

HTML = """<html><body><article>
<h1 class="ltx_title ltx_title_document">Test Paper</h1>
<section><h2 class="ltx_title">1 Introduction</h2>
<div class="ltx_para"><p class="ltx_p">Prior work
<cite class="ltx_cite ltx_citemacro_citet">Lepori et al. (<a class="ltx_ref" href="#bib.bib12">2026</a>)</cite>
found inert representations. Others disagree
<cite class="ltx_cite ltx_citemacro_citep">(<a class="ltx_ref" href="#bib.bib3">Park et al., 2025a</a>; <a class="ltx_ref" href="#bib.bib7">Wu et al., 2024</a>)</cite>.</p></div>
</section>
<section class="ltx_bibliography"><ul>
<li class="ltx_bibitem" id="bib.bib12"><span class="ltx_tag ltx_tag_bibitem">Lepori et al. (2026)</span>
<span class="ltx_bibblock"><span class="ltx_text ltx_bib_author">M. A. Lepori and T. Linzen</span></span>
<span class="ltx_bibblock"><span class="ltx_text ltx_bib_title">Language models struggle to use representations learned in-context</span>.</span>
<span class="ltx_bibblock">In ACL 2026.</span>
<span class="ltx_bibblock ltx_bib_cited">Cited by: §1.</span></li>
<li class="ltx_bibitem" id="bib.bib3"><span class="ltx_tag ltx_tag_bibitem">Park et al. (2025a)</span>
<span class="ltx_bibblock">C. F. Park and A. Lee. 2025.</span>
<span class="ltx_bibblock">ICLR: in-context learning of representations.</span>
<span class="ltx_bibblock">arXiv preprint arXiv:2501.00070.</span></li>
<li class="ltx_bibitem" id="bib.bib7"><span class="ltx_tag ltx_tag_bibitem">Wu et al. (2024)</span>
<span class="ltx_bibblock">Z. Wu. 2024.</span><span class="ltx_bibblock">Reasoning or reciting?</span></li>
</ul></section></article></body></html>"""

ok = True


def check(name, cond, detail=""):
    global ok
    print(("通过 " if cond else "失败 ") + name + ("" if cond else f"：{detail}"))
    ok = ok and cond


doc = extract(HTML, "9999.00001")
bib, seg = doc["bib"], doc["segments"][0]
check("抽出三条参考文献", set(bib) == {"bib.bib12", "bib.bib3", "bib.bib7"}, bib.keys())
check("带 ltx_bib_title 的条目取到标题", bib["bib.bib12"]["title"].startswith("Language models struggle"), bib["bib.bib12"])
check("bibtex 式条目：第二块当标题、抽出 arXiv 号",
      bib["bib.bib3"]["title"].startswith("ICLR") and bib["bib.bib3"]["arxiv"] == "2501.00070", bib["bib.bib3"])
check("「Cited by」那块不算进条目正文", "Cited by" not in bib["bib.bib12"]["text"], bib["bib.bib12"]["text"])
check("正文引用：Lepori et al. (2026) 保留右括号",
      seg["cites"][0]["text"] == "Lepori et al. (2026)", seg["cites"])
check("括号式引用去掉外层括号、两个锚点", seg["cites"][1]["text"].startswith("Park et al., 2025a;") and len(seg["cites"][1]["refs"]) == 2,
      seg["cites"])
check("正文英文不受影响", "Lepori et al. (2026) found" in seg["en"], seg["en"])

# 引用位置：逐句找，多锚点分别标
marks = _cite_marks(seg["sentences"], seg["cites"])
s0, s1 = seg["sentences"]
check("第一句标出 Lepori", [s0[a:b] for a, b, _ in marks[0]] == ["Lepori et al. (2026)"], marks)
check("第二句分别标出 Park、Wu", [s1[a:b] for a, b, _ in marks[1]] == ["Park et al., 2025a", "Wu et al., 2024"], marks)
h = _en_html(s0, [], None, marks[0], {"bib.bib12": {"lib": "2602.04212"}})
check("英文 HTML 里库内引用带 ct in", "<span class='ct in' data-b='bib.bib12'>" in h and h.count("<span class='ct") == 1, h)

# 中文：作者姓 + 年份；右括号一起包进来；数字引用只认方括号里的
zh = "先前的工作 Lepori 等人 (2026) 发现了惰性表示。"
zm = _zh_cite_marks(zh, ["bib.bib12"], bib)
check("中文找到 Lepori 等人 (2026)", [zh[a:b] for a, b, _ in zm] == ["Lepori 等人 (2026)"], zm)
check("中文 HTML 包上引用", "<span class='ct' data-b='bib.bib12'>Lepori 等人 (2026)</span>" in _zh_html(zh, {}, zm, {}),
      _zh_html(zh, {}, zm, {}))
nb = {"bib.bib8": {"tag": "[8]"}}
zm = _zh_cite_marks("在 8 个模型上 [3, 8] 都成立", ["bib.bib8"], nb)
check("数字引用只匹配方括号里的 8", [m[:2] for m in zm] == [(13, 14)], zm)

# 知识库：文件名里的编号、阅读顺序位置、清单标题、译文、卡片；按标题对上没有 arXiv 号的 ACL 版
with tempfile.TemporaryDirectory() as root:
    def put(rel, text=""):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(text)
    put("pdfs/cvpr4/10-negative/2602.04212_StruggleUseInContextRepr.pdf")
    put("pdfs/cvpr4/按优先级/1-精读-反面/01_2602.04212_StruggleUseInContextRepr.pdf")
    put("pdfs/cvpr4/INDEX.md", "| [2602.04212](https://arxiv.org/abs/2602.04212) | Language Models Struggle to Use "
        "Representations Learned In-Context | Lepori | 2026 | `papers/x.pdf` |\n")
    put("pdfs/cvpr4/07/2501.00070_ICLR.pdf")
    put("translations/2602.04212-zh.html")
    put("papers/2026-lepori.md", '---\ntitle: "Language Models Struggle to Use Representations Learned In-Context"\n'
        'url: "https://arxiv.org/abs/2602.04212"\n---\n')
    lib = load_library(root)
    e = lib["papers"]["2602.04212"]
    check("库：阅读顺序位置排在前面", e["where"][0] == "cvpr4 · 精读-反面 #1", e)
    check("库：PDF 用分类文件夹里的原件", e["pdf"] == "pdfs/cvpr4/10-negative/2602.04212_StruggleUseInContextRepr.pdf", e)
    check("库：译文和卡片", e["translation"] == "translations/2602.04212-zh.html" and e["card"] == "papers/2026-lepori.md", e)
    r = Resolver(lib, lookup=False).resolve(bib, "9999.00001")
    check("ACL 版 Lepori 按标题对上库里的 2602.04212，并补上编号",
          r["bib.bib12"] == {"arxiv": "2602.04212", "lib": "2602.04212"}, r)
    check("自带 arXiv 号的 Park 对上库里的 2501.00070", r["bib.bib3"]["lib"] == "2501.00070", r)
    check("不在库里、不联网时 Wu 留空", r["bib.bib7"] == {"arxiv": "", "lib": None}, r)

# 引用统计：两篇来源论文引用同一篇（一篇带编号、一篇是会议版没编号）算同一篇、被引 2；通用论文挪出主表
from iuno_reader.citations import load_generic, report_md, collect  # noqa: E402

BIB = """<html><body><section class="ltx_bibliography"><ul>
<li class="ltx_bibitem" id="b1"><span class="ltx_bibblock">K. Li. 2023.</span>
<span class="ltx_bibblock">Emergent world representations: exploring a sequence model trained on a synthetic task.</span>{extra}</li>
<li class="ltx_bibitem" id="b2"><span class="ltx_bibblock">A. Vaswani. 2017.</span>
<span class="ltx_bibblock">Attention is all you need.</span></li>
</ul></section></body></html>"""
with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as kb:
    os.makedirs(os.path.join(root, "cache"))
    open(os.path.join(root, "cache", "1111.11111.html"), "w").write(
        BIB.format(extra="<span class='ltx_bibblock'>arXiv:2210.13382</span>"))
    open(os.path.join(root, "cache", "2222.22222.html"), "w").write(BIB.format(extra="<span class='ltx_bibblock'>ICLR</span>"))
    for rel in ["pdfs/cvpr4/a/1111.11111_A.pdf", "pdfs/cvpr4/a/2222.22222_B.pdf", "pdfs/cvpr6/a/3333.33333_C.pdf"]:
        os.makedirs(os.path.dirname(os.path.join(kb, rel)), exist_ok=True)
        open(os.path.join(kb, rel), "w").close()
    os.makedirs(os.path.join(kb, "citations"))
    open(os.path.join(kb, "citations", "通用论文.md"), "w", encoding="utf-8").write(
        "# 名单\n- 1706.03762 · Attention Is All You Need（Transformer）\n- 只写标题的 Some Generic Paper Title\n")
    lib = load_library(kb)
    src, st = collect(root, lib, "cvpr4")
    check("统计：只收 cvpr4 里抓过网页的两篇", sorted(src) == ["1111.11111", "2222.22222"], src)
    check("统计：有编号和没编号的同一篇合并，被引 2", len(st.get("2210.13382", {}).get("by", [])) == 2, st.keys())
    g = load_generic(kb)
    check("通用名单：编号和只写标题的都读到", "1706.03762" in g[0] and "somegenericpapertitle" in g[1], g)
    md = report_md(lib, src, st, "cvpr4", generic=({"1706.03762"}, {"attentionisallyouneed"}))
    main, _, tail = md.partition("[!note]- 通用论文")
    check("通用论文不进主表、在折叠框里", "Attention is all you need" not in main and "Attention is all you need" in tail, md)

# 本地词典：CSV 建库、查词、变形找原形、原形词条一起带上
from iuno_reader import dictionary  # noqa: E402
with tempfile.TemporaryDirectory() as root:
    os.makedirs(os.path.join(root, "dict"))
    open(os.path.join(root, "dict", "ecdict.csv"), "w", encoding="utf-8").write(
        "word,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange,detail,audio\n"
        'elicit,i\'lisit,v derive by reason,"vt. 引出, 推导出\\n[网络] 诱发",,1,,cet6 gre,0,0,,,\n'
        'elicits,,pr. & vb. n. of Elicit,"v. 引出( elicit的第三人称单数 )",,,,,0,0,0:elicit,,\n'
        'Inert,,,[化] 惰性,,,,,0,0,,,\n'
        'inert,i\'nә:t,s. unable to move,"a. 惰性的",,1,,toefl,0,0,,,\n')
    db = dictionary.open_db(root)
    r = dictionary.lookup(db, ["elicits", "inert", "nosuchword", "the$"])
    check("词典：变形词带原形、英文释义用原形的", r["elicits"].get("l") == "elicit" and r["elicits"].get("d") == "derive by reason", r)
    check("词典：原形词条一起带上，[网络] 释义去掉", r.get("elicit", {}).get("t") == "vt. 引出, 推导出", r)
    check("词典：大小写都有时取小写那条", r["inert"]["t"] == "a. 惰性的" and r["inert"]["g"] == "托福 柯林斯★", r)
    check("词典：查不到、不像单词的都不出现", "nosuchword" not in r and "the$" not in r, r)

sys.exit(0 if ok else 1)
