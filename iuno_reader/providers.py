"""接口、翻译方案和价格：给网页前端（server.py）用。

- 接口：从 cc-switch 读（只读），加上自己添加的（存在 <配置目录>/providers.json）。
  cc-switch 的 Claude 一栏存的是 Anthropic 格式地址，按域名换算成同一家的 OpenAI 兼容地址。
- 翻译方案：内置几档，加上自己保存的（<配置目录>/presets.json）。
- 价格：内置一张表（元 / 百万 token），可以用 <配置目录>/prices.json 覆盖。
配置目录默认 ~/.config/iuno_reader（环境变量 IUNO_READER_HOME 可改），里面有密钥，文件权限设为只有自己能读写。
密钥只在本机用，不会通过接口返回给网页（网页只看到末尾四位）。
"""
import json
import os
import re
import sqlite3
import time
from urllib.parse import urlparse

HOME = os.path.expanduser(os.environ.get("IUNO_READER_HOME", "~/.config/iuno_reader"))
CCSWITCH_DB = os.path.expanduser(os.environ.get("IUNO_CCSWITCH_DB", "~/.cc-switch/cc-switch.db"))

OPENAI_BASE = {
    "api.deepseek.com": "https://api.deepseek.com/v1",
    "dashscope.aliyuncs.com": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api.moonshot.cn": "https://api.moonshot.cn/v1",
    "open.bigmodel.cn": "https://open.bigmodel.cn/api/paas/v4",
    "api.siliconflow.cn": "https://api.siliconflow.cn/v1",
    "openrouter.ai": "https://openrouter.ai/api/v1",
    "generativelanguage.googleapis.com": "https://generativelanguage.googleapis.com/v1beta/openai",
    "api.openai.com": "https://api.openai.com/v1",
}
# 添加接口时可选的服务商
VENDORS = [
    {"name": "DeepSeek", "base": "https://api.deepseek.com/v1"},
    {"name": "Kimi（月之暗面）", "base": "https://api.moonshot.cn/v1"},
    {"name": "通义（阿里云百炼）", "base": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    {"name": "智谱 GLM", "base": "https://open.bigmodel.cn/api/paas/v4"},
    {"name": "硅基流动", "base": "https://api.siliconflow.cn/v1"},
    {"name": "OpenRouter", "base": "https://openrouter.ai/api/v1"},
    {"name": "Gemini（宿主连不上，需另想办法）", "base": "https://generativelanguage.googleapis.com/v1beta/openai"},
    {"name": "OpenAI（宿主连不上，需另想办法）", "base": "https://api.openai.com/v1"},
]

# 元 / 百万 token：(输入缓存命中, 输入未命中, 输出, 是否分高峰时段)。DeepSeek 高峰价翻倍。2026-10-19 官方价格页
PRICES = {
    "deepseek-v4-pro": [0.15, 4.5, 13.5, True],
    "deepseek-flash": [0.02, 1, 4, True],
    "deepseek-v4-flash": [0.02, 1, 4, True],
    "kimi-k3": [2, 20, 100, False],
    "kimi-k2.6": [1.1, 6.5, 27, False],
}

THINKING = {  # 思考档位 → 附加请求参数（目前按 DeepSeek 的写法；别家不认会被客户端自动去掉）
    "default": {},
    "off": {"thinking": {"type": "disabled"}},
    "low": {"reasoning_effort": "low"},
    "high": {"reasoning_effort": "high"},
}

BUILTIN_PRESETS = [
    {"name": "尤诺推荐", "builtin": True, "scope": "body", "offpeak_only": False,
     "note": "pro 关思考翻译 + pro 盲审；一篇正文约 ¥1～2",
     "translator": {"provider": "deepseek-1", "model": "deepseek-v4-pro", "thinking": "off", "workers": 6},
     "reviewer": {"provider": "deepseek-1", "model": "deepseek-v4-pro", "thinking": "default", "workers": 6},
     "reviewer2": None},
    {"name": "尤诺推荐 · 精读", "builtin": True, "scope": "body", "offpeak_only": False,
     "note": "再加 Kimi K3 换一家审一遍，能抓到 DeepSeek 自己看不出的问题；每篇多 ¥4～8",
     "translator": {"provider": "deepseek-1", "model": "deepseek-v4-pro", "thinking": "off", "workers": 6},
     "reviewer": {"provider": "deepseek-1", "model": "deepseek-v4-pro", "thinking": "default", "workers": 6},
     "reviewer2": {"provider": "kimi", "model": "kimi-k3", "thinking": "default", "workers": 5}},
    {"name": "尤诺推荐 · 略读", "builtin": True, "scope": "skim", "offpeak_only": False,
     "note": "同推荐档，但只翻摘要、引言、讨论和结论，不翻方法和实验；约为正文的三成，一篇约 ¥0.3～0.6",
     "translator": {"provider": "deepseek-1", "model": "deepseek-v4-pro", "thinking": "off", "workers": 6},
     "reviewer": {"provider": "deepseek-1", "model": "deepseek-v4-pro", "thinking": "default", "workers": 6},
     "reviewer2": None},
    {"name": "最省钱", "builtin": True, "scope": "body", "offpeak_only": True,
     "note": "flash 关思考翻译 + pro 盲审，只在空闲时段跑；质量略低于推荐档",
     "translator": {"provider": "deepseek-1", "model": "deepseek-flash", "thinking": "off", "workers": 8},
     "reviewer": {"provider": "deepseek-1", "model": "deepseek-v4-pro", "thinking": "default", "workers": 6},
     "reviewer2": None},
]


def _path(name):
    return os.path.join(HOME, name)


def _load(name, default):
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(name, obj):
    os.makedirs(HOME, mode=0o700, exist_ok=True)
    p = _path(name)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.chmod(p + ".tmp", 0o600)
    os.replace(p + ".tmp", p)


# ---------- 接口 ----------

def _walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _walk(v, f"{path}.{k}" if path else k)
    else:
        yield path, o


def _ccswitch():
    if not os.path.exists(CCSWITCH_DB):
        return []
    out = []
    try:
        db = sqlite3.connect(f"file:{CCSWITCH_DB}?mode=ro", uri=True)
        rows = db.execute("select id, app_type, name, settings_config from providers").fetchall()
    except sqlite3.Error:
        return []
    for pid, app, name, cfg in rows:
        try:
            cfg = json.loads(cfg or "{}")
        except ValueError:
            cfg = {}
        flat = dict(_walk(cfg))
        toml = cfg.get("config") if isinstance(cfg.get("config"), str) else ""
        tv = dict(re.findall(r'^\s*(base_url|model)\s*=\s*"([^"]*)"', toml, re.M))
        key = next((v for k, v in flat.items() if isinstance(v, str) and v and
                    re.search(r"(API_KEY|AUTH_TOKEN|apiKey)$", k) and not k.startswith("auth.tokens")), "")
        base = tv.get("base_url") or next((v for k, v in flat.items() if isinstance(v, str) and v and
                                          re.search(r"BASE_URL$", k)), "")
        if not (key and base):  # 官方登录那几条没有接口密钥，跳过
            continue
        out.append({"id": pid, "name": f"{name}（{app}）", "source": "cc-switch",
                    "base": OPENAI_BASE.get(urlparse(base).netloc, base), "key": key})
    return out


def providers(with_keys=False):
    """全部接口。with_keys=False 时密钥只留末尾四位，给网页看。"""
    items = _ccswitch() + [dict(p, source="自己添加") for p in _load("providers.json", [])]
    if with_keys:
        return items
    return [{k: v for k, v in p.items() if k != "key"} | {"key_tail": (p.get("key") or "")[-4:]} for p in items]


def provider(pid):
    return next((p for p in providers(with_keys=True) if p["id"] == pid), None)


def add_provider(name, base, key, model=""):
    items = _load("providers.json", [])
    pid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "api"
    taken = {p["id"] for p in providers(with_keys=True)}
    n, base_id = 2, pid
    while pid in taken:
        pid, n = f"{base_id}-{n}", n + 1
    items.append({"id": pid, "name": name, "base": base.rstrip("/"), "key": key, "model": model})
    _save("providers.json", items)
    return pid


def delete_provider(pid):
    _save("providers.json", [p for p in _load("providers.json", []) if p["id"] != pid])


# ---------- 方案 ----------

def presets():
    return BUILTIN_PRESETS + [dict(p, builtin=False) for p in _load("presets.json", [])]


def preset(name):
    return next((p for p in presets() if p["name"] == name), None)


def save_preset(p):
    if any(b["name"] == p["name"] for b in BUILTIN_PRESETS):
        raise ValueError("和内置方案重名了，换个名字")
    items = [x for x in _load("presets.json", []) if x["name"] != p["name"]]
    items.append({k: v for k, v in p.items() if k != "builtin"})
    _save("presets.json", items)


def delete_preset(name):
    _save("presets.json", [x for x in _load("presets.json", []) if x["name"] != name])


def r2_name(role):
    """审校二的结果文件名：review.<模型名>.json。"""
    return re.sub(r"[^a-z0-9.]+", "-", role["model"].lower()).strip("-")


def preset_steps(pid, p):
    """按方案跑一篇要的几步子进程：[(说明, 命令行参数, 环境变量)]。接口配置有问题时抛 ValueError。
    前端的任务队列和 kb translate 都用它。"""
    scope = p.get("scope", "body")
    env = role_env("IUNO_TRANSLATE", p["translator"])
    review = "none"
    if p.get("reviewer"):
        env |= role_env("IUNO_REVIEW", p["reviewer"])
        review = "blind"
    rw = str((p.get("reviewer") or {}).get("workers", 1))
    steps = [("翻译和审校一", ["run", pid, "--sections", scope, "--translate", "api", "--review", review,
                              "--workers", str(p["translator"].get("workers", 1)), "--review-workers", rw], env)]
    r2 = p.get("reviewer2")
    if r2:
        steps += [("审校二", ["review", pid, "--mode", "blind", "--name", r2_name(r2), "--sections", scope,
                             "--workers", str(r2.get("workers", 1))], role_env("IUNO_REVIEW", r2)),
                  ("重新渲染", ["run", pid, "--sections", scope], {})]
    return steps


def role_env(prefix, role):
    """方案里一个角色的设置 → 流水线认的环境变量（IUNO_TRANSLATE_* / IUNO_REVIEW_*）。"""
    p = provider(role["provider"])
    if not p:
        raise ValueError(f"找不到接口 {role['provider']}，先在「接口」页添加")
    env = {f"{prefix}_BASE_URL": p["base"], f"{prefix}_API_KEY": p["key"], f"{prefix}_MODEL": role["model"]}
    extra = THINKING.get(role.get("thinking") or "default", {})
    if extra:
        env[f"{prefix}_EXTRA"] = json.dumps(extra)
    return env


# ---------- 价格与时段 ----------

def prices():
    return PRICES | _load("prices.json", {})


def is_peak(t=None):
    """北京时间工作日 9–12、14–18 点是 DeepSeek 的高峰时段（法定节假日不算，这里没有排除）。"""
    bj = time.gmtime((t or time.time()) + 8 * 3600)
    return bj.tm_wday < 5 and (9 <= bj.tm_hour < 12 or 14 <= bj.tm_hour < 18)


def cost(usage, model):
    """用量 → (元, 是否为上限)。没有时段和缓存记录的旧数据按高峰价算，是上限。"""
    if not usage or model not in prices():
        return 0.0, False
    hit, miss, out, timed = prices()[model]

    def base(p=""):
        cached = usage.get(p + "cached_tokens", 0)
        return (cached * hit + (usage.get(p + "prompt_tokens", 0) - cached) * miss
                + usage.get(p + "completion_tokens", 0) * out) / 1e6

    if not timed:
        return base(), False
    if "cached_tokens" not in usage:
        return base() * 2, True
    return base() + base("peak_"), False


def model_of(name):
    """译者或审校记录里的名字（例如「deepseek-v4-pro（关思考）」）→ 价格表里的模型名。"""
    return name.split(" + ")[-1].split("（")[0].strip()
