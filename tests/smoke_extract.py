"""抽取冒烟测试：python tests/smoke_extract.py <arXiv 编号> [...]
检查节标题、超长句（可能分句失败）、过短句、脚注和公式数量。需要先 fetch + extract。"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for pid in sys.argv[1:]:
    d = json.load(open(os.path.join(ROOT, "data", pid, "doc.json"), encoding="utf-8"))
    segs = d["segments"]
    secs = []
    for s in segs:
        if s["section"] not in secs:
            secs.append(s["section"])
    print("=====", pid, d["title"][:70])
    print("  节：", [x[:32] for x in secs][:18])
    sents = [x for s in segs for x in s["sentences"]]
    print("  句子总数：", len(sents))
    long = [x for x in sents if len(x) > 600]
    print("  超长句（可能分句失败）：", len(long))
    for x in long[:2]:
        print("     ", x[:240])
    short = [x for x in sents if len(x) < 15]
    print("  过短句：", short[:15])
    print("  脚注：", [s["en"][:70] for s in segs if s["kind"] == "footnote"][:4])
    inline = sum(len(re.findall(r"\$[^$]+\$", s["en"])) for s in segs)
    print("  行间公式：", sum(1 for s in segs if s["kind"] == "equation"), "行内公式：", inline)
    empty_math = sum(1 for s in segs for m in re.findall(r"\$([^$]*)\$", s["en"]) if not m.strip())
    print("  空公式（alttext 缺失）：", empty_math)
