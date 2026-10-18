"""术语表与限定词表。

terms.tsv：英文<TAB>中文（多个可接受译法用 / 分隔）<TAB>备注
hedges.tsv：英文正则<TAB>可接受的中文（/ 分隔）<TAB>禁止出现的中文（/ 分隔，表示语气被加强）<TAB>说明
"""
import csv
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(os.path.dirname(HERE), "glossary")


def _split_opts(s):
    return [x.strip() for x in re.split(r"[/／]", s or "") if x.strip()]


def _split_zh_cell(s):
    """中文单元格：先去掉括号说明，再按 / ／ 、 拆成多个可接受译法。"""
    s = re.sub(r"[（(][^）)]*[）)]", "", s or "")
    return [x.strip() for x in re.split(r"[/／、，,]", s) if x.strip()]


def load_terms(path=None):
    path = path or os.path.join(DEFAULT_DIR, "terms.tsv")
    terms = {}
    if not os.path.exists(path):
        return terms
    with open(path, encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="\t"):
            if not row or row[0].startswith("#") or len(row) < 2:
                continue
            terms[row[0].strip()] = {"zh": _split_opts(row[1]), "note": row[2].strip() if len(row) > 2 else ""}
    return terms


def load_hedges(path=None):
    path = path or os.path.join(DEFAULT_DIR, "hedges.tsv")
    hedges = []
    with open(path, encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="\t"):
            if not row or row[0].startswith("#") or len(row) < 2:
                continue
            hedges.append({
                "en": re.compile(r"\b(?:%s)\b" % row[0], re.I),
                "en_src": row[0],
                "zh_ok": _split_opts(row[1]),
                "zh_forbid": _split_opts(row[2]) if len(row) > 2 else [],
                "note": row[3] if len(row) > 3 else "",
            })
    return hedges


def import_kb_glossary(md_path):
    """从 Research-KB 的 glossary.md 读出「英文 | 中文」表格行。

    - 「few-shot / zero-shot | 少量示例／零样本」这种成对的，按位置一一对应；
    - 「linear probe / probing | 线性探针」这种只有一个中文的，几个英文都对应它；
    - 去掉括号里的缩写，例如「Dirichlet Energy (DE)」得到 Dirichlet Energy 和 DE。
    """
    out = {}
    zh_col = None  # 当前表格里「中文」所在的列；None 表示不在可用的表格里
    with open(md_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("|"):
                zh_col = None
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 2 or set(cells[0]) <= set("-: "):
                continue
            if cells[0] == "英文":
                # 限定词表（有「强弱」列）交给 hedges.tsv 做更细的检查，这里跳过
                zh_col = cells.index("中文") if "中文" in cells and "强弱" not in cells else None
                continue
            if zh_col is None or zh_col >= len(cells):
                continue
            en_cell, zh_cell = cells[0], cells[zh_col]
            if not re.search(r"[A-Za-z]", en_cell) or not re.search(r"[一-鿿]", zh_cell):
                continue
            if re.search(r"\b[AB]\b", en_cell):  # 「disentangle A from B」这类带变量的句式不收
                continue
            ens = [e.strip() for e in en_cell.split("/") if e.strip()]
            # 成对的条目（如 few-shot / zero-shot | 少量示例／零样本）按 / 一一对应
            pair_zh = [_split_zh_cell(z) for z in _split_opts(re.sub(r"[（(][^）)]*[）)]", "", zh_cell))]
            for i, e in enumerate(ens):
                abbrev = re.findall(r"\(([A-Za-z]+)\)", e)
                base = re.sub(r"\s*\([^)]*\)", "", e).strip()
                if len(pair_zh) == len(ens) and len(ens) > 1:
                    zh = pair_zh[i]
                else:
                    zh = _split_zh_cell(zh_cell)
                for key in [base] + abbrev:
                    if key and len(key) > 1 and zh:
                        out[key] = {"zh": zh, "note": "来自 Research-KB glossary.md"}
    return out


def write_terms(terms, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 英文\t中文（可接受译法用 / 分隔）\t备注\n")
        for en in sorted(terms, key=str.lower):
            t = terms[en]
            f.write(f"{en}\t{'/'.join(t['zh'])}\t{t.get('note', '')}\n")


def terms_in(text, terms):
    """返回在英文 text 里出现的术语（按长度优先，避免短词抢先匹配）。"""
    found = []
    low = text.lower()
    for en in sorted(terms, key=len, reverse=True):
        pat = r"(?<![A-Za-z])" + re.escape(en.lower()) + r"s?(?![A-Za-z])"
        if re.search(pat, low):
            if any(en.lower() in f.lower() for f in found):
                continue
            found.append(en)
    return found
