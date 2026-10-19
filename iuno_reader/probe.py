"""接口探测：给每个配好接口的角色发一个很小的固定样例，看这家接口能不能用、好不好用。

用法：python -m iuno_reader probe [--roles translator,reviewer,backtranslator,bt_judge]

每个角色只发一次请求（出错时最多重试两次），用量很小。报告：
- 能不能连上、JSON 模式支不支持（不支持会自动退回普通模式）、返回条数对不对、耗时和 token；
- 翻译：译文用规则核对一遍（否定、数字、公式、占位符、限定词）；
- 审校：样例里第 2、3 句是故意译错的（限定词被加强、数字写错），看审校能不能抓出来，第 1 句应判 ok。
结果另存一份到 out/probe/<模型>-<时间>.json，方便比较不同接口。
"""
import json
import os
import time
from urllib.parse import urlparse

from .check import check_sentence
from .glossary import load_hedges
from .roles import ROOT, load_prompt, model_config, payload_backtranslator, payload_bt_judge, payload_reviewer
from .translate import OpenAICompatible

SAMPLE = {
    "paper": "Language Models Struggle to Use Representations Learned In-Context",
    "section": "1 Introduction", "subsection": "",
    "previous_paragraph": "", "terms": {"in-context representation": "上下文表示"},
    "sentences": [
        "We find that open-weights models do not reliably use in-context representations, even when they encode them.",
        "This suggests, but does not prove, that the failure lies in deployment rather than encoding.",
        "Accuracy drops from 87.5% to 41.2% when $k \\ge 3$ shots are given{fn1}.",
    ],
}
# 审校样例：第 1 句译对；第 2 句把 suggests, but does not prove 译成「证明」；第 3 句数字写错
SAMPLE_ZH = [
    "我们发现，开放权重模型并不能可靠地利用上下文表示，即使它们编码了这些表示。",
    "这证明了失败出在部署而非编码。",
    "当给出 $k \\ge 3$ 个示例时，准确率从 87.5% 降至 42.1%{fn1}。",
]
EXPECT = ["ok", "bad", "bad"]


_engine = OpenAICompatible


def _summary(eng, t0, ok, detail):
    return {"role": eng.role, "model": eng.model, "host": urlparse(eng.base_url).netloc,
            "ok": ok, "json_mode": eng.json_mode, "seconds": round(time.time() - t0, 1),
            "usage": eng.usage, **detail}


def probe_translator():
    eng, t0 = _engine("translator"), time.time()
    try:
        out = eng.translate(SAMPLE, retries=2)["sentences"]
    except Exception as e:
        return _summary(eng, t0, False, {"error": str(e)})
    failed = [s["note"] for s in out if not s["zh"]]
    if failed:
        return _summary(eng, t0, False, {"error": failed[0]})
    hedges = load_hedges()
    checks = []
    for en, s in zip(SAMPLE["sentences"], out):
        flags = check_sentence(en, s["zh"], {}, hedges, s.get("conf"), s.get("note", ""))
        checks.append({"zh": s["zh"], "conf": s.get("conf"), "note": s.get("note", ""),
                       "flags": [f"{f['level']}:{f['code']}" for f in flags if f["level"] in ("red", "orange")]})
    return _summary(eng, t0, True, {"sentences": checks,
                                    "rule_red": sum(any(x.startswith("red") for x in c["flags"]) for c in checks)})


def _review_task():
    return {"section": SAMPLE["section"], "pairs": [{"en": e, "zh": z} for e, z in zip(SAMPLE["sentences"], SAMPLE_ZH)]}


def _score(reviews):
    got = ["ok" if (r.get("verdict") or "ok") == "ok" else "bad" for r in reviews]
    return {"verdicts": [r.get("verdict") for r in reviews], "issues": [r.get("issue", "") for r in reviews],
            "caught": f"{sum(g == e for g, e in zip(got, EXPECT))}/3"}


def probe_reviewer():
    eng, t0 = _engine("reviewer"), time.time()
    try:
        out = eng.ask_json(load_prompt("reviewer"), payload_reviewer(_review_task()), "reviews", 3)
    except Exception as e:
        return _summary(eng, t0, False, {"error": str(e)})
    return _summary(eng, t0, True, _score(out))


def probe_backtranslate():
    bt, judge, t0 = _engine("backtranslator"), _engine("bt_judge"), time.time()
    task = _review_task()
    try:
        back = bt.ask_json(load_prompt("backtranslator"), payload_backtranslator(task), "sentences", 3)
        out = judge.ask_json(load_prompt("bt_judge"), payload_bt_judge(task, back), "reviews", 3)
    except Exception as e:
        return _summary(bt, t0, False, {"error": str(e), "judge_model": judge.model})
    res = _summary(bt, t0, True, {"judge_model": judge.model, "backtranslations": back, **_score(out)})
    res["role"], res["usage_judge"] = "backtranslator+bt_judge", judge.usage
    return res


PROBES = {"translator": probe_translator, "reviewer": probe_reviewer, "backtranslate": probe_backtranslate}
NEEDS = {"translator": "translator", "reviewer": "reviewer", "backtranslate": "backtranslator"}


def run(roles=None):
    roles = roles or list(PROBES)
    results = []
    for r in roles:
        if not all(model_config(NEEDS[r])):
            print(f"== {r}：没有配置接口，跳过")
            continue
        res = PROBES[r]()
        results.append(res)
        print(f"== {r}　{res['model']} @ {res['host']}　{'可用' if res['ok'] else '失败'}　"
              f"JSON 模式：{'支持' if res['json_mode'] else '不支持（已退回普通模式）'}　{res['seconds']} 秒　"
              f"token：输入 {res['usage']['prompt_tokens']} / 输出 {res['usage']['completion_tokens']}")
        if not res["ok"]:
            print("   错误：", res["error"])
        elif r == "translator":
            for i, s in enumerate(res["sentences"], 1):
                print(f"   {i}. [{s['conf']}] {s['zh']}" + (f"　规则标记：{s['flags']}" if s["flags"] else "")
                      + (f"　备注：{s['note']}" if s["note"] else ""))
        else:
            if r == "backtranslate":
                print("   比对模型：", res["judge_model"])
                for i, b in enumerate(res["backtranslations"], 1):
                    print(f"   回译 {i}. {b}")
            print(f"   判定：{res['verdicts']}（期望 ok、错、错，抓对 {res['caught']}）")
            for i, s in enumerate(res["issues"], 1):
                if s:
                    print(f"   {i}. {s}")
    if results:
        os.makedirs(os.path.join(ROOT, "out", "probe"), exist_ok=True)
        name = results[0]["model"].replace("/", "_") + time.strftime("-%Y%m%d-%H%M%S") + ".json"
        path = os.path.join(ROOT, "out", "probe", name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        print("报告：", path)
    return results
