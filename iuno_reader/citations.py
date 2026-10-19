"""引用统计：把一次调研里各篇论文的参考文献合在一起数。

「一次调研」= 知识库 pdfs/<项目>/ 下收的论文（例如 cvpr4）；不给项目时统计整个知识库，并按项目分列。
只统计已经抓过 arXiv 网页（cache/<编号>.html）的论文，参考文献从网页里读，不调用任何模型接口。

同一篇被引论文的认法：条目里的 arXiv 编号 → 标题（大小写、标点不计）；有编号的条目会把同标题、没编号的条目
（比如会议版）并过来。每篇来源论文只算一次，不管它在正文里引用了几处。

输出：
- 被引最多、但还不在知识库里的论文 —— 可能值得补读；
- 被引最多的库内论文，和它现在的阅读档次放在一起看 —— 档次也许要调；
- 库内论文之间谁引用了谁。
"""
import collections
import json
import os
import re

from . import extract as ex
from .library import Resolver, norm


def _bib_of(cache_html):
    from bs4 import BeautifulSoup
    with open(cache_html, encoding="utf-8") as f:
        return ex._bib_entries(BeautifulSoup(f.read(), "lxml"))


def _projects(e):
    """库内条目 → 它所在的项目（pdfs/<项目>/...）。"""
    out = []
    for w in e["where"]:
        p = w.split(" · ")[0]
        if p not in out:
            out.append(p)
    return out


def collect(root, lib, project=None):
    """→ (来源论文列表, {被引论文键: 统计})。root 是 iuno_reader 的数据根目录（含 cache/）。"""
    papers = lib["papers"]
    sources = [k for k, e in papers.items() if e["id"] and (project is None or project in _projects(e))
               and os.path.exists(os.path.join(root, "cache", f"{e['id']}.html"))]
    # 库里只有编号、没有标题的论文，按编号去 arXiv 补标题（有缓存；IUNO_ARXIV_LOOKUP=0 时不联网）
    res = Resolver(lib, cache_path=os.path.join(root, "cache", "arxiv_lookup.json"))
    entries = {}  # 来源 → 参考文献
    title_to_id = {}
    for s in sources:
        entries[s] = list(_bib_of(os.path.join(root, "cache", f"{s}.html")).values())
        for b in entries[s]:
            if b["arxiv"] and len(norm(b["title"])) >= 15:
                title_to_id.setdefault(norm(b["title"]), b["arxiv"])
    for k, e in papers.items():
        if e["id"] and e["title"]:
            title_to_id.setdefault(norm(e["title"]), e["id"])
    stats = {}
    for s in sources:
        seen = set()
        for b in entries[s]:
            nt = norm(b["title"])
            key = b["arxiv"] or title_to_id.get(nt) or (("t:" + nt) if len(nt) >= 15 else None)
            if not key or key in seen or key == s:
                continue
            seen.add(key)
            st = stats.setdefault(key, {"arxiv": key if not key.startswith("t:") else "", "titles": collections.Counter(),
                                        "years": collections.Counter(), "authors": "", "by": []})
            st["titles"][b["title"]] += 1
            if b["year"]:
                st["years"][b["year"]] += 1
            st["authors"] = st["authors"] or b["authors"]
            st["by"].append(s)
    for key, st in stats.items():
        st["title"] = st["titles"].most_common(1)[0][0] if st["titles"] else key
        st["year"] = st["years"].most_common(1)[0][0] if st["years"] else ""
        st["lib"] = res.in_library(st["arxiv"], st["title"], st["title"])
        del st["titles"], st["years"]
    return sources, stats


GENERIC_FILE = os.path.join("citations", "通用论文.md")


def load_generic(lib_root):
    """知识库 citations/通用论文.md：人人都引的通用论文（模型报告、优化器、Transformer 之类），不进主表。
    每行「- <arXiv 编号> · <标题>」，编号和标题写一个就行；# 开头的行和空行跳过。返回 (编号集合, 标题集合)。"""
    ids, titles = set(), set()
    try:
        lines = open(os.path.join(lib_root, GENERIC_FILE), encoding="utf-8").read().splitlines()
    except OSError:
        return ids, titles
    for line in lines:
        if not line.startswith("- "):
            continue
        body = line[2:]
        m = re.search(r"\b\d{4}\.\d{4,5}\b", body)
        if m:
            ids.add(m.group(0))
        t = re.sub(r"（[^）]*）|\([^)]*\)", "", body.split("·", 1)[-1] if "·" in body else body)
        t = re.sub(r"\b\d{4}\.\d{4,5}\b", "", t)
        if len(norm(t)) >= 8:
            titles.add(norm(t))
    return ids, titles


def _is_generic(st, generic):
    ids, titles = generic
    return bool(st["arxiv"] and st["arxiv"] in ids) or norm(st["title"]) in titles


def _short(e, k):
    return (e["title"] or k)[:60]


def _first_author(a):
    a = re.sub(r"\b(19|20)\d{2}[a-z]?\.?", "", a or "")  # bibtex 式作者块后面带着年份
    a = re.split(r",| and ", a)[0].strip(" .")
    return (a.split()[-1] if a else "") + (" 等" if a else "")


def report_md(lib, sources, stats, project=None, top=40, min_count=2, generic=(set(), set())):
    papers = lib["papers"]
    scope = f"调研 {project}" if project else "整个知识库"
    cmd = f"python -m iuno_reader citations{' --project ' + project if project else ''}"
    lines = ["---", "type: citation-stats", f"scope: \"{project or 'all'}\"", "tool: iuno_reader", "---", "",
             f"# 引用统计：{scope}", "",
             f"> 来源：{scope}里已经抓过 arXiv 网页的 {len(sources)} 篇论文，合计引用了 {len(stats)} 篇不同的论文。"
             f"「被引」= 被这 {len(sources)} 篇里的几篇引用（每篇只算一次）。",
             f"> 这是生成文件，重新运行 `{cmd}` 会整个覆盖；想法请写在卡片或笔记里。"
             f"[[{GENERIC_FILE[:-3]}|通用论文名单]]里的论文不进主表，放在文末折叠框里。", ""]
    multi = project is None  # 全库报告：按调研分列被引次数
    projs = sorted({p for s in sources for p in _projects(papers[s])}) if multi else []

    def by_list(st):
        return "、".join(_short(papers[s], s)[:28] for s in st["by"][:6]) + (" 等" if len(st["by"]) > 6 else "")

    def per_proj(st):
        c = collections.Counter(p for s in st["by"] for p in set(_projects(papers[s])) if p in projs)
        return " · ".join(f"{p} {c[p]}" for p in projs if c[p])

    def title_link(st):
        return f"[{st['title']}](https://arxiv.org/abs/{st['arxiv']})" if st["arxiv"] else st["title"]

    gen = {k for k, st in stats.items() if _is_generic(st, generic)}
    rank = sorted(stats.items(), key=lambda x: (-len(x[1]["by"]), x[1]["title"]))
    out_lib = [(k, st) for k, st in rank if not st["lib"] and k not in gen and len(st["by"]) >= min_count]
    lines += [f"## 被引最多、但不在知识库里（被引 ≥ {min_count}，前 {top} 篇）", "",
              "可能是这个方向绕不开的工作，值得看一眼要不要补进来。", "",
              "| 被引 | 论文 | 年份 | " + ("各调研 | " if multi else "") + "谁引用了它 |",
              "|---|---|---|" + ("---|" if multi else "") + "---|"]
    for k, st in out_lib[:top]:
        lines.append(f"| {len(st['by'])} | {title_link(st)}（{_first_author(st['authors'])}） | {st['year']} | "
                     + (f"{per_proj(st)} | " if multi else "") + f"{by_list(st)} |")
    if not out_lib:
        lines.append("| — | 没有 | | |")
    in_lib = [(k, st) for k, st in rank if st["lib"] and k not in gen]
    lines += ["", "## 被引的库内论文", "",
              "和它现在的阅读档次对照：被引多、档次却靠后的，也许该提前读。", "",
              "| 被引 | 论文 | 在库里的位置 | " + ("各调研 | " if multi else "") + "谁引用了它 |",
              "|---|---|---|" + ("---|" if multi else "") + "---|"]
    for k, st in in_lib[:top]:
        e = papers[st["lib"]]
        tr = f" · [[{e['translation'][:-5]}.md\\|译文]]" if e["translation"] else ""
        lines.append(f"| {len(st['by'])} | {e['title'] or st['title']}{tr} | {'；'.join(e['where'][:2])} | "
                     + (f"{per_proj(st)} | " if multi else "") + f"{by_list(st)} |")
    if multi and len(projs) >= 2:
        cross = [(k, st) for k, st in rank if k not in gen and len(per_proj(st).split(" · ")) >= 2]
        lines += ["", "## 跨调研共同引用", "",
                  "被不止一个调研里的论文引用。只在一两个调研里被集中引用的，是这些方向共同的基础；"
                  "在所有调研里都被大量引用的，多半是通用论文，可以考虑加进通用论文名单。", "",
                  "| 被引 | 论文 | 各调研 | 在库里 |", "|---|---|---|---|"]
        for k, st in cross[:top]:
            e = papers.get(st["lib"]) if st["lib"] else None
            lines.append(f"| {len(st['by'])} | {title_link(st)}（{_first_author(st['authors'])}） | {per_proj(st)} | "
                         f"{'；'.join(e['where'][:2]) if e else '—'} |")
    lines += ["", "## 库内论文之间的引用", "", "左边的论文在参考文献里引用了右边的论文。", ""]
    edges = collections.defaultdict(list)
    for k, st in in_lib:
        for s in st["by"]:
            edges[s].append(st["lib"])
    for s in sorted(edges, key=lambda s: -len(edges[s])):
        tgt = "；".join(f"{_short(papers[t], t)[:40]}（{papers[t]['where'][0] if papers[t]['where'] else ''}）"
                       for t in edges[s])
        lines.append(f"- **{_short(papers[s], s)}**（{papers[s]['where'][0] if papers[s]['where'] else ''}）→ {tgt}")
    gl = [(k, st) for k, st in rank if k in gen]
    if gl:
        lines += ["", f"> [!note]- 通用论文（在名单里，不进主表；{len(gl)} 篇）"]
        lines += [f"> - {len(st['by'])} 次 · {title_link(st)}" + (f"（{per_proj(st)}）" if multi else "") for k, st in gl]
    lines += ["", "## 统计了哪些论文", ""]
    for s in sources:
        e = papers[s]
        lines.append(f"- {s} {_short(e, s)}（{e['where'][0] if e['where'] else ''}，参考文献里引用了库内 {len(edges.get(s, []))} 篇）")
    return "\n".join(lines) + "\n"


def run(root, lib, project=None, out=None, top=40, min_count=2):
    """生成报告。out 不写时：知识库里有 citations/ 就写到那里（<项目>.md 或 全库.md），否则写到 out/_citations/。
    统计明细（JSON）总是写在 out/_citations/，不进知识库。"""
    sources, stats = collect(root, lib, project)
    md = report_md(lib, sources, stats, project, top, min_count, load_generic(lib["root"]))
    kb_dir = os.path.join(lib["root"], "citations")
    md_dir = out or (kb_dir if os.path.isdir(kb_dir) else os.path.join(root, "out", "_citations"))
    js_dir = os.path.join(root, "out", "_citations")
    for d in (md_dir, js_dir):
        os.makedirs(d, exist_ok=True)
    md_path = os.path.join(md_dir, f"{project or '全库'}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(js_dir, f"citations-{project or 'all'}.json"), "w", encoding="utf-8") as f:
        json.dump({"project": project, "sources": sources, "cited": stats}, f, ensure_ascii=False, indent=1)
    return md_path, len(sources), len(stats)
