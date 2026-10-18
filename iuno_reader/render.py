"""把核对后的 doc 渲染成 Obsidian Markdown 和左右两栏 HTML。"""
import datetime
import html
import re

from .check import LEVELS

COLOR = {"red": "#ffc9c9", "orange": "#ffd8a8", "yellow": "#fff3bf", "blue": "#d0ebff"}
ICON = {"red": "🔴", "orange": "🟠", "yellow": "🟡", "blue": "🔵"}
LABEL = {"red": "可能丢了信息", "orange": "翻译没把握或可能漏译", "yellow": "需要留意", "blue": "术语与术语表不一致"}


def _fn(s, md=True):
    return re.sub(r"\{fn(\d+)\}", (r"<sup>[\1]</sup>" if md else r"<sup>[\1]</sup>"), s)


def _attr(s):
    return html.escape(s, quote=True).replace("\n", " ")


def _review_line(stats):
    names = {"blind": "盲审", "backtranslate": "回译比对"}
    parts = [f"{names.get(k[9:], k[9:])} {v} 句" for k, v in stats.items()
             if k.startswith("reviewed_") and k != "reviewed_stale"]
    if not parts:
        return "还没有经过独立审校：没有提醒的句子只表示规则没发现问题。"
    line = "已审校：" + "，".join(parts)
    if stats.get("reviewed_stale"):
        line += f"；另有 {stats['reviewed_stale']} 句在审校后改过译文，旧意见已作废"
    return line + "。审校意见在提醒里以 [审校者] 开头。"


def _legend_md(stats):
    review = _review_line(stats)
    stats = {k: v for k, v in stats.items() if not k.startswith("reviewed_")}
    total = sum(stats.values())
    parts = [f'<mark style="background:{COLOR[lv]}">{ICON[lv]} {LABEL[lv]}</mark>' for lv in LEVELS]
    counts = "，".join(f"{ICON[lv]} {stats.get(lv, 0)}" for lv in LEVELS)
    return ("> [!info] 怎么读\n"
            "> 每段先列英文原文，下面是中文译文。需要核对的句子按颜色高亮，鼠标悬停可以看到原因，"
            "每段下面的折叠框里有逐句对照。\n>\n> " + " ".join(parts) + "\n>\n"
            f"> 本文共 {total} 句：没有提醒 {stats.get('ok', 0)}，{counts}。\n"
            f"> {review}\n"
            "> 这些标记来自自动规则和翻译者自评，只是提醒：标出来的要看一眼，没标出来的也不保证完全正确。\n")


def _zh_md(pairs):
    out = []
    for i, p in enumerate(pairs, 1):
        zh = _fn(p["zh"] or "（缺译文）")
        if p["level"]:
            tip = "；".join(f"{ICON[f['level']]} {f['msg']}" for f in p["flags"])
            zh = f'<mark style="background:{COLOR[p["level"]]}" title="{_attr(tip)}">{zh}</mark><sup>{i}</sup>'
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
        items.append(f"> - **第 {i} 句**：{reasons}\n>     - 原文：{_fn(p['en'])}\n>     - 译文：{_fn(p['zh'])}")
    if not items:
        return ""
    worst = min([f["level"] for f in seg.get("seg_flags", [])] + [p["level"] for p in seg["pairs"] if p["level"]],
                key=LEVELS.index)
    kind = {"red": "danger", "orange": "warning", "yellow": "caution", "blue": "note"}[worst]
    return f"> [!{kind}]- {len(items)} 处需要核对\n" + "\n".join(items) + "\n"


def render_markdown(doc, stats, sections_zh=None, only=None):
    sections_zh = sections_zh or {}
    lines = [
        "---",
        f"arxiv: \"{doc['arxiv_id']}\"",
        f"title: \"{doc['title']}\"",
        f"source: {doc['source']}",
        f"generated: {datetime.date.today().isoformat()}",
        "tool: iuno_reader",
        "---", "",
        f"# {doc['title']}", "",
        f"原文：https://arxiv.org/abs/{doc['arxiv_id']} ｜ HTML：https://arxiv.org/html/{doc['arxiv_id']}", "",
        _legend_md(stats),
    ]
    cur_sec, cur_sub = None, None
    for seg in doc["segments"]:
        if only is not None and seg["id"] not in only:
            continue
        if seg["section"] != cur_sec:
            cur_sec, cur_sub = seg["section"], None
            zh = sections_zh.get(cur_sec)
            lines += [f"## {cur_sec}" + (f" · {zh}" if zh else ""), ""]
        if seg.get("subsection") and seg["subsection"] != cur_sub:
            cur_sub = seg["subsection"]
            zh = sections_zh.get(cur_sub)
            lines += [f"### {cur_sub}" + (f" · {zh}" if zh else ""), ""]
        kind = seg["kind"]
        tag = {"caption": "图注", "table": "表格", "footnote": "脚注", "item": "列表项", "abstract": "摘要",
               "para": "段落", "equation": "公式"}.get(kind, kind)
        if kind == "equation":
            lines += [f"$${seg.get('math', '')}$$" + (f" {seg.get('eqtag')}" if seg.get("eqtag") else ""), ""]
            continue
        lines.append(f"<small>¶ {tag} · {seg['id']}</small>")
        lines.append("")
        if kind in ("caption",) and seg.get("image"):
            lines += [f"![]({seg['image']})", ""]
        en = _fn(seg["en"])
        if kind == "footnote":
            en = f"[{seg.get('footnote')}] " + en
        lines += [en, ""]
        if not seg.get("pairs"):
            lines += ["*（这一段还没有翻译）*", ""]
            continue
        zh = _zh_md(seg["pairs"])
        if kind == "footnote":
            zh = f"[{seg.get('footnote')}] " + zh
        lines += [f"**译**　{zh}", ""]
        if kind == "table":
            lines += ["*（表格内容见原文）*", ""]
        fl = _flags_md(seg)
        if fl:
            lines += [fl]
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def _h(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return _fn(s, md=False)


CSS = """
body{font-family:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif;color:#1f2328;background:#f6f7f9;margin:0}
main{max-width:1400px;margin:0 auto;padding:24px}
h1{font-size:24px}h2{margin-top:36px;border-bottom:1px solid #e3e5e8;padding-bottom:6px}h3{color:#3b915d}
.row{display:grid;grid-template-columns:1fr 1fr;gap:20px;background:#fff;border:1px solid #e3e5e8;border-radius:10px;padding:14px 18px;margin:12px 0}
.row .meta{grid-column:1/3;color:#6b7280;font-size:12px}
.en{line-height:1.7}.zh{line-height:1.9}
.s{border-radius:3px;padding:1px 0}.s:hover{outline:1px solid #6b7280;cursor:help}
.flags{grid-column:1/3;font-size:13px;color:#374151;border-top:1px dashed #e3e5e8;padding-top:8px}
.flags li{margin:4px 0}.legend span{padding:2px 8px;border-radius:4px;margin-right:6px}
img{max-width:100%;grid-column:1/3}
.eq{grid-column:1/3;text-align:center;overflow-x:auto}
"""


def render_html(doc, stats, sections_zh=None, only=None):
    sections_zh = sections_zh or {}
    review = _review_line(stats)
    stats = {k: v for k, v in stats.items() if not k.startswith("reviewed_")}
    total = sum(stats.values())
    legend ="".join(f'<span style="background:{COLOR[lv]}">{ICON[lv]} {LABEL[lv]} {stats.get(lv, 0)}</span>' for lv in LEVELS)
    out = [f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><title>{html.escape(doc['title'])}</title>",
           f"<style>{CSS}</style>",
           "<script>window.MathJax={tex:{inlineMath:[['$','$']],displayMath:[['$$','$$']]}};</script>",
           "<script src='https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js' async></script>",
           "</head><body><main>",
           f"<h1>{html.escape(doc['title'])}</h1>",
           f"<p><a href='https://arxiv.org/abs/{doc['arxiv_id']}'>arXiv {doc['arxiv_id']}</a> · 共 {total} 句，没有提醒 {stats.get('ok', 0)}</p>",
           f"<p class='legend'>{legend}</p>",
           f"<p style='font-size:13px'>{html.escape(review)}</p>",
           "<p style='color:#6b7280;font-size:13px'>把鼠标移到高亮的句子上，可以看到原因，对应的原文句子也会同时高亮。标记只是提醒，不保证没标出的句子完全正确。</p>"]
    cur_sec, cur_sub = None, None
    for seg in doc["segments"]:
        if only is not None and seg["id"] not in only:
            continue
        if seg["section"] != cur_sec:
            cur_sec, cur_sub = seg["section"], None
            zh = sections_zh.get(cur_sec)
            out.append(f"<h2>{html.escape(cur_sec)}{' · ' + html.escape(zh) if zh else ''}</h2>")
        if seg.get("subsection") and seg["subsection"] != cur_sub:
            cur_sub = seg["subsection"]
            zh = sections_zh.get(cur_sub)
            out.append(f"<h3>{html.escape(cur_sub)}{' · ' + html.escape(zh) if zh else ''}</h3>")
        if seg["kind"] == "equation":
            out.append(f"<div class='row'><div class='eq'>$${html.escape(seg.get('math', ''))}$$ {html.escape(seg.get('eqtag', ''))}</div></div>")
            continue
        en_parts, zh_parts, flags = [], [], []
        pairs = seg.get("pairs") or [{"en": s, "zh": "", "level": None, "flags": []} for s in seg["sentences"]]
        for i, p in enumerate(pairs, 1):
            sid = f"{seg['id']}-{i}"
            style = f" style='background:{COLOR[p['level']]}'" if p["level"] else ""
            tip = "；".join(f"{ICON[f['level']]} {f['msg']}" for f in p["flags"])
            title = f" title='{_attr(tip)}'" if tip else ""
            en_parts.append(f"<span class='s' data-s='{sid}'{style}>{_h(p['en'])}</span> ")
            zh_parts.append(f"<span class='s' data-s='{sid}'{style}{title}>{_h(p['zh']) if p['zh'] else ''}</span>")
            if p["level"]:
                flags.append(f"<li><b>第 {i} 句</b>：{html.escape(tip)}</li>")
        for f in seg.get("seg_flags", []):
            flags.insert(0, f"<li><b>整段</b>：{ICON[f['level']]} {html.escape(f['msg'])}</li>")
        img = f"<img src='{seg['image']}' loading='lazy'>" if seg.get("image") else ""
        zh_html = "".join(zh_parts) if seg.get("pairs") else "<i style='color:#6b7280'>（这一段还没有翻译）</i>"
        out.append(f"<div class='row'><div class='meta'>{seg['kind']} · {seg['id']}</div>{img}"
                   f"<div class='en'>{''.join(en_parts)}</div><div class='zh'>{zh_html}</div>"
                   + (f"<ul class='flags'>{''.join(flags)}</ul>" if flags else "") + "</div>")
    out.append("""<script>
document.querySelectorAll('.s').forEach(el=>{
  el.addEventListener('mouseenter',()=>document.querySelectorAll(`[data-s="${el.dataset.s}"]`).forEach(x=>x.style.outline='2px solid #3b915d'));
  el.addEventListener('mouseleave',()=>document.querySelectorAll(`[data-s="${el.dataset.s}"]`).forEach(x=>x.style.outline=''));
});</script></main></body></html>""")
    return "\n".join(out)
