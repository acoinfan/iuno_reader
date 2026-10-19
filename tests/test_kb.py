"""kb：导入调研、总表（几个位置并成一篇、按最高档次）、计划里的方案、译文索引表格、跳转文件、sync 只改标记之间。不联网。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from iuno_reader import kb  # noqa: E402

tmp = tempfile.mkdtemp()
survey, lib = os.path.join(tmp, "research", "papers"), os.path.join(tmp, "KB")


def touch(path, data=b"%PDF-1.4 x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


# 1. 导入：保留子目录；跳过 .venv；同名不同内容不覆盖；INDEX.md 以调研为准
touch(os.path.join(survey, "01-wm", "2602.04212_Struggle.pdf"))
touch(os.path.join(survey, "01-wm", "Li2026-WordToWorld-ACL.pdf"))
touch(os.path.join(survey, ".venv", "lib", "junk.pdf"))
touch(os.path.join(survey, "INDEX.md"), "| 编号 | 标题 |\n|---|---|\n| [2602.04212](x) | Struggle Paper Title |\n"
      "| Li2026-WordToWorld-ACL.pdf | From Word to World Long Title |\n".encode())
added, same, conflict = kb.import_survey(survey, lib, "cvpr9")
assert sorted(added) == ["01-wm/2602.04212_Struggle.pdf", "01-wm/Li2026-WordToWorld-ACL.pdf", "INDEX.md"], added
touch(os.path.join(survey, "01-wm", "2602.04212_Struggle.pdf"), b"%PDF-1.4 changed")
added, same, conflict = kb.import_survey(survey, lib, "cvpr9")
assert added == [] and same == 2 and conflict == ["01-wm/2602.04212_Struggle.pdf"], (added, same, conflict)
print("导入 通过")

# 2. 总表：阅读顺序文件夹里的同一篇并成一条，档次取最高；会议版按标题对上 arXiv 版
touch(os.path.join(lib, "pdfs", "cvpr9", "按优先级", "1-精读-正面", "01_2602.04212_Struggle.pdf"))
touch(os.path.join(lib, "pdfs", "cvpr9", "按优先级", "3-略读-反面", "02_Li2026-WordToWorld-ACL.pdf"))
touch(os.path.join(lib, "pdfs", "2602.04212_Struggle.pdf"))  # pdfs/ 根目录下散放的副本：算这篇的文件，但不算一个调研
fake = {"2602.04212": {"doc": True, "scope": "正文", "source": "arxiv-html", "reviewers": ["deepseek-v4-pro"], "k3": False,
                       "red": 3, "orange": 1, "sentences": {"body": 200, "skim": 60}, "missing": {"body": 0, "skim": 0},
                       "coverage": None},
        "2512.18832": {"doc": True, "scope": "无", "source": "pdf", "reviewers": [], "k3": False, "red": 0, "orange": 0,
                       "sentences": {"body": 300, "skim": 90}, "missing": {"body": 300, "skim": 90}, "coverage": 0.97}}
kb.status = lambda pid: fake.get(pid, dict(fake["2512.18832"], source=""))
kb.Resolver.search_arxiv = lambda self, t: "2512.18832" if "Word to World" in t else ""
reg = {p["key"]: p for p in kb.registry(lib, lookup=False)}
assert set(reg) == {"2602.04212", "2512.18832"}, reg.keys()
s = reg["2602.04212"]
assert s["tier"] == "精读" and len(s["files"]) == 3 and s["title"] == "Struggle Paper Title", s
assert s["projects"] == ["cvpr9"], s["projects"]
w = reg["2512.18832"]
assert w["alias"] == ["Li2026-WordToWorld-ACL"] and w["tier"] == "略读" and w["arxiv"] == "2512.18832", w
print("总表合并与档次 通过")

# 3. 计划：精读缺 K3 → 只估 K3；略读 → skim 范围
kb.pv.preset = lambda name: {"尤诺推荐 · 精读": {"name": name, "scope": "body", "reviewer2": {"model": "kimi-k3"}},
                             "尤诺推荐 · 略读": {"name": name, "scope": "skim"}}.get(name, {"name": name, "scope": "body"})
todo, skipped = kb.plan(lib, prepare_docs=False)
t = {x["key"]: x for x in todo}
assert t["2602.04212"]["sentences"] == 0 and t["2602.04212"]["k3_sentences"] == 200, t
assert t["2512.18832"]["scope"] == "skim" and t["2512.18832"]["sentences"] == 90 and "PDF" in t["2512.18832"]["note"], t
print("计划 通过")

# 4. 索引表格和跳转文件
tables = kb.index_tables(list(reg.values()))
assert "## cvpr9 · 精读-正面" in tables and "Struggle Paper Title（2602.04212）" in tables and "2512.18832" not in tables, tables
jump = kb._jump_md(w, "pdfs/cvpr9/按优先级/3-略读-反面/02_Li2026-WordToWorld-ACL.pdf")
assert "会议版" in jump and "准确度低于网页版" in jump and "../../../../translations/2512.18832-zh.html" in jump, jump
print("索引表格和跳转文件 通过")

# 5. sync：只改标记之间，标记外的手写内容保留
os.makedirs(os.path.join(lib, "translations"))
with open(os.path.join(lib, "translations", "README.md"), "w", encoding="utf-8") as f:
    f.write(f"# 译文\n\n手写的说明\n\n{kb.MARK_BEGIN}\n旧表格\n{kb.MARK_END}\n\n## 我的笔记\n")
kb.ROOT = tmp  # out/ 里没有译文文件：不拷贝，只写索引、总表、跳转文件
n = kb.sync(lib, citations=False)
idx = open(os.path.join(lib, "translations", "README.md"), encoding="utf-8").read()
assert "手写的说明" in idx and "## 我的笔记" in idx and "旧表格" not in idx and "cvpr9 · 精读-正面" in idx, idx
assert os.path.exists(os.path.join(lib, "pdfs", "cvpr9", "按优先级", "1-精读-正面", "01_2602.04212_Struggle.译文.md"))
assert not os.path.exists(os.path.join(lib, "pdfs", "cvpr9", "01-wm", "Li2026-WordToWorld-ACL.译文.md"))  # 没译文不写
tsv = open(os.path.join(lib, "library", "papers.tsv"), encoding="utf-8").read().splitlines()
assert tsv[0].startswith("编号\tarXiv") and len(tsv) == 3, tsv
print("sync 通过：", n)
print("kb 测试全部通过")
