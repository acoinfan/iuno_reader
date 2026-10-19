"""流水线离线测试：python tests/test_pipeline.py
在临时目录里复制代码所需的提示词、术语表和已缓存的 HTML，覆盖三种状态：
等待翻译 → （补上译文）等待审校 → （导入审校）完成。需要 cache/2602.04212.html 已存在。"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for k in list(os.environ):
    if k.startswith("IUNO_"):
        del os.environ[k]  # 确保不会连接任何接口

from iuno_reader import images, pipeline  # noqa: E402
from iuno_reader.pipeline import run  # noqa: E402

pipeline.fix_images = lambda doc, path: []  # 离线测试：不下载图片检查

# 0. 图片检查（不联网）：完整 / 截断的 PNG、截断的 SVG；v3 坏了退回 v2
import struct, zlib  # noqa: E402,E401


def _png(w=4, h=4):
    raw = b"".join(b"\x00" + b"\xff" * (3 * w) for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))  # noqa: E731
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


good = _png(64, 64)
assert images.intact(good, "a.png") and not images.intact(good[:60], "a.png")
assert images.intact(b"<svg>x</svg>\n", "a.svg") and not images.intact(b"<svg><g>", "a.svg")
fake = {"https://arxiv.org/html/1234.5678v3/f.png": good[:60], "https://arxiv.org/html/1234.5678v2/f.png": good}
images._get = lambda u, timeout=30: fake[u]
assert images.resolve("https://arxiv.org/html/1234.5678v3/f.png") == ("https://arxiv.org/html/1234.5678v2/f.png", True)
print("图片检查：截断识别、退回旧版本 通过")

PID = "2602.04212"
tmp = tempfile.mkdtemp(prefix="iuno_reader_test_")
try:
    for d in ("prompts", "glossary"):
        shutil.copytree(os.path.join(ROOT, d), os.path.join(tmp, d))
    os.makedirs(os.path.join(tmp, "cache"))
    shutil.copy(os.path.join(ROOT, "cache", f"{PID}.html"), os.path.join(tmp, "cache", f"{PID}.html"))

    # 1. 没有译文 → 等待翻译，生成任务说明
    m = run(tmp, PID, sections=["2"], translate="auto", review="auto")
    assert m["stage"] == "awaiting_translation", m["stage"]
    out = os.path.join(tmp, "out", PID)
    assert os.path.exists(os.path.join(out, "AGENT_TRANSLATE.md"))
    tasks = json.load(open(os.path.join(tmp, "data", PID, "tasks.json"), encoding="utf-8"))
    # 除了第 2 节的片段，只允许出现被第 2 节引用的图表；图 2 在 HTML 里浮动到了第 1 节，但必须被带上
    assert tasks and all(t["section"].startswith("2 ") or t["kind"] in ("caption", "table", "footnote") for t in tasks)
    assert "S1.F2" in {t["id"] for t in tasks}, "第 2 节引用了图 2，图 2 应被一起选上"
    for f in ("paper.zh.md", "paper.zh.html", "paper.en.md", "flags.md", "checked.json", "manifest.json"):
        assert os.path.exists(os.path.join(out, f)), f
    print("等待翻译：任务说明与输出文件齐全 通过")

    # 2. 用真实译文中第 2 节的部分补上 → 等待审校
    real = json.load(open(os.path.join(ROOT, "data", PID, "translation.json"), encoding="utf-8"))
    tr = {"__meta__": {"translator": "test-translator"}}
    for t in tasks:
        tr[t["id"]] = real[t["id"]]
    json.dump(tr, open(os.path.join(tmp, "data", PID, "translation.json"), "w", encoding="utf-8"), ensure_ascii=False)
    m = run(tmp, PID, sections=["2"])
    assert m["stage"] == "awaiting_review", m["stage"]
    assert not os.path.exists(os.path.join(out, "AGENT_TRANSLATE.md")), "翻译完成后应删除翻译任务说明"
    assert os.path.exists(os.path.join(out, "AGENT_REVIEW.md"))
    rtasks = json.load(open(os.path.join(tmp, "data", PID, "review_tasks.json"), encoding="utf-8"))
    assert all(set(p) == {"en", "zh"} for t in rtasks for p in t["pairs"]), "审校清单每对只能有 en 和 zh"
    notes = [s["note"] for t in tasks for s in real[t["id"]]["sentences"] if s.get("note")]
    dump = json.dumps(rtasks, ensure_ascii=False)
    assert notes and not any(n in dump for n in notes), "审校清单不能含译者备注"
    print("等待审校：翻译说明已删除、审校清单不泄露备注 通过")

    # 3. 导入一份全部 ok 的审校（声明为不同模型）→ 完成
    raw = {t["id"]: {"reviewer": "test", "mode": "blind",
                     "reviews": [{"verdict": "ok", "category": [], "issue": "", "suggestion": ""} for _ in t["pairs"]]}
           for t in rtasks}
    raw_path = os.path.join(tmp, "data", PID, "review.agent.raw.json")
    json.dump(raw, open(raw_path, "w", encoding="utf-8"))
    from iuno_reader import cli
    cli.ROOT = tmp
    try:
        cli.main(["import-review", PID, raw_path, "--name", "test-reviewer", "--reviewer-model", "other-model"])
    except SystemExit:
        pass
    m = run(tmp, PID, sections=["2"])
    assert m["stage"] == "complete", m["stage"]
    assert not os.path.exists(os.path.join(out, "AGENT_REVIEW.md"))
    rv = m["stages"]["review"]["reviewers"][0]
    assert rv["independence"] == "different-model", rv
    print("完成：导入审校后阶段为 complete、独立性为不同模型 通过")

    # 4. 导出
    exp = os.path.join(tmp, "export")
    m = run(tmp, PID, sections=["2"], export=exp)
    assert os.path.exists(os.path.join(exp, PID, "paper.zh.md")) and m["exported_to"].endswith(PID)
    print("导出 通过")

    # 5. 只翻正文：Appendix 开头的节、罗马数字重新编号的节都算附录；正文引用的附录图仍会选上
    from iuno_reader.translate import select_segments

    def seg(i, sec, kind="para", en="x."):
        return {"id": i, "kind": kind, "section": sec, "en": en, "sentences": [en]}
    d1 = {"segments": [seg("a", "", "abstract"), seg("p1", "1 Intro", en="See Figure 9."), seg("p2", "2 Method"),
                       seg("x1", "Appendix A Details"), seg("x2", "Appendix B More"),
                       {**seg("F9", "Appendix B More", "caption", "Figure 9: A plot."), "figure": 9}]}
    got = {s["id"] for s in select_segments(d1, ["body"])}
    assert {"a", "p1", "p2", "F9"} <= got and not {"x1", "x2"} & got, got
    d2 = {"segments": [seg("a", "", "abstract"), seg("p1", "I Introduction"), seg("p2", "II Methods"),
                       seg("x1", "I Details on systems"), seg("x2", "II Additional analysis")]}
    got = {s["id"] for s in select_segments(d2, ["body"])}
    assert got == {"a", "p1", "p2"}, got
    print("只翻正文（含罗马数字编号的附录） 通过")
    print("流水线测试全部通过")
finally:
    shutil.rmtree(tmp)
