# iuno_reader

arXiv 论文的**逐句双语对照**阅读工具：每段英文下面紧跟中文，逐句核对译文有没有丢掉否定、数字、限定词、公式和引用，并用颜色标出值得怀疑的句子。

不翻译 PDF。PDF 翻译要先拆版面再重新排版，这一步可能会悄悄丢掉整段文字（例如 BabelDOC 的 issue #620、#613）。这里直接用 arXiv 的 HTML 版（由作者上传的 LaTeX 生成），段落和公式都是原样的，译文和原文可以逐句对齐、逐句检查。

## 流水线：一条命令

```bash
cd /home/a_coin_fan/work/iuno_reader
.venv/bin/python -m iuno_reader run 2602.04212 [更多编号...] [--sections abs,2,3,4] [--export <目录>]
```

自动完成：下载 arXiv HTML → 拆成段落和句子 → 翻译 → 审校 → 规则核对 → 渲染 → 写出 `out/<id>/`。

- **默认只翻正文**（摘要和所有非附录的节，外加正文引用到的图表和脚注），附录和参考文献不翻。要含附录的全文用 `--sections all`，只要某几节用 `--sections abs,2,3`。附录的识别：节名以 Appendix 开头；或者节编号和前面重复（罗马数字编号的论文，附录会从 I 重新编号）。
- 配了接口（见下文「接口配置」），翻译和审校自动完成。
- 没配接口时不会失败：流水线生成任务清单和写给 agent 的说明（`AGENT_TRANSLATE.md`、`AGENT_REVIEW.md`），状态停在 `awaiting_translation` 或 `awaiting_review`。agent 按说明完成后再运行一次 `run`，会从中断处接着做。
- `--translate auto|api|agent`、`--review auto|blind|backtranslate|agent|none` 可以指定方式。
- 给其他 agent 的使用说明见 [AGENTS.md](AGENTS.md)。

## 网页前端

```bash
cd /home/a_coin_fan/work/iuno_reader
.venv/bin/python -m iuno_reader serve          # 浏览器打开 http://localhost:8780
```

- **任务**：把论文 PDF 拖进来（从文件名认 arXiv 编号），或粘贴编号、链接；选一个方案，点「添加」。每篇一张卡片：翻译、审校的进度条，已经花了多少钱，标红标橙句数，可以暂停、继续、重试，完成后「打开」看双语网页。
- **方案**：内置「尤诺推荐」（pro 关思考翻译 + pro 盲审）、「尤诺推荐 · 精读」（再加 Kimi K3 审）、「最省钱」（flash 翻译，只在空闲时段跑）。也可以复制一份改成自己的：每个角色选接口、模型、思考（默认 / 关 / 低 / 高）、并发数；翻译范围（只翻正文 / 全文）；是否只在 DeepSeek 空闲时段跑。下面的「设置」可以改同时跑几篇，以及完成后把网页和 Markdown 复制到哪个目录（例如知识库的 `translations/`）。
- **接口**：自动读 cc-switch 里的接口；也可以手动添加（选服务商自动填地址）。每个接口可以「测试翻译」「测试审校」，就是跑一次 `probe`。
- **用量**：每篇的花费表（翻译、每份审校分开），按正文句数算每百句多少钱，附价格表。

只监听本机。接口密钥和自己的方案存在 `~/.config/iuno_reader/`（权限 600），网页上只显示密钥末尾四位。任务队列在 `out/jobs.json`，每个任务的日志在 `out/jobs/<编号>.log`；关掉服务再打开，没做完的任务会接着排队，已经翻好、审好的段落不会重复花钱。价格表在 `iuno_reader/providers.py`，可以用 `~/.config/iuno_reader/prices.json` 覆盖。

## 输出 out/<id>/

| 文件 | 内容 |
|---|---|
| `manifest.json` | 每一步的状态、句数和标记统计、译者和审校者及独立性、各文件路径、下一步该做什么 |
| `paper.zh.md` | 给 Obsidian 读：每段英文下面是 **译** 开头的中文；有问题的句子按颜色高亮，悬停看原因；每段下面有默认折叠的逐句对照框 |
| `paper.zh.html` | 浏览器打开，左英右中，鼠标移到一句上两边同时框出（公式需联网加载 MathJax） |
| `paper.en.md` | 英文全文，保留章节、图注、公式、脚注，给 agent 引用和整理 |
| `flags.md` | 所有被标记的句子：原文、译文、原因 |
| `checked.json` | 完整核对数据 |

## 网页阅读开关（paper.zh.html 标题区）

| 开关 | 作用 |
|---|---|
| 隐藏标记 | 关掉所有风险底色，纯阅读 |
| 字体粗细 细/中/粗 | 英文 Computer Modern 用细描边加粗，中文思源宋体切换字重；默认「中」 |
| 加粗 关/首字母/整词 | 只作用于核心词：名词、专有名词、动词、形容词、副词、数词、否定词（not、no、cannot…），以及连字符复合词和术语表多词术语的每一部分（in-context 的 in、one-token 的 one）；the、of、to、which 这类虚词不变。「首字母」加粗核心词的前几个字母（study → **st**udy，representation → **repre**sentation，1 个字母的词不加粗），默认这一档；「整词」让核心词深色稍粗、其余词变浅灰 |
| 加宽词距 | 英文栏词距再加宽（英文默认已改为左对齐，词距均匀） |
| 点词高亮 | 点任意英文单词，全文同一个词（按原形，整篇统一）一起铺底色；若在术语表里，中文栏对应译名也一起亮；最多三个，再点取消 |

开关状态保存在浏览器里。点词高亮和加粗需要 spaCy 与 `en_core_web_sm`（没有时自动关闭这两项）。

## 颜色

| 颜色 | 含义 | 来源 |
|---|---|---|
| 🔴 红 | 可能丢了信息：否定词、数字、公式、代码、脚注标记、引用年份对不上；限定词丢失或语气被加强（如 suggest 译成「证明」）；整句漏译；整段句数对不上 | 自动规则 |
| 🟠 橙 | 翻译没把握（翻译者自评低置信，并写明原因）；译文明显过短，可能漏译 | 翻译者自评 + 规则 |
| 🟡 黄 | 需要留意：翻译者自评中等置信；译文明显过长；残留大段英文 | 翻译者自评 + 规则 |
| 🔵 蓝 | 术语没有按术语表翻译 | 术语表 |

**这些标记只是提醒。** 标出来的句子要看一眼；没标出来的句子，也不保证完全正确。规则都是启发式的，例如「否定词」只检查译文里有没有任何否定表达，不检查否定的是不是同一件事。

## 分步命令（调试用）

```bash
cd /home/a_coin_fan/work/iuno_reader
.venv/bin/python -m iuno_reader fetch 2602.04212        # 下载 arXiv HTML 到 cache/
.venv/bin/python -m iuno_reader extract 2602.04212      # 拆成片段和句子 → data/<id>/doc.json
.venv/bin/python -m iuno_reader tasks 2602.04212 --sections 2,3,4   # 导出待译清单（人工或 Agent 翻译时用）
.venv/bin/python -m iuno_reader translate 2602.04212 --sections 2,3,4   # 自动翻译（需要接口，见下）
.venv/bin/python -m iuno_reader render 2602.04212 --translated-only     # 核对并渲染
```

- `--sections`：只处理某几节，例如 `abs,2,3,4`（`abs` 是摘要）或附录 `A,B`；`body` 表示全部正文，`all` 表示全文。`run` 默认是 `body`，分步命令默认全文。
- `--translated-only`：渲染时只输出已经翻译的片段。

### 四个角色，互不交叉

翻译、盲审、回译、回译比对各有一份提示词（`prompts/`），每份都写明「能看到什么、看不到什么」。接口模式下由 `iuno_reader/roles.py` 的白名单强制执行：回译只看得到中文、回译比对只看得到英文原文和回译、盲审看不到译者备注；每次调用都是一段新对话，不共享历史。各角色的模型分开配置，审校结果会记录它和译者是不是同一个模型（`independence`），同模型会在图例和 manifest 里注明「独立性不足」。详见 [AGENTS.md](AGENTS.md) 的表格。

### 接口配置

```bash
export IUNO_TRANSLATE_BASE_URL=... IUNO_TRANSLATE_MODEL=... IUNO_TRANSLATE_API_KEY=...   # 翻译（未设则用 IUNO_READER_*）
export IUNO_REVIEW_BASE_URL=...    IUNO_REVIEW_MODEL=...    IUNO_REVIEW_API_KEY=...      # 盲审与回译比对，建议换一家模型
export IUNO_BT_BASE_URL=...        IUNO_BT_MODEL=...        IUNO_BT_API_KEY=...          # 回译（未设则用 IUNO_REVIEW_*）
```

密钥不要写进仓库。

### 两种翻译方式

译文都存在 `data/<id>/translation.json`，格式一样：

```json
{
  "__sections__": {"2 Preliminaries: ...": "2 预备知识：..."},
  "<片段 id>": {"sentences": [{"zh": "译文", "conf": "high|mid|low", "note": "不是 high 时写原因"}]}
}
```

1. **人工或 Agent 翻译**：先 `tasks` 导出清单（每段附上上一段原文和命中的术语），按清单逐句翻译，写进上面的文件。句数必须和原文一致。
2. **自动翻译**：OpenAI 兼容接口（DeepSeek、OpenAI 等），只用 Python 标准库。设置三个环境变量后运行 `translate`：

   ```bash
   export IUNO_READER_BASE_URL=https://api.deepseek.com/v1
   export IUNO_READER_MODEL=deepseek-chat
   export IUNO_READER_API_KEY=...        # 不要写进仓库
   ```

   每翻完一段就保存一次，中途失败不会丢掉已经完成的部分；已翻译的段落默认跳过，加 `--force` 重翻。翻译规则（逐句、限定词按强弱译、公式和占位符原样保留、自评置信度）写在 `prompts/translator.md` 里。

   接口不支持 JSON 模式（`response_format`）时会自动退回普通模式；推理模型输出里的 `<think>` 段会被去掉；密钥错、模型名错这类错误不重试，直接报出状态码和返回内容。

### 换接口前先探测

```bash
.venv/bin/python -m iuno_reader probe [--roles translator,reviewer,backtranslate]
```

给每个配好接口的角色发一个三句话的固定样例（含否定、限定词、数字、公式、脚注占位符），报告能不能连上、JSON 模式支不支持、耗时和 token；翻译结果用规则核对一遍；审校角色拿到的是故意译错两句的样例（「suggests, but does not prove」译成「证明」、41.2% 写成 42.1%），看它能不能抓出来。报告另存到 `out/probe/`。

已实测（2026-10-19，`api.deepseek.com/v1`）：`deepseek-v4-flash` 翻译 3 秒、JSON 模式可用；`deepseek-v4-pro` 盲审 5 秒抓对 3/3，回译 + 比对 36 秒抓对 2/3（把第 1 句的回译措辞差异误报成 minor）。整篇论文还没有用接口跑过。

### 独立审校（规则查不出来的那一层）

规则只看表面（否定词、数字、限定词在不在），查不出「主宾译反了」「因果关系错了」这类意思上的偏差。审校环节专门补这一层，结果存成 `data/<id>/review.<审校者>.json`，渲染时自动合并：`major` 标红、`minor` 标黄，提醒里以 `[审校者]` 开头，并附建议译文。

1. **盲审**：审校者只看到原文和译文，看不到翻译者的置信度和备注。
   ```bash
   .venv/bin/python -m iuno_reader review-tasks 2602.04212            # 导出盲审清单
   # 交给人、另一个模型或子代理审，写成 review.json 后导入（会校验条数）：
   .venv/bin/python -m iuno_reader import-review 2602.04212 review.json --name blind-subagent
   # 或者用接口自动盲审：
   .venv/bin/python -m iuno_reader review 2602.04212 --mode blind
   ```
2. **回译比对**：另一个模型先**只看中文**译回英文，再在英文里比较原文和回译，回译结果也会显示在提醒里。
   ```bash
   export IUNO_REVIEW_BASE_URL=... IUNO_REVIEW_MODEL=... IUNO_REVIEW_API_KEY=...   # 建议用和翻译不同的模型
   .venv/bin/python -m iuno_reader review 2602.04212 --mode backtranslate
   ```

每条审校意见都记下了它审的是哪一版译文（`zh_reviewed`）。之后改了译文，旧意见会自动作废，不会标到新译文上。图例里会写明审了多少句、几条已作废。

**独立性要打折扣的情况**：审校者和翻译者是同一个模型（例如由同一个 Agent 另开子代理来审）时，只能算换个角度重看一遍，可能和翻译犯同样的错。要真正独立，审校要换一个不同的模型，或者由人来抽查。

### 术语表

- `glossary/terms.tsv`：术语表。可以从 Research-KB 导入：`python -m iuno_reader import-kb-glossary <glossary.md>`。
- `glossary/hedges.tsv`：限定词表。每行写明英文、可接受的中文，以及「出现了就说明语气被加强」的中文。

## 测试

```bash
.venv/bin/python tests/test_check.py                     # 核对规则的单元测试
.venv/bin/python tests/test_review.py                    # 审校合并（含过期检测、盲审清单不泄露备注）
.venv/bin/python tests/test_api_mock.py                  # 用假回复测试接口流程（不联网），含 JSON 模式退回、401 不重试
.venv/bin/python tests/test_nlp.py                       # 词性、核心词与首字母加粗长度
.venv/bin/python tests/test_server.py                    # 网页前端的服务端（接口、方案、任务队列、进度与花费）
.venv/bin/python tests/smoke_extract.py 2602.04212 ...   # 抽取冒烟测试（先 fetch + extract）
.venv/bin/python tests/coverage_vs_pdf.py 2602.04212 <PDF 纯文本>   # 用 PDF 文本独立检查有没有漏段落
```

## 已知限制

- 只支持有 arXiv HTML 版的论文；没有 HTML 版时，还不支持退回 TeX 源码。
- 表格只翻译表题，表格内容请看原文。
- 图片直接引用 arXiv 上的图片地址，离线时看不到。
- 用罗马数字编号的论文（如 I、II、III），正文和附录的节编号可能重复，`--sections` 按编号选节时会同时选中两者；`body` 不受影响。
- 核对规则是启发式的：可能误报（例如把代词 one 当成数量词，已排除常见情况），也可能漏报（例如否定的对象译错了）。
