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

from . import extract as ex
from .check import check_doc
from .glossary import import_kb_glossary, load_hedges, load_terms, write_terms
from .render import render_html, render_markdown
from .parallel import pmap
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
    from .pipeline import download_html
    try:
        data, url = download_html(a.id)
    except Exception as e:
        sys.exit(f"下载失败：{e}")
    open(out, "wb").write(data)
    print("已下载", url, "→", out, len(data), "字节")


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
    eng = OpenAICompatible(role="translator")
    meta = tr.setdefault("__meta__", {})
    old = meta.get("translator", "")
    if old and eng.name not in old.split(" + "):
        meta["translator"] = f"{old} + {eng.name}"  # 混合了不同译者，审校时据此判断独立性
    elif not old:
        meta["translator"] = eng.name
    for i, t in enumerate(tasks, 1):
        if t["id"] in tr and not a.force:
            continue
        tr[t["id"]] = eng.translate(t)
        tr[t["id"]]["translator"] = eng.name
        save_json(tr, path)  # 每段存一次，中途失败不丢已完成的
        print(f"[{i}/{len(tasks)}] {t['id']}")


def cmd_render(a):
    doc = load_json(p("data", a.id, "doc.json"))
    tr = load_json(p("data", a.id, "translation.json"), {})
    sections_zh = tr.pop("__sections__", {})
    tr.pop("__meta__", None)
    import glob
    reviews = [load_json(f) for f in sorted(glob.glob(p("data", a.id, "review.*.json")))
               if not f.endswith(".raw.json")]
    review_info = [r.get("__meta__", {}) for r in reviews]
    doc, stats = check_doc(doc, tr, load_terms(), load_hedges(), reviews)
    only = None
    if _sections(a):
        only = {s["id"] for s in select_segments(doc, _sections(a))}
        secs = {s["section"] for s in doc["segments"] if s["id"] in only and s["kind"] in ("para", "abstract", "item")}
        only |= {s["id"] for s in doc["segments"] if s["kind"] == "equation" and s.get("section") in secs}
    if a.translated_only:
        done = {s["id"] for s in doc["segments"] if s.get("translated")}
        secs = {s["section"] for s in doc["segments"] if s["id"] in done}
        keep = done | {s["id"] for s in doc["segments"] if s["kind"] == "equation" and s["section"] in secs}
        only = keep if only is None else (only & keep)
    # 和流水线用同一套输出结构：out/<id>/
    save_json(doc, p("out", a.id, "checked.json"))
    open(p("out", a.id, "paper.zh.md"), "w", encoding="utf-8").write(render_markdown(doc, stats, sections_zh, only, review_info))
    open(p("out", a.id, "paper.zh.html"), "w", encoding="utf-8").write(render_html(doc, stats, sections_zh, only, review_info))
    print("统计", dict(stats))
    print("输出", p("out", a.id, "paper.zh.md"), p("out", a.id, "paper.zh.html"), "（完整结果请用 run）")


def cmd_review_tasks(a):
    from .review import make_review_tasks
    doc = load_json(p("data", a.id, "doc.json"))
    tr = load_json(p("data", a.id, "translation.json"), {})
    tr.pop("__sections__", None)
    tr.pop("__meta__", None)
    tasks = make_review_tasks(doc, tr)
    save_json(tasks, p("data", a.id, "review_tasks.json"))
    print("待审片段", len(tasks), "句数", sum(len(t["pairs"]) for t in tasks))


def cmd_import_review(a):
    """导入人工或子代理写的审校结果：校验条数，记下审的是哪一版译文，存成 review.<name>.json。"""
    from .review import attach_reviewed_text
    from .roles import INDEPENDENCE_ZH, independence
    tasks = load_json(p("data", a.id, "review_tasks.json"))
    rv = load_json(a.path)
    rv.pop("__meta__", None)
    by_id = {t["id"]: t for t in tasks}
    bad = [sid for sid, s in rv.items() if sid not in by_id or len(s.get("reviews", [])) != len(by_id[sid]["pairs"])]
    if bad:
        sys.exit(f"条数或片段 id 对不上：{bad[:5]}")
    attach_reviewed_text(rv, tasks)
    translator = (load_json(p("data", a.id, "translation.json"), {}).get("__meta__") or {}).get("translator", "")
    ind = "same-model" if a.same_model else independence(translator, a.reviewer_model)
    rv["__meta__"] = {"reviewer": a.name, "mode": "blind", "models": {"reviewer": a.reviewer_model},
                      "translator": translator, "independence": ind}
    out = p("data", a.id, f"review.{a.name}.json")
    save_json(rv, out)
    from collections import Counter
    c = Counter(r["verdict"] for k, s in rv.items() if k != "__meta__" for r in s["reviews"])
    print("已导入", out, dict(c), "独立性：", INDEPENDENCE_ZH[ind])


def cmd_review(a):
    from .review import ApiReviewer, make_review_tasks
    from .roles import INDEPENDENCE_ZH, independence, worst_independence
    doc = load_json(p("data", a.id, "doc.json"))
    tr = load_json(p("data", a.id, "translation.json"), {})
    tr.pop("__sections__", None)
    translator = (tr.pop("__meta__", None) or {}).get("translator", "")
    path = p("data", a.id, f"review.{a.name or a.mode}.json")
    done = load_json(path, {})
    rv = ApiReviewer(a.mode)
    # 只要有一个审校角色和翻译用了同一个模型（或同一家的模型），就算独立性不足
    ind = worst_independence([independence(translator, m) for m in rv.models().values()])
    done["__meta__"] = {"reviewer": rv.name(), "mode": a.mode, "models": rv.models(),
                        "translator": translator, "independence": ind,
                        "usage": (done.get("__meta__") or {}).get("usage", {})}
    print("审校模型", rv.models(), "翻译者", translator or "未知", "独立性：", INDEPENDENCE_ZH[ind])
    tasks = make_review_tasks(doc, tr)
    if _sections(a) != ["all"]:  # 默认只审正文：附录哪怕翻过也不审，审校（尤其贵的模型）按段收费
        keep = {s["id"] for s in select_segments(doc, _sections(a))}
        tasks = [t for t in tasks if t["id"] in keep]
    todo = [t for t in tasks if a.force or t["id"] not in done]
    for n, (t, res, err, used) in enumerate(pmap(lambda r, t: r.review(t), todo, a.workers,
                                                 lambda: ApiReviewer(a.mode)), 1):
        usage = done["__meta__"].setdefault("usage", {})
        for k, v in used.items():
            usage[k] = round(usage.get(k, 0) + v, 1)
        if err:
            print(f"[{n}/{len(todo)}] {t['id']} 审校失败：{err}")
            continue
        done[t["id"]] = res
        save_json(done, path)  # 只在主线程写文件，每审完一段存一次
        print(f"[{n}/{len(todo)}] {t['id']}")


def cmd_find(a):
    from .pipeline import describe
    from .resolve import Finder, find_inputs
    finder = Finder(ROOT)
    items = [x for t in a.items for x in find_inputs(t)]
    rc = 0
    for it in items:
        r = finder.find(it)
        print(describe(r) + ("" if r.get("arxiv") else ("，可以用 PDF 翻译（准确度较低）" if r.get("oa_url") else "")))
        rc |= not r.get("arxiv")
    sys.exit(rc)


def cmd_kb(a):
    from . import kb
    from .library import library_root
    root = a.library or library_root()
    if not root:
        sys.exit("没有知识库目录：用 --library 指定，或在前端「设置」里填「知识库目录」")
    if a.action == "import":
        if not a.src or not a.project:
            sys.exit("用法：kb import <调研目录> --project <名字>")
        added, same, conflict = kb.import_survey(a.src, root, a.project, dry=a.dry_run)
        print(f"{'（试运行，没有复制）' if a.dry_run else ''}新增 {len(added)} 个，内容相同跳过 {same} 个，"
              f"同名但内容不同、没覆盖 {len(conflict)} 个")
        for x in added[:200]:
            print("  +", x)
        for x in conflict:
            print("  ! 没覆盖：", x)
        return
    if a.action in ("plan", "translate"):
        projects = [a.src] + (a.more or []) if a.src else None
        todo, skipped = kb.plan(root, projects, prepare_docs=not a.no_prepare, untiered=a.untiered,
                                only=set(a.only.split(",")) if a.only else None)
        print(kb.plan_md(todo, skipped))
        if a.action == "translate" and a.go and todo:
            kb.translate(todo, workers=a.workers, log=lambda s: print(s, flush=True))
        elif a.action == "translate" and todo:
            print("\n确认没问题后加 --go 开始翻译。")
        return
    if a.action == "sync":
        n = kb.sync(root, citations=not a.no_citations)
        print("已同步：" + "，".join(f"{k} {v}" for k, v in n.items()))


def cmd_run(a):
    from .pipeline import run, to_pids
    secs = _sections(a)
    ids, bad = to_pids(ROOT, a.ids)
    rc = 1 if bad else 0
    for pid in ids:
        try:
            m = run(ROOT, pid, secs, a.translate, a.review, a.export, a.force, a.workers, a.review_workers)
        except Exception as e:
            print(f"== {pid} 失败：{type(e).__name__}: {e}")
            rc = 1
            continue
        f = m["stages"]["render"]["flags"]
        print(f"== {pid}《{m['title'][:60]}》阶段：{m['stage']}")
        print(f"   翻译：{m['stages']['translate']['status']}；审校：{m['stages']['review']['status']}；"
              f"标记：红 {f['red']} 橙 {f['orange']} 黄 {f['yellow']} 蓝 {f['blue']} 无 {f['ok']}")
        for s in m["next_steps"]:
            print("   下一步：", s)
        print("   结果：", os.path.dirname(m["files"]["bilingual_md"]) + "/", ("→ 已导出到 " + m["exported_to"]) if m.get("exported_to") else "")
    sys.exit(rc)


def cmd_import(a):
    terms = load_terms()
    new = import_kb_glossary(a.path)
    terms.update(new)
    write_terms(terms, p("glossary", "terms.tsv"))
    print("术语表条目", len(terms))


def cmd_serve(a):
    from .server import serve
    serve(a.port, a.host)


def cmd_probe(a):
    from .probe import run
    run(a.roles.split(","))


def cmd_citations(a):
    from .citations import run
    from .library import library_root, load_library
    root = a.library or library_root()
    if not root:
        raise SystemExit("没有知识库目录：用 --library 指定，或在前端「设置」里填「知识库目录」")
    path, n_src, n_cited = run(ROOT, load_library(root), a.project, a.out, a.top, a.min)
    print(f"统计了 {n_src} 篇论文的参考文献，共 {n_cited} 篇不同的被引论文 → {path}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="iuno_reader", description="arXiv 论文逐句双语对照与置信度标注")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in [("fetch", cmd_fetch), ("extract", cmd_extract), ("tasks", cmd_tasks),
                     ("translate", cmd_translate), ("render", cmd_render)]:
        s = sub.add_parser(name)
        s.add_argument("id")
        s.add_argument("--sections", help="只处理这些节，逗号分隔，例如 abs,2,3,4 或 A,B；body 表示全部正文（不含附录）；skim 表示略读（摘要、引言、讨论和结论）")
        s.add_argument("--force", action="store_true")
        s.add_argument("--translated-only", action="store_true", help="render 时只输出已翻译的片段")
        s.set_defaults(fn=fn)
    s = sub.add_parser("run", help="流水线：下载→拆分→翻译→审校→核对渲染→输出 out/<id>/")
    s.add_argument("ids", nargs="+", help="一个或多个 arXiv 编号；也可以是 DOI、论文页面链接或标题（加引号），会先换成 arXiv 编号")
    s.add_argument("--sections", default="body",
                   help="只处理这些节，逗号分隔，例如 abs,2,3,4；默认 body（全部正文，不含附录）；skim 表示略读（摘要、引言、讨论和结论）；all 表示含附录的全文")
    s.add_argument("--translate", choices=["auto", "api", "agent"], default="auto",
                   help="auto：有接口就自动翻译，否则生成 agent 任务说明")
    s.add_argument("--review", choices=["auto", "blind", "backtranslate", "agent", "none"], default="auto")
    s.add_argument("--export", help="把结果文件夹复制到这个目录下的 <id>/")
    s.add_argument("--force", action="store_true", help="重新下载 HTML")
    s.add_argument("--workers", type=int, default=1, help="翻译时同时处理几段（按接口允许的并发数设）")
    s.add_argument("--review-workers", type=int, help="审校时同时审几段（默认同 --workers）")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("review-tasks", help="导出盲审清单（只含原文和译文）")
    s.add_argument("id")
    s.set_defaults(fn=cmd_review_tasks)
    s = sub.add_parser("import-review", help="导入人工或子代理写的审校结果")
    s.add_argument("id")
    s.add_argument("path")
    s.add_argument("--name", default="blind", help="审校者名字，存成 review.<name>.json")
    s.add_argument("--reviewer-model", default="", help="审校者用的模型，用来和翻译者比较独立性")
    s.add_argument("--same-model", action="store_true", help="明确标记审校者与翻译者是同一模型")
    s.set_defaults(fn=cmd_import_review)
    s = sub.add_parser("review", help="用 OpenAI 兼容接口审校")
    s.add_argument("id")
    s.add_argument("--mode", choices=["blind", "backtranslate"], default="backtranslate")
    s.add_argument("--name", help="结果存成 review.<name>.json（默认用 mode）；几个模型分别审同一份译文时用来区分")
    s.add_argument("--sections", default="body", help="只审这些节；默认 body（正文），skim 表示略读的几节，all 表示全部已翻译的段落")
    s.add_argument("--workers", type=int, default=1, help="同时审几段（按接口允许的并发数设，例如 Kimi 15 并发、3 篇同时跑就设 5）")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_review)
    s = sub.add_parser("probe", help="给每个配好接口的角色发一个小样例，检查接口能不能用、审校能不能抓错")
    s.add_argument("--roles", default="translator,reviewer,backtranslate",
                   help="逗号分隔：translator、reviewer（盲审）、backtranslate（回译 + 比对）")
    s.set_defaults(fn=cmd_probe)
    s = sub.add_parser("serve", help="网页前端：拖论文进来、选方案、看进度和花费")
    s.add_argument("--port", type=int, default=8780)
    s.add_argument("--host", default="127.0.0.1", help="默认只监听本机")
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("kb", help="调研 → 知识库 → 译文：import 导入调研、plan 列出没翻的和估价、translate 按档次翻译、sync 同步进知识库")
    s.add_argument("action", choices=["import", "plan", "translate", "sync"])
    s.add_argument("src", nargs="?", help="import：调研里放论文的目录；plan / translate：项目名（可以写几个）")
    s.add_argument("more", nargs="*", help="更多项目名")
    s.add_argument("--project", help="import：放到知识库 pdfs/ 下的哪个项目名，例如 cvpr7")
    s.add_argument("--library", help="知识库目录（默认用设置里的「知识库目录」）")
    s.add_argument("--dry-run", action="store_true", help="import：只列出要复制的，不复制")
    s.add_argument("--no-prepare", action="store_true", help="plan：没下载拆分过的论文不去下载（不估句数）")
    s.add_argument("--untiered", action="store_true", help="plan / translate：没分档（只在分类文件夹里）的论文也排进来，按略读翻")
    s.add_argument("--only", help="plan / translate：只看这几篇（编号，逗号分隔）")
    s.add_argument("--go", action="store_true", help="translate：真的开始翻译（会花钱）")
    s.add_argument("--workers", type=int, default=3, help="translate：几篇同时翻（默认 3）")
    s.add_argument("--no-citations", action="store_true", help="sync：不重算引用统计")
    s.set_defaults(fn=cmd_kb)
    s = sub.add_parser("find", help="只查不翻：把 DOI、论文页面链接或标题换成 arXiv 编号")
    s.add_argument("items", nargs="+", help="arXiv 编号或链接、DOI、doi.org 链接、ACL/OpenReview/NeurIPS 等论文页面链接、标题（标题要加引号）")
    s.set_defaults(fn=cmd_find)
    s = sub.add_parser("citations", help="引用统计：一次调研里各篇论文都在引用哪些论文、库内论文之间谁引用了谁")
    s.add_argument("--project", help="知识库 pdfs/ 下的项目名，例如 cvpr4；不写就统计整个知识库")
    s.add_argument("--library", help="知识库目录（默认用设置里的「知识库目录」）")
    s.add_argument("--out", help="输出目录（默认 out/_citations/）")
    s.add_argument("--top", type=int, default=40, help="每张表最多列几篇")
    s.add_argument("--min", type=int, default=2, help="不在库里的论文，被引至少几次才列出")
    s.set_defaults(fn=cmd_citations)
    s = sub.add_parser("import-kb-glossary")
    s.add_argument("path")
    s.set_defaults(fn=cmd_import)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
