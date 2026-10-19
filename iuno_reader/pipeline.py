"""流水线：给 arXiv 编号，自动下载 → 拆分 → 翻译 → 审校 → 核对渲染 → 输出固定结构的结果文件夹。

结果文件夹 out/<id>/：
  manifest.json   每一步的状态、统计、译者和审校者、文件路径、下一步该做什么（给 agent 读）
  paper.zh.md     双语对照（Obsidian）
  paper.zh.html   双语对照（浏览器，左右两栏）
  paper.en.md     英文全文，保留章节、图注、公式、脚注（给 agent 引用和整理）
  flags.md        所有被标记的句子：原文、译文、原因
  checked.json    完整的核对数据
  AGENT_TRANSLATE.md / AGENT_REVIEW.md  需要 agent 接手翻译或审校时生成的任务说明

翻译或审校没有接口时，流水线不会失败：它生成任务清单和说明，把状态写成 awaiting_*，
其他 agent 按说明完成后再运行一次 run，就会从中断的地方接着做。
"""
import datetime
import glob
import json
import os
import shutil
from collections import Counter

from . import extract as ex
from .check import LEVELS, check_doc
from .glossary import load_hedges, load_terms
from .parallel import pmap
from .render import ICON, render_html, render_markdown
from .roles import INDEPENDENCE_ZH, independence, load_prompt, model_config, worst_independence
from .translate import make_tasks

VERSION = "0.2"


class Paths:
    def __init__(self, root, pid):
        self.root, self.pid = root, pid
        self.cache = os.path.join(root, "cache", f"{pid}.html")
        self.data = os.path.join(root, "data", pid)
        self.out = os.path.join(root, "out", pid)
        for d in (os.path.dirname(self.cache), self.data, self.out):
            os.makedirs(d, exist_ok=True)

    def d(self, name):
        return os.path.join(self.data, name)

    def o(self, name):
        return os.path.join(self.out, name)


def _load(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def _write(text, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def fetch(P, force=False):
    import urllib.request
    if os.path.exists(P.cache) and not force:
        return {"status": "cached"}
    url = f"https://arxiv.org/html/{P.pid}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "iuno_reader"}), timeout=60) as r:
        data = r.read()
    if b"ltx_document" not in data:
        raise RuntimeError("下载到的页面不是 LaTeXML 生成的论文 HTML，可能这篇没有 HTML 版（暂不支持 TeX 兜底）")
    with open(P.cache, "wb") as f:
        f.write(data)
    return {"status": "downloaded", "bytes": len(data)}


def _has_api(role):
    return all(model_config(role))


def _translate_headings(P, doc, tr, tasks, terms):
    """补译论文标题和这次用到的各级节标题，存进 translation.json 的 __sections__（标题存在 __title__）。
    已经有译名的不再翻。复用翻译角色的提示词和条数校验，每 30 条一次请求。"""
    from .glossary import terms_in
    from .translate import OpenAICompatible
    secz = tr.setdefault("__sections__", {})
    chosen = {t["id"] for t in tasks}
    heads = []
    for s in doc["segments"]:
        if s["id"] in chosen:
            for h in (s.get("section"), s.get("subsection")):
                if h and h not in secz and h not in heads:
                    heads.append(h)
    items = ([doc["title"]] if doc.get("title") and "__title__" not in secz else []) + heads
    if not items:
        return
    eng = OpenAICompatible(role="translator")
    meta = tr.setdefault("__meta__", {})
    for i in range(0, len(items), 30):
        batch = items[i:i + 30]
        task = {"id": "__headings__", "paper": doc.get("title", ""), "subsection": "", "previous_paragraph": "",
                "section": "（这一组是论文标题和各节标题，每条单独译成简洁的中文标题；节编号如 3、3.1、A、IV 原样保留在开头）",
                "terms": {t: "/".join(terms[t]["zh"]) for t in terms_in(" ".join(batch), terms)}, "sentences": batch}
        for h, s in zip(batch, eng.translate(task)["sentences"]):
            if s.get("zh"):
                secz["__title__" if h == doc.get("title") and "__title__" not in secz else h] = s["zh"]
        meta["usage"] = _add_usage(meta.get("usage"), eng.take_usage())
        _save(tr, P.d("translation.json"))


def _add_usage(total, new):
    """累加接口用量（调用次数、输入输出 token、秒数），存进 __meta__.usage，断点续跑时接着加。"""
    total = dict(total or {})
    for k, v in (new or {}).items():
        total[k] = round(total.get(k, 0) + v, 1)
    return total


def _agent_translate_md(P, n_seg, n_sent):
    return f"""# 翻译任务：arXiv {P.pid}

你是这篇论文的译者。请严格按下面的「译者规则」逐句翻译。

- 输入：`{P.d('tasks.json')}`。列表里每项是一个片段：`id`、所在节、上一段原文（只用于理解，不要翻译）、命中的术语表条目、要翻译的英文句子 `sentences`。共 {n_seg} 段、{n_sent} 句。
- 输出：写入（或合并进）`{P.d('translation.json')}`，格式：

```json
{{"__meta__": {{"translator": "<你的模型名或名字>"}},
  "<片段 id>": {{"sentences": [{{"zh": "译文", "conf": "high|mid|low", "note": "不是 high 时写原因"}}]}}}}
```

  每段的句数必须和输入完全相同。已有的条目不要删除。
- 完成后运行：`cd {P.root} && .venv/bin/python -m iuno_reader run {P.pid}`，流水线会接着核对、审校、渲染。

## 译者规则

{load_prompt('translator')}
"""


def _agent_review_md(P, n_seg, n_sent, title):
    tmpl = open(os.path.join(P.root, "prompts", "agent_blind_review.md"), encoding="utf-8").read()
    body = tmpl.split("---", 1)[1].strip()
    body = (body.replace("{TASKS}", P.d("review_tasks.json")).replace("{OUTPUT}", P.d("review.agent.raw.json"))
            .replace("{N_SEG}", str(n_seg)).replace("{N_SENT}", str(n_sent)).replace("{PAPER}", f"{title}（arXiv {P.pid}）"))
    return (f"# 审校任务：arXiv {P.pid}\n\n"
            "> 重要：如果你和译者是同一个模型，或者读得到和译者相同的人设说明，你的审校只能算「同模型复看」。"
            f"导入时请如实写明你的模型：\n> `cd {P.root} && .venv/bin/python -m iuno_reader import-review {P.pid} "
            f"{P.d('review.agent.raw.json')} --name <你的名字> --reviewer-model <你的模型>`（同模型再加 `--same-model`），"
            f"然后运行 `.venv/bin/python -m iuno_reader run {P.pid}` 重新渲染。\n\n---\n\n" + body + "\n")


def paper_en_md(doc):
    """英文全文，保留结构，给 agent 引用和整理。"""
    out = [f"# {doc['title']}", "", f"arXiv: {doc['arxiv_id']} ｜ https://arxiv.org/abs/{doc['arxiv_id']}", ""]
    sec = sub = None
    for s in doc["segments"]:
        if s["section"] != sec:
            sec, sub = s["section"], None
            out += [f"## {sec}", ""]
        if s.get("subsection") and s["subsection"] != sub:
            sub = s["subsection"]
            out += [f"### {sub}", ""]
        k = s["kind"]
        if k == "equation":
            out += [f"$${s.get('math', '')}$$ {s.get('eqtag', '')}".rstrip(), ""]
        elif k == "caption":
            out += ([f"![]({s['image']})"] if s.get("image") else []) + [f"*{s['en']}*", ""]
        elif k == "footnote":
            out += [f"[^{s.get('footnote')}]: {s['en']}", ""]
        elif k == "table":
            out += [f"*{s['en']}*（表格内容见原文）", ""]
        else:
            out += [s["en"], ""]
    return "\n".join(out)


def flags_md(doc, pid):
    out = [f"# 需要核对的句子：arXiv {pid}", ""]
    n = 0
    for s in doc["segments"]:
        for f in s.get("seg_flags", []):
            out += [f"- {ICON[f['level']]} **{s['id']}**（整段）：{f['msg']}"]
            n += 1
        for i, p in enumerate(s.get("pairs", []), 1):
            if not p["level"]:
                continue
            n += 1
            reasons = "；".join(f"{ICON[f['level']]} {f['msg']}" for f in p["flags"])
            out += [f"## {ICON[p['level']]} {s['id']} 第 {i} 句", "", f"- 原因：{reasons}",
                    f"- 原文：{p['en']}", f"- 译文：{p['zh']}", ""]
    if n == 0:
        out.append("没有被标记的句子。（这不代表译文完全正确，见 manifest.json 里的审校情况。）")
    return "\n".join(out)


def run(root, pid, sections=None, translate="auto", review="auto", export=None, force_fetch=False, workers=1,
        review_workers=None):
    """workers：翻译时同时处理几段；review_workers：审校时同时审几段（默认同 workers）。按接口允许的并发数设。"""
    review_workers = review_workers or workers
    P = Paths(root, pid)
    st = {}
    st["fetch"] = fetch(P, force_fetch)

    doc = ex.extract(open(P.cache, encoding="utf-8").read(), pid)
    _save(doc, P.d("doc.json"))
    st["extract"] = {"status": "ok", "segments": len(doc["segments"]),
                     "sentences": sum(len(s["sentences"]) for s in doc["segments"])}

    terms, hedges = load_terms(), load_hedges()
    tasks = make_tasks(doc, terms, hedges, sections)
    tr = _load(P.d("translation.json"), {})
    missing = [t for t in tasks if t["id"] not in tr]
    n_sent = sum(len(t["sentences"]) for t in tasks)
    if missing and translate in ("auto", "api") and _has_api("translator"):
        from .translate import OpenAICompatible
        eng = OpenAICompatible(role="translator")
        meta = tr.setdefault("__meta__", {})
        # 已有别人翻的段落时（例如先人工翻了几节，再用接口补译其余部分），两个译者都记下
        old = meta.get("translator", "")
        if old and eng.name not in old.split(" + "):
            meta["translator"] = f"{old} + {eng.name}"
        elif not old:
            meta["translator"] = eng.name
        for t, res, _, used in pmap(lambda e, t: e.translate(t), missing, workers,
                                    lambda: OpenAICompatible(role="translator")):
            tr[t["id"]] = res
            tr[t["id"]]["translator"] = eng.name
            meta["usage"] = _add_usage(meta.get("usage"), used)
            _save(tr, P.d("translation.json"))
        missing = []
    if missing:
        _save(missing, P.d("tasks.json"))
        _write(_agent_translate_md(P, len(missing), sum(len(t["sentences"]) for t in missing)), P.o("AGENT_TRANSLATE.md"))
        st["translate"] = {"status": "awaiting_translation", "missing_segments": len(missing),
                           "task_file": P.d("tasks.json"), "instructions": P.o("AGENT_TRANSLATE.md"),
                           "reason": "没有配置翻译接口" if translate != "agent" else "指定由 agent 翻译"}
    else:
        if os.path.exists(P.o("AGENT_TRANSLATE.md")):
            os.remove(P.o("AGENT_TRANSLATE.md"))
        st["translate"] = {"status": "done", "translator": (tr.get("__meta__") or {}).get("translator", "未知"),
                           "segments": len(tasks), "sentences": n_sent,
                           "usage": (tr.get("__meta__") or {}).get("usage")}

    if translate in ("auto", "api") and _has_api("translator"):
        _translate_headings(P, doc, tr, tasks, terms)

    # 审校：只在翻译完成后进行
    tr_clean = {k: v for k, v in tr.items() if not k.startswith("__")}
    translator = (tr.get("__meta__") or {}).get("translator", "")
    if st["translate"]["status"] == "done" and review != "none":
        from .review import make_review_tasks
        rtasks = [t for t in make_review_tasks(doc, tr_clean, terms) if t["id"] in {x["id"] for x in tasks}]
        _save(rtasks, P.d("review_tasks.json"))
        existing = [f for f in glob.glob(P.d("review.*.json")) if not f.endswith(".raw.json")]
        mode = review if review in ("blind", "backtranslate") else None
        if mode is None and review == "auto":
            mode = "backtranslate" if _has_api("backtranslator") else ("blind" if _has_api("reviewer") else None)
        if mode and _has_api("reviewer" if mode == "blind" else "backtranslator"):
            from .review import ApiReviewer
            rv = ApiReviewer(mode)
            path = P.d(f"review.{mode}.json")
            done = _load(path, {})
            ind = worst_independence([independence(translator, m) for m in rv.models().values()])
            done["__meta__"] = {"reviewer": rv.name(), "mode": mode, "models": rv.models(),
                                "translator": translator, "independence": ind}
            done["__meta__"]["usage"] = (_load(path, {}).get("__meta__") or {}).get("usage")
            todo = [t for t in rtasks if t["id"] not in done]
            for t, res, err, used in pmap(lambda r, t: r.review(t), todo, review_workers, lambda: ApiReviewer(mode)):
                if err:
                    print(f"审校失败 {t['id']}：{err}")
                else:
                    done[t["id"]] = res
                done["__meta__"]["usage"] = _add_usage(done["__meta__"]["usage"], used)
                _save(done, path)
            existing = sorted(set(existing) | {path})
        elif not existing or review == "agent":
            _write(_agent_review_md(P, len(rtasks), sum(len(t["pairs"]) for t in rtasks), doc["title"]),
                   P.o("AGENT_REVIEW.md"))
        if existing and review != "agent" and os.path.exists(P.o("AGENT_REVIEW.md")):
            os.remove(P.o("AGENT_REVIEW.md"))
        st["review"] = {"status": "done" if existing else "awaiting_review",
                        "reviewers": [(_load(f, {}).get("__meta__") or {}) for f in existing],
                        "instructions": P.o("AGENT_REVIEW.md") if os.path.exists(P.o("AGENT_REVIEW.md")) else None}
    else:
        st["review"] = {"status": "skipped" if review == "none" else "waiting_for_translation"}

    # 核对与渲染
    reviews = [_load(f) for f in sorted(glob.glob(P.d("review.*.json"))) if not f.endswith(".raw.json")]
    review_info = [r.get("__meta__", {}) for r in reviews]
    chosen = {t["id"] for t in tasks}
    # 只核对、统计这次选中的段落（例如只翻正文时，以前翻过的附录不算进句数和标记数）
    doc, stats = check_doc(doc, {k: v for k, v in tr_clean.items() if k in chosen}, terms, hedges, reviews)
    # 公式按所在节一起显示；只看正文片段所在的节，免得被引用的附录图把整节附录的公式带进来
    secs = {s["section"] for s in doc["segments"] if s["id"] in chosen and s["kind"] in ("para", "abstract", "item")}
    only = chosen | {s["id"] for s in doc["segments"] if s["kind"] == "equation" and s["section"] in secs}
    sections_zh = tr.get("__sections__", {})
    _save(doc, P.o("checked.json"))
    _write(render_markdown(doc, stats, sections_zh, only, review_info), P.o("paper.zh.md"))
    _write(render_html(doc, stats, sections_zh, only, review_info), P.o("paper.zh.html"))
    _write(paper_en_md(doc), P.o("paper.en.md"))
    _write(flags_md({"segments": [s for s in doc["segments"] if s["id"] in only]}, pid), P.o("flags.md"))
    counts = {lv: stats.get(lv, 0) for lv in LEVELS}
    counts["ok"] = stats.get("ok", 0)
    st["render"] = {"status": "ok", "flags": counts, "stale_reviews": stats.get("reviewed_stale", 0)}

    stage = ("awaiting_translation" if st["translate"]["status"] != "done"
             else "awaiting_review" if st["review"]["status"] == "awaiting_review" else "complete")
    next_steps = []
    if stage == "awaiting_translation":
        next_steps.append(f"按 {P.o('AGENT_TRANSLATE.md')} 翻译，然后重新运行 run {pid}")
    if stage == "awaiting_review":
        next_steps.append(f"按 {P.o('AGENT_REVIEW.md')} 做盲审，导入后重新运行 run {pid}")
    if any((r or {}).get("independence") in ("same-model", "same-vendor") for r in review_info):
        next_steps.append("现有审校与翻译是同一模型或同一家的模型，独立性不足；"
                          "配置另一家的模型后运行 run --review backtranslate，或请人抽查 flags.md")
    if counts["red"] or counts["orange"]:
        next_steps.append(f"人工看一下 flags.md 里的红色和橙色句子（{counts['red']} 红、{counts['orange']} 橙）")

    manifest = {
        "arxiv_id": pid, "title": doc["title"], "abs_url": f"https://arxiv.org/abs/{pid}",
        "html_url": f"https://arxiv.org/html/{pid}", "tool": f"iuno_reader {VERSION}",
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "sections_selected": sections or "all", "stage": stage, "stages": st,
        "review_summary": [f"{m.get('reviewer')}（{m.get('mode')}，{INDEPENDENCE_ZH.get(m.get('independence'), '?')}）"
                           for m in review_info if m],
        "files": {k: P.o(v) for k, v in {"bilingual_md": "paper.zh.md", "bilingual_html": "paper.zh.html",
                                          "english_md": "paper.en.md", "flags": "flags.md",
                                          "checked": "checked.json"}.items()},
        "next_steps": next_steps,
        "note": "颜色标记只是提醒：标出的句子要人看一眼，没标出的也不保证完全正确。",
    }
    _save(manifest, P.o("manifest.json"))

    if export:
        dst = os.path.join(export, pid)
        os.makedirs(dst, exist_ok=True)
        for f in os.listdir(P.out):
            shutil.copy2(P.o(f), os.path.join(dst, f))
        manifest["exported_to"] = dst
        _save(manifest, os.path.join(dst, "manifest.json"))
    return manifest
