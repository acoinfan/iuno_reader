"""把核对后的 doc 渲染成 Obsidian Markdown 和左右两栏 HTML。

阅读体验上的约定：
- 图、表、脚注跟着正文走：放到正文第一次提到它的那一段后面（找不到引用的保持原位）。
- 不用卡片和边框，段落之间只用留白分隔；片段编号不显示。
- 有问题的句子用浅色底色标出；网页里鼠标悬停时左右对应句子一起铺浅绿底色，点击有底色的句子展开说明。
- 标题：网页里英文、中文左右两栏；Obsidian 里标题用中文，英文原标题小字放在下面。
- 字体：英文用 Computer Modern（CMU Serif，论文常用），中文用思源宋体 / 宋体。
"""
import datetime
import html
import json
import os
import re
import urllib.parse

from .check import LEVELS

COLOR = {"red": "#f7d3d3", "orange": "#f8dcbf", "yellow": "#f8f0c4", "blue": "#d9e8f7"}
_DOT = {"red": "#d9534f", "orange": "#e08a2c", "yellow": "#c9a200", "blue": "#4a86c5"}  # 句末小圆点
ICON = {"red": "🔴", "orange": "🟠", "yellow": "🟡", "blue": "🔵"}
LABEL = {"red": "可能丢了信息", "orange": "翻译没把握或可能漏译", "yellow": "需要留意", "blue": "术语与术语表不一致"}

def _fn(s):
    return re.sub(r"\{fn(\d+)\}", r"<sup>\1</sup>", s)


def _attr(s):
    return html.escape(s, quote=True).replace("\n", " ")


def reading_order(segments, only=None):
    """返回按阅读顺序排列的片段：图、表、脚注挪到正文第一次提到它的段落后面。"""
    from .extract import FLOAT_KINDS, float_ref_pattern
    segs = [s for s in segments if only is None or s["id"] in only]
    main = [s for s in segs if s["kind"] not in FLOAT_KINDS]
    anchor = {}
    for f in segs:
        if f["kind"] not in FLOAT_KINDS:
            continue
        pat = float_ref_pattern(f)
        if not pat:
            continue
        for s in main:
            if pat.search(s["en"]):
                anchor[f["id"]] = s["id"]
                break
    attached = {}
    for f in segs:
        if f["id"] in anchor:
            attached.setdefault(anchor[f["id"]], []).append(f)
    out = []
    for s in segs:
        if s["id"] in anchor:
            continue  # 会在它的锚点段落后面输出
        out.append(s)
        for f in attached.get(s["id"], []):
            out.append(dict(f, section=s["section"], subsection=s.get("subsection", "")))
    return out


def _review_line(stats, review_info=None):
    from .roles import INDEPENDENCE_ZH
    names = {"blind": "盲审", "backtranslate": "回译比对"}
    parts = [f"{names.get(k[9:], k[9:])} {v} 句" for k, v in stats.items()
             if k.startswith("reviewed_") and k != "reviewed_stale"]
    if not parts:
        return "还没有经过独立审校：没有标记的句子只表示规则没发现问题。"
    line = "已审校：" + "，".join(parts)
    if stats.get("reviewed_stale"):
        line += f"；另有 {stats['reviewed_stale']} 句在审校后改过译文，旧意见已作废"
    who = []
    for m in review_info or []:
        if not m:
            continue
        ind = INDEPENDENCE_ZH.get(m.get("independence", "unknown"), "独立性未知")
        who.append(f"{m.get('reviewer', '?')}（{names.get(m.get('mode'), m.get('mode'))}，{ind}）")
    if who:
        line += "。审校者：" + "；".join(who)
    return line + "。"


def _counts(stats):
    s = {k: v for k, v in stats.items() if not k.startswith("reviewed_")}
    return s, sum(s.values())


# ---------------------------------------------------------------- Markdown（Obsidian）

def _zh_md(pairs):
    out = []
    for p in pairs:
        zh = _fn(p["zh"] or "（缺译文）")
        if p["level"]:
            tip = "；".join(f"{ICON[f['level']]} {f['msg']}" for f in p["flags"])
            zh = f'<mark style="background:{COLOR[p["level"]]};color:inherit" title="{_attr(tip)}">{zh}</mark>'
        out.append(zh)
    return "".join(out)


def _flags_md(seg):
    items = []
    for f in seg.get("seg_flags", []):
        items.append(f"> - {ICON[f['level']]} 整段：{f['msg']}")
    for i, p in enumerate(seg.get("pairs", []), 1):
        if not p["level"]:
            continue
        reasons = "；".join(f"{ICON[f['level']]} {f['msg']}" for f in p["flags"])
        items.append(f"> - 第 {i} 句：{reasons}\n>     - 原文：{_fn(p['en'])}\n>     - 译文：{_fn(p['zh'])}")
    if not items:
        return ""
    return f"> [!note]- {len(items)} 处可以核对\n" + "\n".join(items) + "\n"


def render_markdown(doc, stats, sections_zh=None, only=None, review_info=None, cite_info=None):
    sections_zh = sections_zh or {}
    s, total = _counts(stats)
    title_zh = sections_zh.get("__title__")
    chips = " ".join(f'<mark style="background:{COLOR[lv]};color:inherit">{LABEL[lv]} {s.get(lv, 0)}</mark>' for lv in LEVELS)
    lines = [
        "---", f"arxiv: \"{doc['arxiv_id']}\"", f"title: \"{doc['title']}\"",
        f"generated: {datetime.date.today().isoformat()}", "tool: iuno_reader", "cssclasses: [iuno-reader]", "---", "",
        f"# {title_zh or doc['title']}", "",
    ]
    if title_zh:
        lines += [f"<small>{doc['title']}</small>", ""]
    lines += [f"<small>arXiv {doc['arxiv_id']} ｜ https://arxiv.org/abs/{doc['arxiv_id']}</small>", "",
              f"> [!info]- 关于这份译文（共 {total} 句，有标记 {total - s.get('ok', 0)} 句）",
              "> 每段先是英文原文，接着是中文译文。有底色的句子值得核对：鼠标悬停看原因，段落后的折叠框里有逐句对照。",
              f"> {chips}",
              f"> {_review_line(stats, review_info)}",
              "> 标记只是提醒：标出的句子要看一眼，没标出的也不保证完全正确。", ""]
    cur_sec = cur_sub = None
    for seg in reading_order(doc["segments"], only):
        if seg["section"] != cur_sec:
            cur_sec, cur_sub = seg["section"], None
            zh = sections_zh.get(cur_sec)
            lines += [f"## {zh or cur_sec}", ""] + ([f"<small>{cur_sec}</small>", ""] if zh else [])
        if seg.get("subsection") and seg["subsection"] != cur_sub:
            cur_sub = seg["subsection"]
            zh = sections_zh.get(cur_sub)
            lines += [f"### {zh or cur_sub}" + (f" <small>{cur_sub}</small>" if zh else ""), ""]
        kind = seg["kind"]
        if kind == "equation":
            lines += [f"$${seg.get('math', '')}$$", ""]
            continue
        en = _fn(seg["en"])
        zh = _zh_md(seg["pairs"]) if seg.get("pairs") else "*（这一段还没有翻译）*"
        if kind == "footnote":
            n = seg.get("footnote")
            lines += [f"<small>注 {n}：{en}</small>", "", f"<small>注 {n}：{zh}</small>", ""]
        elif kind in ("caption", "table"):
            # 用 Obsidian 的折叠框装图和图注，标题写「图 N」，正文字号不缩小
            m = re.match(r"\s*(Figure|Fig\.|Table|Tab\.)\s*(\d+)", seg["en"])
            label = (("表 " if m.group(1).lower().startswith("tab") else "图 ") + m.group(2)) if m else "图"
            box = [f"> [!example] {label}"]
            for url in seg.get("images") or ([seg["image"]] if seg.get("image") else []):
                box += [f"> ![]({url})", ">"]
            box += [f"> {en}", ">", f"> {zh}"]
            lines += box + [""]
        else:
            lines += [en, "", zh, ""]
        fl = _flags_md(seg)
        if fl:
            lines += [fl]
    lines += _refs_md(doc, reading_order(doc["segments"], only), cite_info)
    return "\n".join(lines)


def _refs_md(doc, ordered, cite_info):
    """文末：本文引用到的库内论文（双链到译文、卡片、PDF），以及全部参考文献的折叠框。"""
    bib, refs = doc.get("bib") or {}, (cite_info or {}).get("refs") or {}
    lib = (cite_info or {}).get("lib") or {}
    used, where_cited = [], {}
    for seg in ordered:
        for c in seg.get("cites") or []:
            for r in c["refs"]:
                if r["b"] in bib:
                    if r["b"] not in used:
                        used.append(r["b"])
                    sec = seg["section"].split(" ")[0] if seg["section"] else ""
                    sec = "摘要" if sec == "Abstract" else (f"§{sec}" if sec else "")
                    where_cited.setdefault(r["b"], [])
                    if sec and sec not in where_cited[r["b"]]:
                        where_cited[r["b"]].append(sec)
    if not used:
        return []
    out = []
    inlib = [(b, lib[refs[b]["lib"]]) for b in used if (refs.get(b) or {}).get("lib") in lib]
    if inlib:
        out += ["## 本文引用的库内论文", "",
                f"<small>正文引用到的 {len(used)} 篇里，有 {len(inlib)} 篇在你的知识库里。</small>", ""]
        for b, e in inlib:
            links = [f"[[{e['translation'][:-5]}.md|译文]]" if e["translation"] else "",
                     f"[[{e['card'][:-3]}|卡片]]" if e["card"] else "",
                     f"[[{e['pdf']}|PDF]]" if e["pdf"] else ""]
            secs = "、".join(where_cited.get(b, []))
            out.append(f"- **{bib[b].get('tag', '')}**《{e['title'] or bib[b].get('title', '')}》"
                       f" → {'；'.join(e['where']) or '库内'}　{' · '.join(x for x in links if x)}"
                       + (f"　<small>（在 {secs} 引用）</small>" if secs else ""))
        out.append("")
    out += [f"> [!quote]- 正文引用的全部参考文献（{len(used)} 篇）"]
    for b in used:
        d, r = bib[b], refs.get(b) or {}
        arx = r.get("arxiv") or d.get("arxiv")
        mark = "📚 " if r.get("lib") in lib else ""
        body = f"{d.get('authors', '')}. {d.get('title', '')}" if d.get("title") else d.get("text", "")[:200]
        out.append(f"> - {mark}**{d.get('tag', '')}** {body}" + (f" [arXiv {arx}](https://arxiv.org/abs/{arx})" if arx else ""))
    return out + [""]


# ---------------------------------------------------------------- HTML（左右两栏）

def _h(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return _fn(s)


CSS = """
@import url('https://cdn.jsdelivr.net/npm/computer-modern@0.1.2/cmu-serif.min.css');
:root{--en:"CMU Serif","Latin Modern Roman","Computer Modern Serif","Times New Roman",Times,serif;
      --zh:"Noto Serif SC","Source Han Serif SC","Songti SC","SimSun","STSong",serif;
      --ink:#141414;--sub:#5f5f5f;--rule:#e6e2d8;--hl:#e4f1e8;
      --zhw:500;--zhb:800;--enstroke:.35px}
html{background:#fdfcf8}
body{margin:0;color:var(--ink)}
/* 字体粗细三档：细 / 中（默认）/ 粗。英文 CMU Serif 只有常规和粗体两种，用细描边加粗；中文思源宋体是可变字重 */
body.w1{--zhw:400;--zhb:700;--enstroke:0px}
body.w3{--zhw:600;--zhb:900;--enstroke:.6px}
.en{-webkit-text-stroke:var(--enstroke) currentColor}
.zh{font-weight:var(--zhw)}
.zh b{font-weight:var(--zhb)}
/* 英文：左对齐让词距均匀（两端对齐会把空隙撑得忽宽忽窄，眼睛要不停找词的边界），再稍微加宽词距 */
body .en{text-align:left;word-spacing:.06em;hyphens:manual}
body.wide .en{word-spacing:.26em}
/* 加粗三选一：首字母（核心词的前几个字母加粗，study → **st**udy）/ 整词（核心词深色稍粗，其余变浅灰）/ 关 */
.w{cursor:pointer;border-radius:2px}
body.bio .en .w.k .h{font-weight:bold}
body.kw .en .w{color:#8d8d8d}
body.kw .en .w.k{color:var(--ink);-webkit-text-stroke:calc(var(--enstroke) + .4px) currentColor}
/* 点词高亮：最多三个词，三种底色 */
.f1{background:#c9e9cf!important}.f2{background:#cfdff7!important}.f3{background:#f1d6ee!important}
.p.f1 .w,.p.f2 .w,.p.f3 .w{background:transparent!important}
.nb{cursor:pointer;font-size:.8em;margin-left:2px;vertical-align:.1em;user-select:none}
.clear{display:none}
body.focusing .clear{display:inline}
.weights span,.bolds span{cursor:pointer;padding:0 6px;border-radius:3px}
.weights span.on,.bolds span.on{background:#e4f1e8;color:#2f7a4c}
main{max-width:1320px;margin:0 auto;padding:48px 56px 120px}
.pair{display:grid;grid-template-columns:1fr 1fr;column-gap:56px}
.en{font-family:var(--en);font-size:17.5px;line-height:1.72;hyphens:auto;text-align:justify}
.zh{font-family:var(--zh);font-size:16.5px;line-height:1.95;text-align:justify}
header{margin-bottom:40px;border-bottom:1px solid var(--rule);padding-bottom:28px}
header .title .en{font-size:28px;line-height:1.3;font-weight:normal;text-align:left}
header .title .zh{font-size:25px;line-height:1.45;font-weight:600;text-align:left}
.meta{font-size:13px;color:var(--sub);font-family:var(--zh);margin-top:14px;line-height:1.8}
.meta a{color:var(--sub)}
.chips span{display:inline-block;padding:1px 8px;border-radius:3px;margin-right:8px}
.toggle{cursor:pointer;user-select:none;color:#3b915d;text-decoration:underline;text-underline-offset:3px}
h2.pair{margin:56px 0 18px}
h2 .en{font-size:22px;font-weight:bold;text-align:left;line-height:1.35}
h2 .zh{font-size:21px;font-weight:700;text-align:left;line-height:1.5}
h3.pair{margin:30px 0 10px}
h3 .en{font-size:18px;font-weight:bold;font-style:italic;text-align:left}
h3 .zh{font-size:17.5px;font-weight:700;text-align:left}
.para{margin:0 0 1.15em}
.para p{margin:0}
.s{border-radius:2px;transition:background .12s}
.s.hl{background:var(--hl)!important}
body.plain .s.f{background:none!important}
body.plain .nb{display:none}
.notes{grid-column:1/3;display:none;margin:8px 0 2px;padding:10px 14px;background:#f5f2ea;border-radius:6px;
       font-family:var(--zh);font-size:13.5px;line-height:1.8;color:#444}
.para.open .notes{display:block}
.notes div{margin:3px 0}
/* 图块：不用小字，用浅暖灰底色整体区分；开头的「Figure N / 图 N」做成绿色标签 */
figure{margin:30px -26px 34px;padding:24px 26px 18px;background:#f4f1e9;border-radius:12px}
figure .imgs{display:flex;flex-wrap:wrap;justify-content:center;align-items:flex-end;gap:12px;margin:0 auto 18px;max-width:88%}
figure img{display:block;max-height:460px;max-width:100%;flex:1 1 0;min-width:0;object-fit:contain;background:#fff;border-radius:4px}
figure .imgs img:only-child{max-width:82%;flex:0 1 auto}
/* 侧栏目录：宽屏（主栏两边放得下）常驻左侧，窄屏收成右下角「目录」按钮，点开从左边滑出 */
html{scroll-behavior:smooth}
h2.pair,h3.pair,figure,header{scroll-margin-top:18px}
.toc{position:fixed;left:0;top:0;bottom:0;width:260px;overflow-y:auto;z-index:40;background:#fdfcf8;
     border-right:1px solid var(--rule);padding:18px 12px 40px;font-family:var(--zh);font-size:13.5px;
     transform:translateX(-102%);transition:transform .2s;box-shadow:2px 0 14px rgba(0,0,0,.04)}
.toc.on{transform:none}
.toc a{display:block;color:var(--ink);text-decoration:none;padding:5px 8px;border-radius:6px;line-height:1.45}
.toc a:hover{background:#f1eee5}
.toc a.l1{font-weight:700;margin-bottom:6px}
.toc a.l3{padding-left:22px;font-size:12.5px;color:#444}
.toc a .e{display:block;font-family:var(--en);font-size:11.5px;color:var(--sub);margin-top:1px}
.toc a.cur{background:var(--hl);color:#2f7a4c}
.toc-h{font-size:12px;color:var(--sub);margin:14px 8px 6px;display:flex;justify-content:space-between}
.toc-x{cursor:pointer;font-size:16px;line-height:1}
.toc-figs{display:flex;flex-wrap:wrap;gap:4px;padding:0 6px}
.toc-figs a{padding:2px 8px;background:#f1eee5;font-size:12.5px}
.toc a.toc-lib{cursor:pointer;color:#2f7a4c;font-size:12.5px}
/* 引用：虚线下划线，点开看是哪篇；在你知识库里的用绿色实线，后面带个小「库」字 */
.ct{cursor:pointer;border-bottom:1px dotted #9aa3ad}
.ct:hover{background:#eef3f8}
.ct.in{border-bottom:1.5px solid #3b915d}
.ct.in:hover{background:var(--hl)}
.ct.in::after{content:"库";font:10px var(--zh);color:#fff;background:#3b915d;border-radius:3px;padding:0 2px;margin-left:2px;
     vertical-align:super;-webkit-text-stroke:0}
.ct.flash{background:#fff3b0!important;transition:background .6s}
.ref-pop{position:absolute;z-index:47;width:400px;max-width:92vw;background:#fff;border:1px solid var(--rule);border-radius:10px;
     padding:12px 14px;font:13px/1.65 var(--zh);box-shadow:0 6px 24px rgba(0,0,0,.12);color:var(--ink)}
.ref-pop .ti{font-family:var(--en);font-size:14.5px;font-weight:700;line-height:1.4}
.ref-pop .au{color:var(--sub);font-size:12.5px;margin:2px 0 6px}
.ref-pop .lib{background:var(--hl);border-radius:6px;padding:6px 8px;margin:6px 0;color:#2f7a4c}
.ref-pop .lib .where{font-weight:700}
.ref-pop a{color:#2f7a4c;margin-right:10px;text-underline-offset:3px}
.ref-pop .row{display:flex;justify-content:space-between;align-items:center;margin-top:6px;font-size:12px;color:var(--sub)}
.ref-pop .nx{cursor:pointer;color:#2f7a4c}
.toc-btn,.top-btn{position:fixed;right:20px;z-index:39;font:13px var(--zh);border:1px solid var(--rule);background:#fff;
     color:#2f7a4c;border-radius:20px;padding:6px 14px;cursor:pointer;text-decoration:none;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.toc-btn{bottom:62px}.top-btn{bottom:20px}
@media (min-width:1860px){.toc{transform:none;box-shadow:none}.toc-btn,.toc-x{display:none}}
/* 标签批注：选中文字 → 「✎ 批注」；有批注的句子加下划线和小标记；侧栏可按标签筛选、跳转、导出 */
.ann-add{position:absolute;z-index:45;font:13px var(--zh);background:#2f7a4c;color:#fff;border:none;border-radius:6px;
     padding:4px 10px;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.15)}
.ann-pop{position:absolute;z-index:46;width:340px;max-width:92vw;background:#fff;border:1px solid var(--rule);border-radius:10px;
     padding:12px;font:13px var(--zh);box-shadow:0 6px 24px rgba(0,0,0,.12)}
.ann-pop .q{color:var(--sub);font-size:12.5px;max-height:4.5em;overflow:auto;margin-bottom:8px;border-left:3px solid var(--rule);padding-left:8px}
.ann-pop textarea{width:100%;height:76px;font:13px var(--zh);border:1px solid var(--rule);border-radius:6px;padding:6px;margin-top:8px;box-sizing:border-box}
.ann-pop input{font:12.5px var(--zh);border:1px solid var(--rule);border-radius:6px;padding:3px 6px;width:96px}
.ann-pop .row{display:flex;gap:6px;justify-content:flex-end;margin-top:8px}
.ann-pop button{font:13px var(--zh);border:1px solid var(--rule);background:#fff;border-radius:6px;padding:4px 10px;cursor:pointer}
.ann-pop button.pri{background:#2f7a4c;color:#fff;border-color:#2f7a4c}
.ann-pop button.del{color:#c0392b;margin-right:auto}
.tag{display:inline-block;padding:1px 8px;margin:2px 3px 2px 0;border-radius:10px;font-size:12px;cursor:pointer;border:1px solid transparent;
     background:#f1eee5;color:#555;user-select:none}
.tag.on{color:#fff}
.s.ann{text-decoration:underline;text-decoration-thickness:2px;text-underline-offset:4px;text-decoration-color:var(--tc,#2f7a4c)}
.ann-mark{cursor:pointer;font-size:.78em;margin-left:2px;vertical-align:.15em;color:var(--tc,#2f7a4c);user-select:none}
.s.flash{animation:flash 1.6s}
@keyframes flash{0%,60%{background:#fff3b0}100%{background:transparent}}
.ann-list a{font-size:12.5px}
.ann-list a .n{display:block;color:var(--sub);font-size:11.5px}
.ann-tools{display:flex;flex-wrap:wrap;gap:4px;padding:0 6px 6px}
.ann-tools button{font:12px var(--zh);border:1px solid var(--rule);background:#fff;border-radius:6px;padding:2px 8px;cursor:pointer}
.ann-empty{color:var(--sub);font-size:12.5px;padding:0 8px}
/* 看大图：点图片全屏显示（铺满窗口），再点一下切到原始尺寸可滚动看细节，Esc 或点空白处关闭 */
figure img{cursor:zoom-in}
.lb{position:fixed;inset:0;z-index:50;background:#f4f1e9;display:none;overflow:auto;cursor:zoom-out}
.lb.on{display:flex}
.lb img{margin:auto;max-width:96vw;max-height:94vh;object-fit:contain;background:#fff;box-shadow:0 2px 18px rgba(0,0,0,.12);cursor:zoom-in}
.lb.on.full{display:block}
.lb.full img{display:block;margin:24px auto;max-width:none;max-height:none;cursor:zoom-out}
.lb .tip{position:fixed;top:10px;right:16px;font:13px var(--zh);color:var(--sub);background:#fff;padding:3px 10px;border-radius:6px;border:1px solid var(--rule)}
figure .en{font-size:16.5px;line-height:1.68}
figure .zh{font-size:15.5px;line-height:1.9;text-align:left}
.figlabel{color:#2f7a4c;font-weight:bold;margin-right:.35em;-webkit-text-stroke:0}
.zh .figlabel{font-weight:800}
.fn .en,.fn .zh{font-size:13.5px;color:var(--sub);line-height:1.6}
.fn{margin:-.4em 0 1.1em}
.eq{text-align:center;margin:10px 0 18px;overflow-x:auto;font-size:17px}
code{font-family:"CMU Typewriter Text",Consolas,monospace;font-size:.92em}
sup{font-size:.7em}
@media (max-width:860px){main{padding:28px 18px}.pair{grid-template-columns:1fr;row-gap:6px}}
"""

JS = r"""<script>
document.querySelectorAll('.s').forEach(el=>{
  const peers=()=>document.querySelectorAll(`.s[data-s="${el.dataset.s}"]`);
  el.addEventListener('mouseenter',()=>peers().forEach(x=>x.classList.add('hl')));
  el.addEventListener('mouseleave',()=>peers().forEach(x=>x.classList.remove('hl')));
});
// 点有标记句子后面的小圆点，展开或收起这一段的说明
document.querySelectorAll('.nb').forEach(el=>el.addEventListener('click',e=>{
  e.stopPropagation();el.closest('.para').classList.toggle('open');}));
// 点词高亮：同一个键（词的原形，或术语表词组）全文一起铺底色；中文栏里对应的术语译名也一起亮
const focus=[];  // 当前高亮的键，最多三个
function paint(){
  document.querySelectorAll('.f1,.f2,.f3').forEach(x=>x.classList.remove('f1','f2','f3'));
  focus.forEach((k,i)=>document.querySelectorAll(`[data-l="${CSS.escape(k)}"]`).forEach(x=>x.classList.add('f'+(i+1))));
  document.body.classList.toggle('focusing',focus.length>0);}
document.addEventListener('click',e=>{
  if(String(getSelection())) return;  // 正在选文字（准备批注）时不算点词
  const w=e.target.closest('.w,.g');if(!w) return;
  const p=w.closest('.p');const k=(p&&p.dataset.l)||w.dataset.l;if(!k) return;
  const i=focus.indexOf(k);
  if(i>=0) focus.splice(i,1); else {if(focus.length>=3) focus.shift(); focus.push(k);}
  paint();});
const C=document.getElementById('clear');
if(C) C.addEventListener('click',()=>{focus.length=0;paint();});
function sw(id,cls,on_txt,off_txt,key){const el=document.getElementById(id);if(!el) return;
  const set=on=>{document.body.classList.toggle(cls,on);el.textContent=on?on_txt:off_txt;
    try{localStorage.setItem(key,on?'1':'0')}catch(e){}};
  el.addEventListener('click',()=>set(!document.body.classList.contains(cls)));
  let v='0';try{v=localStorage.getItem(key)||'0'}catch(e){}set(v==='1');}
sw('wst','wide','加宽词距：开','加宽词距：关','iuno-reader-wide');
// 多选一的开关：字体粗细 w1/w2/w3，加粗方式 none/bio/kw
function pick(sel,all,key,dflt){const B=document.querySelectorAll(sel);
  const set=v=>{all.forEach(c=>document.body.classList.remove(c));if(v!=='none') document.body.classList.add(v);
    B.forEach(x=>x.classList.toggle('on',x.dataset.w===v));try{localStorage.setItem(key,v)}catch(e){}};
  B.forEach(x=>x.addEventListener('click',()=>set(x.dataset.w)));
  let v=dflt;try{v=localStorage.getItem(key)||dflt}catch(e){}set(v);}
pick('.weights span',['w1','w2','w3'],'iuno-reader-weight','w2');
pick('.bolds span',['bio','kw'],'iuno-reader-bold','bio');
// arXiv 上个别图片文件是坏的（例如 2602.04212v3 的图 1 只传了一截）：加载失败就换同一篇更早版本里的同名图片
function olderVersion(im){const m=im.src.match(/(\d{4}\.\d{4,5})v(\d+)\//);
  if(m&&+m[2]>1) im.src=im.src.replace(m[0],m[1]+'v'+(+m[2]-1)+'/');}
document.querySelectorAll('figure img').forEach(im=>{im.addEventListener('error',()=>olderVersion(im));
  if(im.complete&&im.naturalWidth===0) olderVersion(im);});  // 脚本运行前就已经失败的
// 侧栏目录：按钮开合；窄屏点了目录项自动收起；滚动时高亮当前所在的节
const TOC=document.getElementById('toc'),wide=()=>matchMedia('(min-width:1860px)').matches;
if(TOC){
  document.getElementById('tocBtn').addEventListener('click',()=>TOC.classList.toggle('on'));
  TOC.querySelector('.toc-x').addEventListener('click',()=>TOC.classList.remove('on'));
  TOC.querySelectorAll('a').forEach(a=>a.addEventListener('click',()=>{if(!wide())TOC.classList.remove('on');}));
  const heads=[...document.querySelectorAll('h2.pair[id],h3.pair[id]')];let last=null;
  const spy=()=>{let cur=null;for(const h of heads){if(h.getBoundingClientRect().top<140)cur=h;else break;}
    const id=cur?cur.id:null;if(id===last)return;last=id;
    TOC.querySelectorAll('a.cur').forEach(a=>a.classList.remove('cur'));
    const a=id&&TOC.querySelector(`a[data-a="${id}"]`);
    if(a){a.classList.add('cur');const r=a.getBoundingClientRect();if(r.top<60||r.bottom>innerHeight-20)a.scrollIntoView({block:'center'});}};
  let tick=false;addEventListener('scroll',()=>{if(!tick){tick=true;requestAnimationFrame(()=>{spy();tick=false;});}});spy();}
// 看大图
const LB=document.createElement('div');LB.className='lb';
LB.innerHTML="<img alt=''><span class='tip'>点图片：原始尺寸 / 适应窗口　·　Esc 或点空白处关闭</span>";
document.body.appendChild(LB);const LBI=LB.querySelector('img');
document.querySelectorAll('figure img').forEach(im=>im.addEventListener('click',()=>{
  LBI.src=im.src;LB.classList.remove('full');LB.classList.add('on');document.body.style.overflow='hidden';}));
LBI.addEventListener('click',e=>{e.stopPropagation();LB.classList.toggle('full');});
const lbClose=()=>{LB.classList.remove('on','full');document.body.style.overflow='';};
LB.addEventListener('click',lbClose);
document.addEventListener('keydown',e=>{if(e.key==='Escape')lbClose();});
const t=document.getElementById('toggle');
if(t) t.addEventListener('click',()=>{document.body.classList.toggle('plain');
  t.textContent=document.body.classList.contains('plain')?'显示标记':'隐藏标记';});
</script>"""


ANN_JS = r"""<script>
// ---------- 标签批注 ----------
// 存在浏览器里（localStorage，按论文编号分开）；通过 iuno_reader 前端服务打开时，同时存到服务器的 data/<编号>/notes.json。
// 删除的批注留一个 deleted 标记，免得和服务器上的旧版本合并时又冒出来。
(function(){
const PID=document.body.dataset.pid||location.pathname, KEY='iuno-reader-notes:'+PID;
const TAGS=[['重点','#d9534f'],['不懂','#e8a33d'],['疑问','#8a7bd1'],['生词','#3b82c4'],['方法','#2f7a4c'],['结论','#6b6b6b']];
const color=t=>(TAGS.find(x=>x[0]===t)||[0,'#2f7a4c'])[1];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const SERVER=/^https?:/.test(location.protocol)&&location.pathname.startsWith('/out/');
const now=()=>{const d=new Date(),p=n=>String(n).padStart(2,'0');return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;};
let notes=[];try{notes=JSON.parse(localStorage.getItem(KEY)||'[]')}catch(e){}
const live=()=>notes.filter(n=>!n.deleted);
// 句子 → 所在的节和小节，导出时按节整理
const order=[],secOf={};let h2='',h3='';
document.querySelectorAll('h2.pair,h3.pair,.en .s[data-s]').forEach(el=>{
  const name=el.querySelector?.('.zh')?.innerText.trim()||el.querySelector?.('.en')?.innerText.trim()||'';
  if(el.tagName==='H2'){h2=name;h3='';}else if(el.tagName==='H3'){h3=name;}else{order.push(el.dataset.s);secOf[el.dataset.s]=[h2,h3];}});
const textOf=(sid,side)=>(document.querySelector(`.${side} .s[data-s="${sid}"]`)?.innerText||'').replace(/●/g,'').trim();
function persist(sync=true){
  try{localStorage.setItem(KEY,JSON.stringify(notes))}catch(e){}
  if(sync&&SERVER)fetch('/api/notes/'+PID,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({notes,markdown:toMarkdown()})}).catch(()=>{});}
function merge(list){const m=new Map(notes.map(n=>[n.id,n]));
  list.forEach(n=>{const o=m.get(n.id);if(!o||(n.time||'')>(o.time||''))m.set(n.id,n);});
  notes=[...m.values()];persist(false);paint();}
// 选中文字 → 「✎ 批注」按钮
const add=document.createElement('button');add.className='ann-add';add.textContent='✎ 批注';add.style.display='none';
document.body.appendChild(add);let pending=null;
document.addEventListener('mouseup',e=>{if(e.target.closest('.ann-pop,.ann-add'))return;setTimeout(()=>{
  const sel=getSelection(),txt=String(sel).trim();
  const node=sel.rangeCount&&sel.anchorNode,el=node&&(node.nodeType===1?node:node.parentElement).closest('.s[data-s]');
  if(!txt||!el){add.style.display='none';return;}
  const r=sel.getRangeAt(0).getBoundingClientRect();
  pending={s:el.dataset.s,side:el.closest('.zh')?'zh':'en',quote:txt.slice(0,500)};
  add.style.left=(r.right+scrollX+6)+'px';add.style.top=(r.top+scrollY-34)+'px';add.style.display='block';},0);});
add.addEventListener('click',()=>{add.style.display='none';
  if(pending)open({id:'n'+Date.now().toString(36),tags:[],note:'',...pending},parseInt(add.style.left),parseInt(add.style.top)+34);});
// 编辑框
let pop=null;
function close(){if(pop){pop.remove();pop=null;}}
function open(n,x,y){close();const isNew=!notes.some(m=>m.id===n.id);const sel=new Set(n.tags);
  const all=[...TAGS.map(t=>t[0]),...new Set(live().flatMap(m=>m.tags).filter(t=>!TAGS.some(x=>x[0]===t)))];
  pop=document.createElement('div');pop.className='ann-pop';
  pop.innerHTML=`<div class="q">${esc(n.quote)}</div><div class="tags"></div>
   <input placeholder="新标签，回车添加"><textarea placeholder="写点什么：为什么重要、哪里不懂、和哪篇有关……">${esc(n.note)}</textarea>
   <div class="row">${isNew?'':'<button class="del">删除</button>'}<button class="cancel">取消</button><button class="pri">保存</button></div>`;
  const box=pop.querySelector('.tags');
  const drawTags=()=>{box.innerHTML=all.map(t=>`<span class="tag ${sel.has(t)?'on':''}" style="${sel.has(t)?'background:'+color(t):''}">${esc(t)}</span>`).join('');
    box.querySelectorAll('.tag').forEach(s=>s.onclick=()=>{const t=s.textContent;sel.has(t)?sel.delete(t):sel.add(t);drawTags();});};
  drawTags();
  pop.querySelector('input').onkeydown=e=>{const v=e.target.value.trim().replace(/\s+/g,'_');if(e.key==='Enter'&&v){if(!all.includes(v))all.push(v);sel.add(v);e.target.value='';drawTags();}};
  pop.querySelector('.cancel').onclick=close;
  pop.querySelector('.pri').onclick=()=>{Object.assign(n,{tags:[...sel],note:pop.querySelector('textarea').value.trim(),time:now(),deleted:false});
    if(isNew)notes.push(n);persist();paint();close();getSelection().removeAllRanges();};
  const del=pop.querySelector('.del');if(del)del.onclick=()=>{if(!confirm('删除这条批注？'))return;Object.assign(n,{deleted:true,time:now()});persist();paint();close();};
  document.body.appendChild(pop);
  pop.style.left=Math.max(8,Math.min(x,scrollX+innerWidth-pop.offsetWidth-12))+'px';pop.style.top=y+'px';
  pop.querySelector('textarea').focus();}
document.addEventListener('keydown',e=>{if(e.key==='Escape')close();});
// 显示：句子下划线 + 句末小标记；侧栏列表
function jump(sid){const el=document.querySelector(`.s[data-s="${sid}"]`);if(!el)return;
  el.scrollIntoView({block:'center'});document.querySelectorAll(`.s[data-s="${sid}"]`).forEach(x=>{x.classList.remove('flash');void x.offsetWidth;x.classList.add('flash');});}
let filter='';
function paint(){
  document.querySelectorAll('.ann-mark').forEach(m=>m.remove());
  document.querySelectorAll('.s.ann').forEach(s=>{s.classList.remove('ann');s.style.removeProperty('--tc');});
  live().forEach(n=>document.querySelectorAll(`.s[data-s="${n.s}"]`).forEach(s=>{
    const c=color(n.tags[0]);s.classList.add('ann');s.style.setProperty('--tc',c);
    const m=document.createElement('span');m.className='ann-mark';m.textContent='✎';m.style.setProperty('--tc',c);
    m.title=(n.tags.length?'['+n.tags.join('][')+'] ':'')+(n.note||n.quote);
    m.onclick=e=>{e.stopPropagation();const r=m.getBoundingClientRect();open(n,r.left+scrollX,r.bottom+scrollY+6);};
    s.after(m);}));
  const toc=document.getElementById('toc');if(!toc)return;
  let sec=document.getElementById('annSec');
  if(!sec){sec=document.createElement('div');sec.id='annSec';toc.appendChild(sec);}
  const list=live().sort((a,b)=>order.indexOf(a.s)-order.indexOf(b.s));
  const tags=[...new Set(list.flatMap(n=>n.tags))];
  const shown=filter?list.filter(n=>n.tags.includes(filter)):list;
  sec.innerHTML=`<div class="toc-h">批注（${list.length}）</div>
   <div class="ann-tools">${['',...tags].map(t=>`<span class="tag ${filter===t?'on':''}" data-t="${esc(t)}" style="${filter===t?'background:'+(t?color(t):'#2f7a4c'):''}">${t?esc(t):'全部'}</span>`).join('')}</div>
   <div class="ann-tools"><button data-x="md">导出复盘笔记</button><button data-x="json">备份</button><button data-x="in">导入</button></div>
   <div class="ann-list">${shown.map(n=>`<a href="javascript:void 0" data-s="${n.s}">${n.tags.map(t=>`<span style="color:${color(t)}">#${esc(t)}</span>`).join(' ')} ${esc(n.quote.slice(0,40))}${n.note?`<span class="n">${esc(n.note.slice(0,60))}</span>`:''}</a>`).join('')}</div>
   ${list.length?'':'<div class="ann-empty">在正文里选中文字，就会出现「✎ 批注」按钮。</div>'}`;
  sec.querySelectorAll('.ann-tools .tag').forEach(t=>t.onclick=()=>{filter=t.dataset.t;paint();});
  sec.querySelectorAll('.ann-list a').forEach(a=>a.onclick=()=>jump(a.dataset.s));
  sec.querySelector('[data-x=md]').onclick=()=>download(`${PID}-批注.md`,toMarkdown(),'text/markdown');
  sec.querySelector('[data-x=json]').onclick=()=>download(`${PID}-批注备份.json`,JSON.stringify(notes,null,1),'application/json');
  sec.querySelector('[data-x=in]').onclick=()=>{const f=document.createElement('input');f.type='file';f.accept='.json';
    f.onchange=async()=>{try{merge(JSON.parse(await f.files[0].text()));persist();}catch(e){alert('导入失败：'+e.message);}};f.click();};}
function download(name,text,type){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type}));a.download=name;a.click();}
function toMarkdown(){
  const title=document.querySelector('header .title .zh')?.innerText.trim()||document.title;
  const en=document.querySelector('header .title .en')?.innerText.trim()||'';
  let md=`# ${title} · 批注\n\n> ${en}（arXiv ${PID}）· 导出于 ${now()}\n> 中文对照：[[translations/${PID}-zh.md]]\n\n`;let last='';
  live().sort((a,b)=>order.indexOf(a.s)-order.indexOf(b.s)).forEach(n=>{
    const [s2,s3]=secOf[n.s]||['',''],head=[s2,s3].filter(Boolean).join(' › ');
    if(head!==last){md+=`## ${head||'（无节名）'}\n\n`;last=head;}
    md+=`- ${n.tags.map(t=>'#'+t).join(' ')} 「${n.quote.replace(/\s+/g,' ')}」\n`;
    if(n.note)md+=`  - 批注：${n.note.replace(/\n/g,'\n    ')}\n`;
    md+=`  - 原句：${textOf(n.s,'en')}\n  - 译文：${textOf(n.s,'zh')}\n  - ${n.time}\n\n`;});
  return md;}
paint();
if(SERVER)fetch('/api/notes/'+PID).then(r=>r.ok?r.json():null).then(d=>{if(d&&d.notes)merge(d.notes);}).catch(()=>{});
})();
</script>"""

CITE_JS = r"""<script>
// ---- 引用：悬停一行提示；点开卡片看是哪篇、在不在你的知识库里，能跳到本文下一处引用它的地方 ----
(()=>{
const el=document.getElementById('refdata');const R=el?JSON.parse(el.textContent):{};
const tip=d=>[d.tag,d.ti].filter(Boolean).join(' · ')+(d.lib?`  —  已在你的库里：${d.lib.where[0]||''}`:'');
document.querySelectorAll('.ct').forEach(c=>{const d=R[c.dataset.b];if(d)c.title=tip(d);});
let pop=null;
function close(){if(pop){pop.remove();pop=null;}}
function add(parent,tag,cls,text){const x=document.createElement(tag);if(cls)x.className=cls;if(text!=null)x.textContent=text;parent.appendChild(x);return x;}
function link(parent,href,text){const a=add(parent,'a','',text);a.href=href;if(!href.startsWith('#'))a.target='_blank';return a;}
function occ(bid){return [...document.querySelectorAll(`.en .ct[data-b="${CSS.escape(bid)}"]`)];}
function show(c){
  close();const bid=c.dataset.b,d=R[bid];if(!d)return;
  pop=document.createElement('div');pop.className='ref-pop';
  add(pop,'div','ti',d.ti||d.tag);
  add(pop,'div','au',[d.au,d.y].filter(Boolean).join(' · ')||d.text||'');
  if(d.lib){const b=add(pop,'div','lib');add(b,'span','where','📚 已在你的库里：'+(d.lib.where||[]).join('；'));
    const l=add(b,'div');
    if(d.lib.tr)link(l,d.lib.tr,'中文译文');
    if(d.lib.pdf)link(l,d.lib.pdf,'PDF');
    if(d.lib.card)link(l,d.lib.card,'论文卡片（Obsidian）');}
  const l2=add(pop,'div');
  if(d.arxiv)link(l2,'https://arxiv.org/abs/'+d.arxiv,'arXiv '+d.arxiv);
  else add(l2,'span','',d.lib?'':'arXiv 上没找到对应的论文 ');
  (d.links||[]).forEach(u=>link(l2,u,u.includes('doi')?'DOI':'出版页'));
  const all=occ(bid),i=all.indexOf(c.closest('.en')?c:all[0]);
  const row=add(pop,'div','row');add(row,'span','',`本文引用了 ${all.length} 处${i>=0?`，这是第 ${i+1} 处`:''}`);
  if(all.length>1){const nx=add(row,'span','nx','下一处 ↓');nx.onclick=ev=>{ev.stopPropagation();go(all[(i+1)%all.length]);};}
  document.body.appendChild(pop);
  const r=c.getBoundingClientRect();
  pop.style.top=(scrollY+r.bottom+6)+'px';
  pop.style.left=Math.max(8,Math.min(scrollX+r.left,scrollX+innerWidth-pop.offsetWidth-12))+'px';}
function go(c){c.scrollIntoView({block:'center'});c.classList.add('flash');setTimeout(()=>c.classList.remove('flash'),900);setTimeout(()=>show(c),350);}
document.addEventListener('click',e=>{
  const t=e.target.closest('.toc-lib');
  if(t){e.preventDefault();const a=occ(t.dataset.b)[0];if(a)go(a);return;}
  if(pop&&pop.contains(e.target))return;
  const c=e.target.closest('.ct');
  if(c&&!String(getSelection())){e.stopPropagation();show(c);return;}  // 点引用不算点词高亮
  close();},true);
document.addEventListener('keydown',e=>{if(e.key==='Escape')close();});
})();
</script>"""

_NOT_KW = {"et", "al"}  # 引用里的 et al. 不算实词


def _cite_marks(sentences, cites):
    """一段里的引用 → 每句的 [(起, 止, 条目 id)]。引用按出现顺序逐句去找；
    只有一个锚点时标整个引用（如「Lepori et al. (2026)」），几个锚点时分别标（「A, 2024; B, 2025」）。"""
    marks = [[] for _ in sentences]
    todo = list(cites or [])
    for i, s in enumerate(sentences):
        pos = 0
        while todo:
            c = todo[0]
            p = s.find(c["text"], pos) if c["text"] else -1
            if p < 0:
                break
            todo.pop(0)
            end = p + len(c["text"])
            if len(c["refs"]) == 1:
                marks[i].append((p, end, c["refs"][0]["b"]))
            else:
                q = p
                for r in c["refs"]:
                    k = s.find(r["t"], q, end) if r["t"] else -1
                    if k >= 0:
                        marks[i].append((k, k + len(r["t"]), r["b"]))
                        q = k + len(r["t"])
            pos = end
    return marks


def _surname_year(text, bib_tag=""):
    """「Lepori et al., 2026」→ ("Lepori", "2026")；锚点里没有作者时用参考文献的标签。数字引用返回 (数字, "")。"""
    t = text.strip("()[] ")
    if re.fullmatch(r"\d+", t):
        return t, ""
    if not re.search(r"[A-Za-z]", t) or not re.search(r"(19|20)\d{2}", t):
        t = bib_tag or t
    year = re.search(r"(19|20)\d{2}", t)
    name = re.split(r"\s+(?:et al\.?|and|&)\s*|,\s*|\s*\(|\s+(?=(?:19|20)\d{2})", t.strip("()[] "))[0].strip()
    return name, year.group(0) if year else ""


def _zh_cite_marks(zh, bids, bib):
    """中文译文里找这一句英文引用到的条目：作者姓后面不远处跟着年份；数字引用找方括号里的数字。"""
    out, taken = [], []
    for bid in dict.fromkeys(bids):
        b = bib.get(bid, {})
        name, year = _surname_year(b.get("tag", ""), b.get("tag", ""))
        if not name:
            continue
        if not year:  # 数字引用
            pat = re.compile(rf"(?<=[\[,，、\s]){re.escape(name)}(?=[\],，、\s–-])")
        else:
            pat = re.compile(rf"{re.escape(name)}[^。；;]{{0,24}}?{year}[a-z]?")
        for m in pat.finditer(zh):
            if year == "" and not re.search(r"\[[^\]]*$", zh[:m.start()]):
                continue  # 数字不在方括号里，不是引用
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            end = m.end()
            inner = zh[m.start():end]
            if inner.count("(") + inner.count("（") > inner.count(")") + inner.count("）") and zh[end:end + 1] in ")）":
                end += 1  # 「Park et al. (2025)」的右括号一起包进来
            out.append((m.start(), end, bid))
            taken.append((m.start(), end))
            break
    return sorted(out)


def _cite_open(bid, refs):
    r = (refs or {}).get(bid) or {}
    return f"<span class='ct{' in' if r.get('lib') else ''}' data-b='{_attr(bid)}'>"


def _en_html(sentence, terms, ann=None, marks=None, refs=None):
    """英文句子 → HTML：每个字母词包一层 <span class='w' data-l=原形>，核心词加 k，
    并把前几个字母包成 <span class='h'>（首字母加粗用）；
    术语表里的词组再包一层 <span class='p' data-l=词组>。公式、代码、占位符原样保留。
    marks：引用位置 [(起, 止, 条目 id)]，包成 <span class='ct'>。
    spaCy 不可用时退回普通转义。"""
    from . import nlp
    events = {}  # 字符位置 → 要插入的标签
    for a, b, bid in marks or []:
        events.setdefault(a, []).append(_cite_open(bid, refs))
        events.setdefault(b, []).insert(0, "</span>")
    if not nlp.available():
        if not events:
            return _h(sentence)
        cuts = sorted(events)
        res, cur = [], 0
        for p in cuts:
            res.append(_h(sentence[cur:p]) + "".join(events[p]))
            cur = p
        return "".join(res) + _h(sentence[cur:])
    ann = ann if ann is not None else nlp.analyze(sentence)
    # 引用的边界如果落在词中间，挪到词的边界上（只会在奇怪的标点里出现）
    for a, b, *_ in ann:
        for p in [p for p in events if a < p < b]:
            events.setdefault(a if p - a < b - p else b, []).extend(events.pop(p))
    # 找术语表里的多词术语在本句的位置（按词原形连续匹配，中间只允许空格或连字符）
    starts, ends, in_term = {}, {}, set()
    for t in sorted(terms, key=len, reverse=True):
        parts = re.split(r"[\s\-]+", t.strip().lower())
        if len(parts) < 2:
            continue
        key = nlp.term_key(t)
        for i in range(len(ann) - len(parts) + 1):
            if any(j in starts or j in ends for j in range(i, i + len(parts))):
                continue
            ok = all(ann[i + j][2] == parts[j] or sentence[ann[i + j][0]:ann[i + j][1]].lower() == parts[j]
                     for j in range(len(parts)))
            ok = ok and all(re.fullmatch(r"[\s\-]+", sentence[ann[i + j][1]:ann[i + j + 1][0]])
                            for j in range(len(parts) - 1))
            if ok:
                starts[i], ends[i + len(parts) - 1] = key, True
                in_term.update(range(i, i + len(parts)))  # 多词术语的每个词都算核心词（in context learning 的 in）
    out, last, bold = [], 0, False

    def raw(txt):
        nonlocal bold
        pieces = txt.split("**")
        res = []
        for n, piece in enumerate(pieces):
            if n:
                res.append("</b>" if bold else "<b>")
                bold = not bold
            piece = html.escape(piece, quote=False)
            piece = re.sub(r"`([^`]+)`", r"<code>\1</code>", piece)
            res.append(_fn(piece))
        return "".join(res)

    def raw_ev(x, y):
        """sentence[x:y] 的普通文字，中间按位置插入引用标签（含 x、y 两端）。"""
        res, cur = [], x
        for p in sorted(p for p in events if x <= p <= y):
            res.append(raw(sentence[cur:p]) + "".join(events.pop(p)))
            cur = p
        return "".join(res) + raw(sentence[cur:y])

    for i, (a, b, key, kw) in enumerate(ann):
        out.append(raw_ev(last, a))
        if i in starts:
            out.append(f"<span class='p' data-l='{_attr(starts[i])}'>")
        word = sentence[a:b]
        kw = (kw or i in in_term) and key not in _NOT_KW
        n = nlp.bold_len(word) if kw else 0
        body = (f"<span class='h'>{html.escape(word[:n])}</span>{html.escape(word[n:])}" if n
                else html.escape(word))
        out.append(f"<span class='w{' k' if kw else ''}' data-l='{_attr(key)}'>{body}</span>")
        if i in ends:
            out.append("</span>")
        last = b
    out.append(raw_ev(last, len(sentence)))
    if bold:
        out.append("</b>")
    return "".join(out)


def _zh_html(sentence, mapping, marks=None, refs=None):
    """中文句子 → HTML，并把出现的术语译名包成 <span class='g' data-l=键>，点它和点英文词一样高亮。
    marks：引用位置 [(起, 止, 条目 id)]（按原文字符位置），包成 <span class='ct'>。"""
    def inside(p):  # 位置落在 **粗体** 或 `代码` 里面时不能切开
        return sentence[:p].count("**") % 2 or sentence[:p].count("`") % 2
    marks = [m for m in marks or [] if not inside(m[0]) and not inside(m[1])]
    if marks:
        res, cur = [], 0
        for a, b, bid in marks:
            res.append(_h(sentence[cur:a]) + _cite_open(bid, refs) + _h(sentence[a:b]) + "</span>")
            cur = b
        h = "".join(res) + _h(sentence[cur:])
    else:
        h = _h(sentence)
    if not mapping:
        return h
    pat = re.compile("|".join(re.escape(z) for z in sorted(mapping, key=len, reverse=True)))
    parts = re.split(r"(<[^>]+>)", h)
    for n, part in enumerate(parts):
        if part.startswith("<") or "$" in part:
            continue
        parts[n] = pat.sub(lambda m: f"<span class='g' data-l='{_attr(mapping[m.group(0)])}'>{m.group(0)}</span>", part)
    return "".join(parts)


def _term_mapping(en, terms_all, ann=None):
    """本句英文里出现的术语 → {中文译名: 高亮键}。
    单词术语的键取它在这一句里实际还原出的原形，保证和英文栏点词时用的键一致。"""
    from . import nlp
    from .glossary import terms_in
    if not terms_all or not nlp.available():
        return {}, []
    found = terms_in(re.sub(r"\$[^$]*\$", " ", en), terms_all)
    mapping = {}
    for t in found:
        key = nlp.term_key(t)
        if ann and not re.search(r"[\s\-]", t.strip()):
            tl = t.lower()
            for a, b, k, _ in ann:
                if en[a:b].lower() in (tl, tl + "s", tl + "es") or k == key:
                    key = k
                    break
        for z in terms_all[t]["zh"]:
            for v in {z, z.rstrip("的")}:
                if len(v) >= 2:
                    mapping.setdefault(v, key)
    return mapping, found


_LABEL_EN = re.compile(r"^\s*(Figure|Fig\.|Table|Tab\.)\s*(\d+)\s*[:.]\s*")
_LABEL_ZH = re.compile(r"^\s*(图|表)\s*(\d+)\s*[：:]\s*")


def _canon_table(segments):
    """整篇统一高亮键：统计每个词形（小写）最常被还原成的原形。
    spaCy 对同一个词在不同句子里可能给出不同原形（如 prefilled / prefille），统一后点一处全文都能亮。"""
    from collections import Counter, defaultdict
    from . import nlp
    if not nlp.available():
        return {}
    votes = defaultdict(Counter)
    for seg in segments:
        for p in seg.get("pairs") or []:
            for a, b, k, _ in nlp.analyze(p["en"]):
                votes[p["en"][a:b].lower()][k] += 1
    return {w: c.most_common(1)[0][0] for w, c in votes.items()}


def _pair_html(seg, terms_all=None, canon=None, refs=None, bib=None):
    en_parts, zh_parts, notes = [], [], []
    pairs = seg.get("pairs") or [{"en": x, "zh": "", "level": None, "flags": []} for x in seg["sentences"]]
    raws = []  # 每句 (英文, 中文, 英文图表标签, 中文图表标签)
    for i, p in enumerate(pairs, 1):
        en_raw, zh_raw, lab_en, lab_zh = p["en"], p["zh"] or "", "", ""
        if i == 1 and seg["kind"] in ("caption", "table"):
            m = _LABEL_EN.match(en_raw)
            if m:
                lab_en, en_raw = f"<span class='figlabel'>{m.group(1)} {m.group(2)}</span>", en_raw[m.end():]
            m = _LABEL_ZH.match(zh_raw)
            if m:
                lab_zh, zh_raw = f"<span class='figlabel'>{m.group(1)} {m.group(2)}</span>", zh_raw[m.end():]
        raws.append((en_raw, zh_raw, lab_en, lab_zh))
    cmarks = _cite_marks([r[0] for r in raws], seg.get("cites"))
    for i, (p, (en_raw, zh_raw, lab_en, lab_zh), em) in enumerate(zip(pairs, raws, cmarks), 1):
        sid = f"{seg['id']}-{i}"
        cls, style, title, nb = "s", "", "", ""
        if p["level"]:
            cls += " f"
            style = f" style='background:{COLOR[p['level']]}'"
            tip = "；".join(f"{ICON[f['level']]} {f['msg']}" for f in p["flags"])
            title = f" title='{_attr(tip)}'"
            notes.append(f"<div><b>第 {i} 句</b>　{html.escape(tip)}</div>")
            nb = f"<span class='nb' title='点击展开说明' style='color:{_DOT[p['level']]}'>●</span>"
        from . import nlp
        ann = nlp.analyze(en_raw) if nlp.available() else None
        if ann and canon:
            ann = [(a, b, canon.get(en_raw[a:b].lower(), k), kw) for a, b, k, kw in ann]
        mapping, found = _term_mapping(en_raw, terms_all, ann)
        zm = _zh_cite_marks(zh_raw, [m[2] for m in em], bib or {}) if em else []
        en_parts.append(f"<span class='{cls}' data-s='{sid}'{style}>{lab_en}{_en_html(en_raw, found, ann, em, refs)}</span> ")
        zh_parts.append(f"<span class='{cls}' data-s='{sid}'{style}{title}>{lab_zh}"
                        f"{_zh_html(zh_raw, mapping, zm, refs)}</span>{nb}" if p["zh"] else "")
    for f in seg.get("seg_flags", []):
        notes.insert(0, f"<div><b>整段</b>　{ICON[f['level']]} {html.escape(f['msg'])}</div>")
    zh = "".join(zh_parts) if seg.get("pairs") else "<span style='color:#999'>（这一段还没有翻译）</span>"
    return "".join(en_parts), zh, notes


def _cite_data(doc, ordered, cite_info):
    """网页里用到的参考文献 → 嵌进页面的 JSON；另返回侧栏「引用了你库里的论文」的条目。
    链接都按知识库的 translations/ 目录写相对路径（网页就放在那里）。"""
    bib, refs = doc.get("bib") or {}, (cite_info or {}).get("refs") or {}
    lib, vault = (cite_info or {}).get("lib") or {}, (cite_info or {}).get("vault", "")
    used, data, inlib = [], {}, {}
    for seg in ordered:
        for c in seg.get("cites") or []:
            for r in c["refs"]:
                if r["b"] in bib and r["b"] not in used:
                    used.append(r["b"])
    for bid in used:
        b, r = bib[bid], refs.get(bid) or {}
        d = {"tag": b.get("tag", ""), "au": b.get("authors", ""), "ti": b.get("title", ""), "y": b.get("year", ""),
             "arxiv": r.get("arxiv") or b.get("arxiv", ""), "links": [u for u in b.get("links", []) if "arxiv.org" not in u][:2]}
        if not d["ti"]:
            d["text"] = b.get("text", "")[:300]
        e = lib.get(r.get("lib")) if r.get("lib") else None
        if e:
            d["lib"] = {"where": e["where"], "title": e["title"],
                        "tr": os.path.basename(e["translation"]) if e["translation"] else "",
                        "pdf": ("../" + e["pdf"]) if e["pdf"] else "",
                        "card": (f"obsidian://open?vault={urllib.parse.quote(vault)}&file="
                                 f"{urllib.parse.quote(e['card'][:-3])}") if e["card"] and vault else ""}
            inlib.setdefault(r["lib"], (bid, e))
        data[bid] = d
    return data, list(inlib.values())


def render_html(doc, stats, sections_zh=None, only=None, review_info=None, cite_info=None):
    """cite_info：参考文献的对应结果 {"refs": {条目 id: {arxiv, lib}}, "lib": {库内键: 条目}, "vault": 知识库名}。"""
    sections_zh = sections_zh or {}
    s, total = _counts(stats)
    title_zh = sections_zh.get("__title__", "")
    chips = "".join(f"<span style='background:{COLOR[lv]}'>{LABEL[lv]} {s.get(lv, 0)}</span>" for lv in LEVELS)
    out = ["<!doctype html><html lang='zh'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           f"<title>{html.escape(title_zh or doc['title'])}</title><style>{CSS}</style>",
           "<script>window.MathJax={tex:{inlineMath:[['$','$']],displayMath:[['$$','$$']]}};</script>",
           "<script src='https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js' async></script>",
           f"</head><body data-pid='{html.escape(doc['arxiv_id'])}'><main><header id='top'>",
           f"<div class='pair title'><div class='en'>{html.escape(doc['title'])}</div><div class='zh'>{html.escape(title_zh)}</div></div>",
           f"<div class='meta'><a href='https://arxiv.org/abs/{doc['arxiv_id']}'>arXiv {doc['arxiv_id']}</a>　·　"
           f"共 {total} 句，有标记 {total - s.get('ok', 0)} 句　·　<span class='toggle' id='toggle'>隐藏标记</span>"
           "　·　<span class='weights'>字体粗细 <span data-w='w1'>细</span><span data-w='w2'>中</span><span data-w='w3'>粗</span></span>"
           "　·　<span class='bolds' title='只作用于核心词（名词、动词、形容词、副词、数词、否定词和连字符复合词），the、of 这类虚词不变'>加粗 "
           "<span data-w='none'>关</span><span data-w='bio'>首字母</span><span data-w='kw'>整词</span></span>"
           "　·　<span class='toggle' id='wst'>加宽词距：关</span>"
           "　·　<span class='toggle clear' id='clear'>清除高亮</span><br>"
           f"<span class='chips'>{chips}</span><br>{html.escape(_review_line(stats, review_info))}"
           "　点任意英文单词，全文同一个词（含中文栏里对应的术语）一起高亮，最多三个；"
           "有底色的句子悬停看原因，点句末小圆点展开说明；标记只是提醒，没标出的也不保证完全正确。</div></header>"]
    from .glossary import load_terms
    terms_all = load_terms()
    ordered = reading_order(doc["segments"], only)
    canon = _canon_table(ordered)
    cdata, inlib = _cite_data(doc, ordered, cite_info)
    refs, bib = (cite_info or {}).get("refs") or {}, doc.get("bib") or {}
    cur_sec = cur_sub = None
    toc, figs = [], []  # 侧栏目录：(级别, 锚点, 中文, 英文)；图表：(锚点, 标签)
    for seg in ordered:
        if seg["section"] != cur_sec:
            cur_sec, cur_sub = seg["section"], None
            anchor = f"s{len(toc)}"
            toc.append((2, anchor, sections_zh.get(cur_sec, ""), cur_sec))
            out.append(f"<h2 class='pair' id='{anchor}'><div class='en'>{html.escape(cur_sec)}</div>"
                       f"<div class='zh'>{html.escape(sections_zh.get(cur_sec, ''))}</div></h2>")
        if seg.get("subsection") and seg["subsection"] != cur_sub:
            cur_sub = seg["subsection"]
            anchor = f"s{len(toc)}"
            toc.append((3, anchor, sections_zh.get(cur_sub, ""), cur_sub))
            out.append(f"<h3 class='pair' id='{anchor}'><div class='en'>{html.escape(cur_sub)}</div>"
                       f"<div class='zh'>{html.escape(sections_zh.get(cur_sub, ''))}</div></h3>")
        kind = seg["kind"]
        if kind == "equation":
            out.append(f"<div class='eq'>$${html.escape(seg.get('math', ''))}$$</div>")
            continue
        en, zh, notes = _pair_html(seg, terms_all, canon, refs, bib)
        note_html = f"<div class='notes'>{''.join(notes)}</div>" if notes else ""
        if kind in ("caption", "table"):
            urls = seg.get("images") or ([seg["image"]] if seg.get("image") else [])
            img = ("<div class='imgs'>" + "".join(f"<img src='{u}' alt=''>" for u in urls) + "</div>") if urls else ""
            anchor = "f-" + re.sub(r"[^A-Za-z0-9_-]", "-", seg["id"])
            m = _LABEL_EN.match(seg["en"])
            if m:
                figs.append((anchor, ("表 " if m.group(1).lower().startswith("tab") else "图 ") + m.group(2)))
            out.append(f"<figure class='para' id='{anchor}'>{img}<div class='pair'><div class='en'>{en}</div>"
                       f"<div class='zh'>{zh}</div>{note_html}</div></figure>")
        elif kind == "footnote":
            n = seg.get("footnote")
            out.append(f"<div class='para fn pair'><div class='en'><sup>{n}</sup> {en}</div>"
                       f"<div class='zh'><sup>{n}</sup> {zh}</div>{note_html}</div>")
        else:
            out.append(f"<div class='para pair'><div class='en'><p>{en}</p></div>"
                       f"<div class='zh'><p>{zh}</p></div>{note_html}</div>")
    out.append(_toc_html(title_zh or doc["title"], toc, figs, inlib))
    out.append("<script id='refdata' type='application/json'>"
               + json.dumps(cdata, ensure_ascii=False).replace("</", "<\\/") + "</script>")
    out.append(JS + ANN_JS + CITE_JS + "</main></body></html>")
    return "\n".join(out)


def _toc_html(title, toc, figs, inlib=()):
    """侧栏目录：各节、小节（中文在前，英文小字），图表快捷跳转，以及本文引用到的库内论文。
    宽屏常驻，窄屏收成右下角按钮。"""
    items = [f"<a href='#top' class='l1'>{html.escape(title)}</a>"]
    for level, anchor, zh, en in toc:
        sub = f"<span class='e'>{html.escape(en)}</span>" if zh else ""
        items.append(f"<a href='#{anchor}' class='l{level}' data-a='{anchor}'>{html.escape(zh or en)}{sub}</a>")
    fig_links = "".join(f"<a href='#{a}'>{html.escape(lab)}</a>" for a, lab in figs)
    figs_html = f"<div class='toc-h'>图表</div><div class='toc-figs'>{fig_links}</div>" if figs else ""
    lib_links = "".join(
        f"<a class='toc-lib' data-b='{_attr(bid)}' title='{_attr(e['title'])}'>{html.escape(e['where'][0] if e['where'] else '库内')}"
        f"<span class='e'>{html.escape(e['title'][:70] or bid)}</span></a>" for bid, e in inlib)
    lib_html = f"<div class='toc-h'>引用了你库里的 {len(inlib)} 篇</div>{lib_links}" if inlib else ""
    return (f"<nav class='toc' id='toc'><div class='toc-h'>目录<span class='toc-x' title='收起'>×</span></div>"
            f"{''.join(items)}{figs_html}{lib_html}</nav>"
            "<button class='toc-btn' id='tocBtn'>目录</button><a class='top-btn' href='#top' title='回到顶部'>↑</a>")
