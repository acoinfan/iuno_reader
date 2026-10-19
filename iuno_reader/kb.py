"""调研 → 知识库 → 译文，一条线累积下去（知识库按 research-KB 的结构，见 library.py）。

  kb import <调研目录> --project <名字>   把调研目录里的论文（PDF、网页）和 INDEX.md 补进知识库 pdfs/<名字>/，保留子目录；
                                           已有的同名文件内容一样就跳过，不一样不覆盖、列出来
  kb plan [项目…]                          每篇论文按阅读档次定方案，列出还没翻（或还缺 K3 审校）的，估句数和花费；
                                           要估句数会先下载、拆分（不调用模型，不花钱）
  kb translate [项目…] --go               照 plan 翻译（几篇同时跑）；不加 --go 等于 plan
  kb sync                                  译文拷进 translations/，所有 PDF 旁边写 .译文.md 跳转文件，重写总表 library/papers.tsv、
                                           library/README.md 和译文索引里的表格（translations/README.md 两个标记之间），更新引用统计

档次 → 方案：精读 →「尤诺推荐 · 精读」（正文 + Kimi K3 审校）；细读 →「尤诺推荐」（正文）；略读 →「尤诺推荐 · 略读」。
没分档的（只在分类文件夹里）默认不翻，plan / translate 加 --untiered 才按略读排进来。
一篇论文出现在几个位置时按最高的档次算。会议版 PDF（文件名里没有 arXiv 编号）按标题（清单里没有就读 PDF 第一页）
找到 arXiv 版本后并成一篇。
"""
import datetime
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import providers as pv
from .library import Resolver, _label, load_library, norm, pid_from_name
from .roles import ROOT

TIER_RANK = {"精读": 3, "细读": 2, "略读": 1, "": 0}
PRESET_BY_TIER = {"精读": "尤诺推荐 · 精读", "细读": "尤诺推荐", "略读": "尤诺推荐 · 略读", "": "尤诺推荐 · 略读"}
# 每百句的价格（元，DeepSeek 空闲时段，2026-10-19 实测：pro 关思考翻译 + pro 盲审约 0.55，Kimi K3 盲审约 2.0）；高峰时段 DeepSeek 翻倍
PRICE_100 = {"pro": 0.6, "k3": 2.0}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "site-packages", "__pycache__", ".obsidian", "text"}
MARK_BEGIN, MARK_END = "<!-- iuno_reader kb sync：从这里到下一个标记由工具生成，手改会被覆盖 -->", "<!-- iuno_reader kb sync 结束 -->"


# ---------- 导入调研 ----------

def import_survey(src, kb, project, dry=False):
    """→ (新增的相对路径, 内容相同跳过的个数, 同名但内容不同、没覆盖的)。INDEX.md 是调研那边生成的清单，以那边为准。"""
    dst_root = os.path.join(kb, "pdfs", project)
    added, same, conflict = [], 0, []
    for dirpath, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for fn in sorted(files):
            if not (fn.lower().endswith((".pdf", ".html", ".htm")) or fn == "INDEX.md"):
                continue
            s = os.path.join(dirpath, fn)
            rel = os.path.relpath(s, src)
            d = os.path.join(dst_root, rel)
            if os.path.exists(d):
                if filecmp.cmp(s, d, shallow=False):
                    same += 1
                elif fn == "INDEX.md":
                    if not dry:
                        shutil.copyfile(s, d)
                    added.append(rel + "（清单有更新）")
                else:
                    conflict.append(rel)
                continue
            if not dry:
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copyfile(s, d)
            added.append(rel)
    return added, same, conflict


# ---------- 总表 ----------

def _tier(label):
    m = re.search(r"(精读|细读|略读)", label)
    return m.group(1) if m else ""


def _order(label):
    m = re.search(r"#(\d+)$", label)
    return int(m.group(1)) if m else 999


def status(pid):
    """一篇论文在翻译工具里的状态：拆分了没有、翻了哪个范围、谁审的、红橙多少、来自网页还是 PDF。"""
    from .translate import select_segments
    d = os.path.join(ROOT, "data", pid)
    out = {"doc": False, "scope": "无", "source": "", "reviewers": [], "k3": False, "red": 0, "orange": 0,
           "sentences": {}, "missing": {}, "coverage": None}
    try:
        doc = json.load(open(os.path.join(d, "doc.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return out
    out["doc"], out["source"] = True, doc.get("source", "arxiv-html")
    out["coverage"] = (doc.get("pdf") or {}).get("coverage")
    try:
        tr = json.load(open(os.path.join(d, "translation.json"), encoding="utf-8"))
    except (OSError, ValueError):
        tr = {}
    done = {k for k, v in tr.items() if not k.startswith("__") and isinstance(v, dict) and v.get("sentences")}
    for scope in ("body", "skim"):
        segs = select_segments(doc, [scope])
        out["sentences"][scope] = sum(len(s["sentences"]) for s in segs)
        out["missing"][scope] = sum(len(s["sentences"]) for s in segs if s["id"] not in done)
    if done and not out["missing"]["body"]:
        out["scope"] = "正文"
    elif done and not out["missing"]["skim"]:
        out["scope"] = "略读"
    elif done:
        out["scope"] = "部分"
    for fn in sorted(os.listdir(d)):
        m = re.fullmatch(r"review\.([\w.-]+)\.json", fn)
        if m and not fn.endswith(".raw.json"):
            try:
                meta = json.load(open(os.path.join(d, fn), encoding="utf-8")).get("__meta__", {})
            except (OSError, ValueError):
                meta = {}
            name = meta.get("reviewer") or meta.get("model") or m.group(1)
            if name not in out["reviewers"]:
                out["reviewers"].append(name)
            out["k3"] = out["k3"] or "k3" in m.group(1).lower() or "k3" in name.lower()
    try:
        f = json.load(open(os.path.join(ROOT, "out", pid, "manifest.json"), encoding="utf-8"))["stages"]["render"]["flags"]
        out["red"], out["orange"] = f.get("red", 0), f.get("orange", 0)
    except (OSError, ValueError, KeyError):
        pass
    return out


def _file_title(path):
    if path.lower().endswith(".pdf"):
        try:
            from .pdfextract import pdf_title
        except ImportError:  # 没装 PyMuPDF
            return ""
        return pdf_title(path)
    try:
        t = re.search(r"<title[^>]*>(.*?)</title>", open(path, encoding="utf-8", errors="replace").read(200_000), re.I | re.S)
    except OSError:
        return ""
    return " ".join(t.group(1).split()) if t else ""


def registry(kb, lookup=True):
    """知识库里的每篇论文（同一篇的几个位置、会议版和 arXiv 版并成一条）→ [{key, arxiv, title, where, tier, files, card, status}]。"""
    lib = load_library(kb)
    files = {}  # 键 → 所有位置的文件（pdfs/ 下的相对路径）
    for dirpath, dirs, fns in os.walk(os.path.join(kb, "pdfs")):
        dirs.sort()
        for fn in sorted(fns):
            if fn.lower().endswith((".pdf", ".html", ".htm")):
                rel = os.path.relpath(os.path.join(dirpath, fn), kb).replace("\\", "/")
                files.setdefault(pid_from_name(fn), []).append(rel)
    res = Resolver(cache_path=os.path.join(ROOT, "cache", "arxiv_lookup.json"), lookup=lookup)
    papers = {}
    for key, e in lib["papers"].items():
        if key not in files and not e.get("translation"):
            continue  # 只有卡片或译文、没有 PDF 的不算收录
        arx = e.get("id") or ""
        if not arx and not e.get("title") and files.get(key):  # 清单里没写标题：从文件本身读
            e["title"] = _file_title(os.path.join(kb, files[key][0]))
        if not arx and e.get("title"):  # 会议版 PDF：按标题找 arXiv 版本
            arx = res.search_arxiv(e["title"]) or ""
        k = arx or key
        p = papers.setdefault(k, {"key": k, "arxiv": arx, "title": "", "where": [], "files": [], "card": "",
                                  "alias": []})
        p["title"] = p["title"] or e.get("title", "")
        p["where"] += [w for w in e.get("where", []) if w not in p["where"]]
        p["files"] += [f for f in files.get(key, []) if f not in p["files"]]
        p["card"] = p["card"] or e.get("card", "")
        if k != key:
            p["alias"].append(key)
    for p in papers.values():
        tiers = [_tier(w) for w in p["where"]]
        p["tier"] = max(tiers, key=lambda t: TIER_RANK[t]) if tiers else ""
        p["projects"] = sorted({f.split("/")[1] for f in p["files"] if f.count("/") >= 2})  # pdfs/ 根目录下散放的不算调研
        # 链接优先用阅读档次文件夹里的那份，其次分类文件夹，pdfs/ 根目录下散放的放最后
        p["files"].sort(key=lambda f: (0 if _tier(f) else 1 if f.count("/") >= 2 else 2, f))
        p["status"] = status(p["key"])
        if not p["title"] and p["status"]["doc"]:
            try:
                p["title"] = json.load(open(os.path.join(ROOT, "data", p["key"], "doc.json"), encoding="utf-8"))["title"]
            except (OSError, ValueError, KeyError):
                pass
    return sorted(papers.values(), key=lambda p: (p["projects"][:1], -TIER_RANK[p["tier"]], p["key"]))


# ---------- 计划与翻译 ----------

def _want(p):
    preset = pv.preset(PRESET_BY_TIER[p["tier"]])
    return preset, preset.get("scope", "body"), bool(preset.get("reviewer2"))


def plan(kb, projects=None, prepare_docs=True, untiered=False, only=None, log=print):
    """还要翻的论文 → [{key, title, tier, preset, scope, sentences, cost, note}]，以及翻不了的 [(key, 原因)]。
    默认只排分了档（精读、细读、略读）的论文；没分档的（只在分类文件夹里）要 untiered=True 才排进来，按略读翻。
    only：只看这几篇（编号；会议版写文件名编号或 arXiv 编号都行）。"""
    from .pipeline import add_pdf, prepare
    todo, skipped = [], []
    for p in registry(kb):
        if projects and not set(projects) & set(p["projects"]):
            continue
        if only and not ({p["key"]} | set(p["alias"])) & only:
            continue
        if not p["tier"] and not untiered:
            continue
        preset, scope, k3 = _want(p)
        st = p["status"]
        have = st["scope"] == "正文" or (scope == "skim" and st["scope"] == "略读")
        if have and (not k3 or st["k3"]):
            continue
        if not st["doc"] and prepare_docs:
            try:
                if not p["arxiv"]:
                    pdf = next((f for f in p["files"] if f.lower().endswith(".pdf")), "")
                    if not pdf:
                        skipped.append((p["key"], "只有网页文章（不是 arXiv 论文），暂时翻不了"))
                        continue
                    add_pdf(ROOT, os.path.join(kb, pdf), p["key"])
                prepare(ROOT, p["key"])
                st = p["status"] = status(p["key"])
            except Exception as e:  # 下载、拆分失败：记下原因，接着看下一篇
                skipped.append((p["key"], f"{type(e).__name__}: {str(e)[:200]}"))
                continue
        if not st["doc"]:
            skipped.append((p["key"], "还没下载拆分（plan 时加了 --no-prepare）"))
            continue
        n_tr = st["missing"][scope]
        n_k3 = st["sentences"][scope] if k3 and not st["k3"] else 0
        cost = n_tr / 100 * PRICE_100["pro"] + n_k3 / 100 * PRICE_100["k3"]
        note = "来自 PDF，准确度较低" if st["source"] == "pdf" else ""
        todo.append({"key": p["key"], "title": p["title"], "tier": p["tier"] or "未分档", "preset": preset["name"],
                     "scope": scope, "sentences": n_tr, "k3_sentences": n_k3, "cost": round(cost, 2), "note": note})
    return todo, skipped


def plan_md(todo, skipped):
    lines = ["| 编号 | 论文 | 档次 | 方案 | 待翻句数 | K3 审句数 | 估价（元） | 备注 |", "|---|---|---|---|---|---|---|---|"]
    for t in todo:
        lines.append(f"| {t['key']} | {t['title'][:60]} | {t['tier']} | {t['preset']} | {t['sentences']} | "
                     f"{t['k3_sentences'] or ''} | {t['cost']:.2f} | {t['note']} |")
    total = sum(t["cost"] for t in todo)
    peak = "（现在是 DeepSeek 高峰时段，实际约翻倍）" if pv.is_peak() else "（按 DeepSeek 空闲时段价）"
    lines += ["", f"共 {len(todo)} 篇，估价约 {total:.1f} 元{peak}。"]
    if skipped:
        lines += ["", "翻不了或没估的："] + [f"- {k}：{why}" for k, why in skipped]
    return "\n".join(lines)


def translate(todo, workers=3, log=print):
    """照计划翻译，几篇同时跑；每篇按方案依次跑翻译、审校（见 providers.preset_steps）。日志在 out/_kb/<编号>.log。"""
    logs = os.path.join(ROOT, "out", "_kb")
    os.makedirs(logs, exist_ok=True)
    lock, done = threading.Lock(), {"ok": 0, "bad": 0}

    def one(t):
        try:
            steps = pv.preset_steps(t["key"], pv.preset(t["preset"]))
        except ValueError as e:
            ok, why = False, str(e)
        else:
            ok, why = True, ""
            with open(os.path.join(logs, f"{t['key']}.log"), "a", encoding="utf-8") as f:
                for label, args, env in steps:
                    f.write(f"\n== {time.strftime('%m-%d %H:%M:%S')} {label}\n")
                    f.flush()
                    env2 = {k: v for k, v in os.environ.items() if not k.startswith("IUNO_")} | env | {"PYTHONUNBUFFERED": "1"}
                    rc = subprocess.run([sys.executable, "-m", "iuno_reader", *args], cwd=ROOT, env=env2,
                                        stdout=f, stderr=subprocess.STDOUT).returncode
                    if rc:
                        ok, why = False, f"{label}失败，见 out/_kb/{t['key']}.log"
                        break
        with lock:
            done["ok" if ok else "bad"] += 1
            log(f"{time.strftime('%H:%M')} {t['key']} {'完成' if ok else '失败：' + why}"
                f"（{done['ok'] + done['bad']}/{len(todo)}）")
        return ok

    with ThreadPoolExecutor(max(1, workers)) as ex:
        results = list(ex.map(one, todo))
    log(f"{time.strftime('%H:%M')} 全部结束：完成 {sum(results)}，失败 {len(results) - sum(results)}")
    return results


# ---------- 同步进知识库 ----------

def _copy_if_changed(src, dst):
    if os.path.exists(dst) and filecmp.cmp(src, dst, shallow=False):
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    return True


def _scope_text(st):
    return {"正文": "只翻了正文（附录和参考文献没翻）", "略读": "略读：只翻了摘要、引言、讨论和结论",
            "部分": "只翻了一部分"}.get(st["scope"], "")


def _jump_md(p, rel):
    st, key = p["status"], p["key"]
    depth = "../" * rel.count("/")
    fn = rel.rsplit("/", 1)[-1]
    head = f"{p['title']}（{'arXiv ' + p['arxiv'] if p['arxiv'] else key}）"
    if p["alias"] and not re.search(r"\d{4}\.\d{4,5}", fn):
        head += "，这份 PDF 是会议版，译文按 arXiv 版翻译"
    warn = "\n> ⚠ 这份译文来自 PDF，准确度低于网页版：段落和节标题是按版面猜的，引用前请对照 PDF 原文。\n" \
        if st["source"] == "pdf" else ""
    revs = "、".join(st["reviewers"]) or "无"
    return f"""# {p['title']}

{head}
{warn}
- 原文：[[{rel}|{fn}]]
- 中文对照（Obsidian 里读，每段英文下面是中文）：[[translations/{key}-zh.md|打开]]
- 网页版（浏览器打开，左英右中，可点词高亮、查词、点引用看是哪篇、点图看大图）：[打开]({depth}translations/{key}-zh.html)

{_scope_text(st)}。审校：{revs}；标红 {st['red']} 句、标橙 {st['orange']} 句，引用前对照原文。

> 这个文件由 iuno_reader 生成，放在 PDF 旁边方便找译文；所有译文的索引见 [[translations/README|译文索引]]，全部论文见 [[library/README|论文总表]]。
"""


def _row(p):
    st, key = p["status"], p["key"]
    src = "PDF ⚠" if st["source"] == "pdf" else "网页"
    pdf = p["files"][0] if p["files"] else ""
    links = f"[[{key}-zh.md\\|Markdown]] · [网页]({key}-zh.html)" + (f" · [[{pdf}\\|PDF]]" if pdf else "")
    return (f"| {p['title'] or key}（{key}） | {st['scope']} | {src} | {'；'.join(st['reviewers']) or '无'} | "
            f"{st['red']} / {st['orange']} | {links} |")


def index_tables(reg):
    """译文索引的表格：每个阅读档次一张表（「cvpr4 · 精读-正面」），没分档的按调研放一张。"""
    groups = {}
    for p in reg:
        if p["status"]["scope"] == "无":
            continue
        labels = sorted({re.sub(r"\s#\d+$", "", w) for w in p["where"] if _tier(w)}) or \
            [f"{proj} · 未分档" for proj in p["projects"][:1]]
        for lab in labels:
            order = min([_order(w) for w in p["where"] if w.startswith(lab)] or [999])
            groups.setdefault(lab, []).append((order, p))

    def gkey(lab):
        proj, _, rest = lab.partition(" · ")
        return (proj, -TIER_RANK[_tier(rest)], rest)
    out = []
    for lab in sorted(groups, key=gkey):
        out += [f"## {lab}", "", "| 论文 | 范围 | 来源 | 审校 | 红 / 橙 | 文件 |", "|---|---|---|---|---|---|"]
        out += [_row(p) for _, p in sorted(groups[lab], key=lambda x: (x[0], x[1]["key"]))]
        out.append("")
    return "\n".join(out)


def library_md(reg):
    by_proj = {}
    for p in reg:
        for proj in p["projects"] or ["（无）"]:
            by_proj.setdefault(proj, []).append(p)
    lines = ["# 论文总表", "",
             f"> 由 `iuno_reader kb sync` 生成（{datetime.date.today().isoformat()}），手改会被覆盖。"
             "逐篇明细见同目录的 `papers.tsv`（可以用表格软件打开）。", "",
             "| 调研 | 论文 | 正文已翻 | 略读已翻 | 来自 PDF | 未翻 |", "|---|---|---|---|---|---|"]
    for proj in sorted(by_proj):
        ps = by_proj[proj]
        c = lambda f: sum(1 for p in ps if f(p))  # noqa: E731
        lines.append(f"| {proj} | {len(ps)} | {c(lambda p: p['status']['scope'] == '正文')} | "
                     f"{c(lambda p: p['status']['scope'] == '略读')} | {c(lambda p: p['status']['source'] == 'pdf')} | "
                     f"{c(lambda p: p['status']['scope'] in ('无', '部分'))} |")
    lines += ["", "## 分了档、还没有译文的论文", ""]
    for p in reg:
        if p["tier"] and p["status"]["scope"] in ("无", "部分"):
            lines.append(f"- {p['title'] or p['key']}（{p['key']}）：{'；'.join(w for w in p['where'] if _tier(w))}")
    return "\n".join(lines) + "\n"


def papers_tsv(reg):
    cols = ["编号", "arXiv", "标题", "档次", "位置", "译文", "来源", "审校", "红", "橙", "卡片", "文件"]
    rows = ["\t".join(cols)]
    for p in reg:
        st = p["status"]
        vals = [p["key"], p["arxiv"], p["title"], p["tier"], "；".join(p["where"]), st["scope"],
                {"pdf": "PDF", "": ""}.get(st["source"], "网页"), "；".join(st["reviewers"]), str(st["red"]),
                str(st["orange"]), p["card"], "；".join(p["files"])]
        rows.append("\t".join(v.replace("\t", " ").replace("\n", " ") for v in vals))
    return "\n".join(rows) + "\n"


def _write_if_changed(path, text):
    if os.path.exists(path) and open(path, encoding="utf-8").read() == text:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True


def sync(kb, citations=True, log=print):
    """→ 改动统计。只写知识库里由工具生成的文件：translations/<编号>-zh.*、PDF 旁的 .译文.md、library/、
    translations/README.md 标记之间的表格、citations/ 下的报告。"""
    reg = registry(kb)
    n = {"译文": 0, "跳转文件": 0}
    for p in reg:
        if p["status"]["scope"] == "无":
            continue
        for ext in ("html", "md"):
            src = os.path.join(ROOT, "out", p["key"], f"paper.zh.{ext}")
            if os.path.exists(src) and _copy_if_changed(src, os.path.join(kb, "translations", f"{p['key']}-zh.{ext}")):
                n["译文"] += 1
        for rel in p["files"]:
            stem = os.path.splitext(rel)[0]
            if _write_if_changed(os.path.join(kb, stem + ".译文.md"), _jump_md(p, rel)):
                n["跳转文件"] += 1
    _write_if_changed(os.path.join(kb, "library", "papers.tsv"), papers_tsv(reg))
    _write_if_changed(os.path.join(kb, "library", "README.md"), library_md(reg))
    idx = os.path.join(kb, "translations", "README.md")
    old = open(idx, encoding="utf-8").read() if os.path.exists(idx) else "# 论文译文（iuno_reader 生成）\n"
    block = f"{MARK_BEGIN}\n\n{index_tables(reg)}\n{MARK_END}"
    if MARK_BEGIN in old and MARK_END in old:
        new = old[:old.index(MARK_BEGIN)] + block + old[old.index(MARK_END) + len(MARK_END):]
    else:
        new = old.rstrip() + "\n\n" + block + "\n"
    n["译文索引"] = int(_write_if_changed(idx, new))
    if citations:
        from .citations import run as cite_run
        lib = load_library(kb)
        projects = sorted({proj for p in reg for proj in p["projects"]})
        for proj in projects + [None]:
            try:
                cite_run(ROOT, lib, proj)
            except Exception as e:  # 某个调研统计失败不影响其他
                log(f"引用统计 {proj or '全库'} 失败：{type(e).__name__}: {e}")
        n["引用统计"] = len(projects) + 1
    n["论文"] = len(reg)
    return n
