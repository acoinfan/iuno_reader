"""命令行入口：python -m iuno_reader <命令> <arXiv 编号> [选项]

  fetch      下载 arXiv HTML 到 cache/
  extract    拆成片段 → data/<id>/doc.json
  tasks      导出待译清单（给人或 Agent 翻译）→ data/<id>/tasks.json
  translate  调用 OpenAI 兼容接口自动翻译 → 合并进 data/<id>/translation.json
  render     核对并渲染 → out/<id>.md、out/<id>.html、out/<id>.checked.json
  import-kb-glossary <glossary.md>  从 Research-KB 导入术语表 → glossary/terms.tsv
"""
import argparse
import json
import os
import sys
import urllib.request

from . import extract as ex
from .check import check_doc
from .glossary import import_kb_glossary, load_hedges, load_terms, write_terms
from .render import render_html, render_markdown
from .translate import OpenAICompatible, make_tasks, select_segments

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def p(*parts):
    path = os.path.join(ROOT, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def cmd_fetch(a):
    out = p("cache", f"{a.id}.html")
    if os.path.exists(out) and not a.force:
        print("已有缓存", out)
        return
    url = f"https://arxiv.org/html/{a.id}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "iuno_reader"}), timeout=60) as r:
            data = r.read()
    except Exception as e:
        sys.exit(f"下载失败：{url}（{e}）。这篇可能没有 HTML 版，暂不支持 TeX 源码兜底。")
    if b"ltx_document" not in data:
        sys.exit("下载到的页面不是 LaTeXML 生成的论文 HTML，可能这篇没有 HTML 版。")
    open(out, "wb").write(data)
    print("已下载", out, len(data), "字节")


def cmd_extract(a):
    html = open(p("cache", f"{a.id}.html"), encoding="utf-8").read()
    doc = ex.extract(html, a.id)
    save_json(doc, p("data", a.id, "doc.json"))
    kinds = {}
    for s in doc["segments"]:
        kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    print("片段", len(doc["segments"]), kinds)


def _sections(a):
    return [x.strip() for x in a.sections.split(",")] if getattr(a, "sections", None) else None


def cmd_tasks(a):
    doc = load_json(p("data", a.id, "doc.json"))
    tasks = make_tasks(doc, load_terms(), load_hedges(), _sections(a))
    save_json(tasks, p("data", a.id, "tasks.json"))
    print("待译片段", len(tasks), "句数", sum(len(t["sentences"]) for t in tasks))


def cmd_translate(a):
    doc = load_json(p("data", a.id, "doc.json"))
    path = p("data", a.id, "translation.json")
    tr = load_json(path, {})
    tasks = make_tasks(doc, load_terms(), load_hedges(), _sections(a))
    eng = OpenAICompatible()
    for i, t in enumerate(tasks, 1):
        if t["id"] in tr and not a.force:
            continue
        tr[t["id"]] = eng.translate(t)
        save_json(tr, path)  # 每段存一次，中途失败不丢已完成的
        print(f"[{i}/{len(tasks)}] {t['id']}")


def cmd_render(a):
    doc = load_json(p("data", a.id, "doc.json"))
    tr = load_json(p("data", a.id, "translation.json"), {})
    sections_zh = tr.pop("__sections__", {})
    import glob
    reviews = [load_json(f) for f in sorted(glob.glob(p("data", a.id, "review*.json")))
               if not f.endswith("review_tasks.json")]
    doc, stats = check_doc(doc, tr, load_terms(), load_hedges(), reviews)
    only = None
    if _sections(a):
        only = {s["id"] for s in select_segments(doc, _sections(a))}
        only |= {s["id"] for s in doc["segments"] if s["kind"] == "equation" and s.get("section", "").split(" ")[0] in _sections(a)}
    if a.translated_only:
        done = {s["id"] for s in doc["segments"] if s.get("translated")}
        secs = {s["section"] for s in doc["segments"] if s["id"] in done}
        keep = done | {s["id"] for s in doc["segments"] if s["kind"] == "equation" and s["section"] in secs}
        only = keep if only is None else (only & keep)
    save_json(doc, p("out", f"{a.id}.checked.json"))
    open(p("out", f"{a.id}.md"), "w", encoding="utf-8").write(render_markdown(doc, stats, sections_zh, only))
    open(p("out", f"{a.id}.html"), "w", encoding="utf-8").write(render_html(doc, stats, sections_zh, only))
    print("统计", dict(stats))
    print("输出", p("out", f"{a.id}.md"), p("out", f"{a.id}.html"))


def cmd_review_tasks(a):
    from .review import make_review_tasks
    doc = load_json(p("data", a.id, "doc.json"))
    tr = load_json(p("data", a.id, "translation.json"), {})
    tr.pop("__sections__", None)
    tasks = make_review_tasks(doc, tr)
    save_json(tasks, p("data", a.id, "review_tasks.json"))
    print("待审片段", len(tasks), "句数", sum(len(t["pairs"]) for t in tasks))


def cmd_import_review(a):
    """导入人工或子代理写的审校结果：校验条数，记下审的是哪一版译文，存成 review.<name>.json。"""
    from .review import attach_reviewed_text
    tasks = load_json(p("data", a.id, "review_tasks.json"))
    rv = load_json(a.path)
    by_id = {t["id"]: t for t in tasks}
    bad = [sid for sid, s in rv.items() if sid not in by_id or len(s.get("reviews", [])) != len(by_id[sid]["pairs"])]
    if bad:
        sys.exit(f"条数或片段 id 对不上：{bad[:5]}")
    attach_reviewed_text(rv, tasks)
    out = p("data", a.id, f"review.{a.name}.json")
    save_json(rv, out)
    from collections import Counter
    c = Counter(r["verdict"] for s in rv.values() for r in s["reviews"])
    print("已导入", out, dict(c))


def cmd_review(a):
    from .review import ApiReviewer, make_review_tasks
    doc = load_json(p("data", a.id, "doc.json"))
    tr = load_json(p("data", a.id, "translation.json"), {})
    tr.pop("__sections__", None)
    path = p("data", a.id, f"review.{a.mode}.json")
    done = load_json(path, {})
    rv = ApiReviewer()
    tasks = make_review_tasks(doc, tr)
    for i, t in enumerate(tasks, 1):
        if t["id"] in done and not a.force:
            continue
        try:
            done[t["id"]] = rv.blind(t) if a.mode == "blind" else rv.backtranslate(t)
        except RuntimeError as e:
            print(f"[{i}/{len(tasks)}] {t['id']} 审校失败：{e}")
            continue
        save_json(done, path)
        print(f"[{i}/{len(tasks)}] {t['id']}")


def cmd_import(a):
    terms = load_terms()
    new = import_kb_glossary(a.path)
    terms.update(new)
    write_terms(terms, p("glossary", "terms.tsv"))
    print("术语表条目", len(terms))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="iuno_reader", description="arXiv 论文逐句双语对照与置信度标注")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in [("fetch", cmd_fetch), ("extract", cmd_extract), ("tasks", cmd_tasks),
                     ("translate", cmd_translate), ("render", cmd_render)]:
        s = sub.add_parser(name)
        s.add_argument("id")
        s.add_argument("--sections", help="只处理这些节，逗号分隔，例如 abs,2,3,4 或 A,B")
        s.add_argument("--force", action="store_true")
        s.add_argument("--translated-only", action="store_true", help="render 时只输出已翻译的片段")
        s.set_defaults(fn=fn)
    s = sub.add_parser("review-tasks", help="导出盲审清单（只含原文和译文）")
    s.add_argument("id")
    s.set_defaults(fn=cmd_review_tasks)
    s = sub.add_parser("import-review", help="导入人工或子代理写的审校结果")
    s.add_argument("id")
    s.add_argument("path")
    s.add_argument("--name", default="blind", help="审校者名字，存成 review.<name>.json")
    s.set_defaults(fn=cmd_import_review)
    s = sub.add_parser("review", help="用 OpenAI 兼容接口审校")
    s.add_argument("id")
    s.add_argument("--mode", choices=["blind", "backtranslate"], default="backtranslate")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_review)
    s = sub.add_parser("import-kb-glossary")
    s.add_argument("path")
    s.set_defaults(fn=cmd_import)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
