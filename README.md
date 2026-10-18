# iuno_reader

arXiv 论文的**逐句双语对照**阅读工具：每段英文下面紧跟中文，逐句核对译文有没有丢掉否定、数字、限定词、公式和引用，并用颜色标出值得怀疑的句子。

不翻译 PDF。PDF 翻译要先拆版面再重新排版，这一步可能会悄悄丢掉整段文字（例如 BabelDOC 的 issue #620、#613）。这里直接用 arXiv 的 HTML 版（由作者上传的 LaTeX 生成），段落和公式都是原样的，译文和原文可以逐句对齐、逐句检查。

## 输出长什么样

- `out/<id>.md`：给 Obsidian 读。每段先是英文，下面是 **译** 开头的中文；有问题的句子按颜色高亮，鼠标悬停能看到原因；每段下面有一个默认折叠的框，把被标出的句子的原文和译文并排列出。
- `out/<id>.html`：左右两栏，左英右中，鼠标移到一句上，两边对应的句子会同时被框出来。用浏览器打开（公式需要联网加载 MathJax）。
- `out/<id>.checked.json`：核对结果的原始数据。

## 颜色

| 颜色 | 含义 | 来源 |
|---|---|---|
| 🔴 红 | 可能丢了信息：否定词、数字、公式、代码、脚注标记、引用年份对不上；限定词丢失或语气被加强（如 suggest 译成「证明」）；整句漏译；整段句数对不上 | 自动规则 |
| 🟠 橙 | 翻译没把握（翻译者自评低置信，并写明原因）；译文明显过短，可能漏译 | 翻译者自评 + 规则 |
| 🟡 黄 | 需要留意：翻译者自评中等置信；译文明显过长；残留大段英文 | 翻译者自评 + 规则 |
| 🔵 蓝 | 术语没有按术语表翻译 | 术语表 |

**这些标记只是提醒。** 标出来的句子要看一眼；没标出来的句子，也不保证完全正确。规则都是启发式的，例如「否定词」只检查译文里有没有任何否定表达，不检查否定的是不是同一件事。

## 用法

```bash
cd /home/a_coin_fan/work/iuno_reader
.venv/bin/python -m iuno_reader fetch 2602.04212        # 下载 arXiv HTML 到 cache/
.venv/bin/python -m iuno_reader extract 2602.04212      # 拆成片段和句子 → data/<id>/doc.json
.venv/bin/python -m iuno_reader tasks 2602.04212 --sections 2,3,4   # 导出待译清单（人工或 Agent 翻译时用）
.venv/bin/python -m iuno_reader translate 2602.04212 --sections 2,3,4   # 自动翻译（需要接口，见下）
.venv/bin/python -m iuno_reader render 2602.04212 --translated-only     # 核对并渲染
```

- `--sections`：只处理某几节，例如 `abs,2,3,4`（`abs` 是摘要）或附录 `A,B`。
- `--translated-only`：渲染时只输出已经翻译的片段。

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

   每翻完一段就保存一次，中途失败不会丢掉已经完成的部分；已翻译的段落默认跳过，加 `--force` 重翻。翻译规则（逐句、限定词按强弱译、公式和占位符原样保留、自评置信度）写在 `iuno_reader/translate.py` 的 `RULES` 里。**这部分代码还没有在真实接口上跑过。**

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
.venv/bin/python tests/test_api_mock.py                  # 用假回复测试接口流程（不联网）
.venv/bin/python tests/smoke_extract.py 2602.04212 ...   # 抽取冒烟测试（先 fetch + extract）
.venv/bin/python tests/coverage_vs_pdf.py 2602.04212 <PDF 纯文本>   # 用 PDF 文本独立检查有没有漏段落
```

## 已知限制

- 只支持有 arXiv HTML 版的论文；没有 HTML 版时，还不支持退回 TeX 源码。
- 表格只翻译表题，表格内容请看原文。
- 图片直接引用 arXiv 上的图片地址，离线时看不到。
- 用罗马数字编号的论文（如 I、II、III），正文和附录的节编号可能重复，`--sections` 会同时选中两者。
- 核对规则是启发式的：可能误报（例如把代词 one 当成数量词，已排除常见情况），也可能漏报（例如否定的对象译错了）。
