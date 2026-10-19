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


def cmd_run(a):
    from .pipeline import run
    secs = _sections(a)
    rc = 0
    for pid in a.ids:
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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="iuno_reader", description="arXiv 论文逐句双语对照与置信度标注")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in [("fetch", cmd_fetch), ("extract", cmd_extract), ("tasks", cmd_tasks),
                     ("translate", cmd_translate), ("render", cmd_render)]:
        s = sub.add_parser(name)
        s.add_argument("id")
        s.add_argument("--sections", help="只处理这些节，逗号分隔，例如 abs,2,3,4 或 A,B；body 表示全部正文（不含附录）")
        s.add_argument("--force", action="store_true")
        s.add_argument("--translated-only", action="store_true", help="render 时只输出已翻译的片段")
        s.set_defaults(fn=fn)
    s = sub.add_parser("run", help="流水线：下载→拆分→翻译→审校→核对渲染→输出 out/<id>/")
    s.add_argument("ids", nargs="+", help="一个或多个 arXiv 编号")
    s.add_argument("--sections", default="body",
                   help="只处理这些节，逗号分隔，例如 abs,2,3,4；默认 body（全部正文，不含附录）；all 表示含附录的全文")
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
    s.add_argument("--sections", default="body", help="只审这些节；默认 body（正文），all 表示全部已翻译的段落")
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
    s = sub.add_parser("import-kb-glossary")
    s.add_argument("path")
    s.set_defaults(fn=cmd_import)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
