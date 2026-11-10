# 聚合回填的最小转换接口 v0

后续进展：[Tag 实际查询绑定](TAG_QUERY_BINDING.md)已接入本接口，提供当前查询/元数据核验并贯通受控生成执行；随后由[第二个关系实例](AGGREGATE_BINDING_REUSE.md)验证共享绑定与生成，并完成[调用点配置候选提取](COUNT_CALLSITE_EXTRACTION.md)。本文保留独立 v0 接口的设计与当时验证范围；直接运行离线入口仍不会自动连接数据库。

2026-11-03。已实现**离线模式识别与准入计划接口**，从真实 helper 恢复符号角色，区分查询核验和调用方声明，条件不足时保留原路径。本版没有自动查询绑定器、SQL 生成器或执行器，不把这一步称为完整 B3。

沿用[变换契约](AGGREGATE_REATTACH_CONTRACT.md)与[单入口绑定](TAG_BINDING_AUDIT.md)。当前任务是补齐转换机制；既有成本数据继续保留，不以首版效率作为实现门槛。

## 1. 手工 B2 的信息来自哪里

| 信息 | 恢复/提供方式 | v0 状态 |
|---|---|---|
| 哪个参数是目标查询 T、中间模型 L、允许 ID A | 分析 helper 的参数与受限 AST 数据流 | 自动恢复符号角色；不执行函数 |
| 分组键参数、计数列参数、COUNT DISTINCT | 匹配 filter/values/annotate 的结构及参数流向 | 自动识别，真实模型字段尚未绑定 |
| 字典来源、CASE 查找键、缺失值 0 | 匹配完整函数体中的赋值、推导式、空分支与返回 | 自动识别；多余消费者或语句会拒绝该语法模式 |
| T/K/L/A 的实际查询与权限、manager 谓词 | 在固定 ORM 运行时绑定输入，再检查查询结构和编译结果 | 已有 Tag 人工审计证据；自动绑定器未接入 |
| 非 NULL 整数键、DB 相等语义、分组键唯一 | 模型/数据库约束与实际聚合结构 | 外部核验义务，不能由源码出现 GROUP BY 就直接标记完成 |
| 最终排序、结果转换与观察边界 | 消费者绑定加 ORM/驱动类型核验 | 需调用点信息；helper 源码自身不足以确定 |
| 固定读取状态、立即消费、成功执行范围 | 调用方/实验环境显式契约 | 必须声明，不能从 trace 或一批测试推断 |
| 无可观察读取副作用、运行时符号绑定固定 | 环境与依赖契约 | 必须声明；源码 import 不排除 monkeypatch 或对象重载 |

第一层识别程序结构，第二层核验实际查询，第三层约束执行环境。有限数据测试支持实现验证，不代替后两层的条件，也不决定哪些数据或模块可以删除。

## 2. 接口与状态

实现：[transform_contract.py](../prototype/aggregate_reattach/transform_contract.py)。

```python
region = recover_region(source_text, "annotate_document_count_by_ids")
plan = prepare_plan(
    region,
    bound_source_sha256=None,  # 尚无与该源码版本对应的绑定记录
    checks={},                # 未核验的条件不能默认视为满足
)
```

`Region` 包含整个源文件的 SHA-256、函数/行号、目标/关系/允许 ID 参数、分组/计数参数、字典变量、输出别名、distinct 和零默认值。它没有数据库连接、样本 ID 或可执行回调。

输入 `checks` 的每项为 `Evidence(state, reference)`：

- `verified`：由查询绑定/元数据审计提供的核验记录。
- `declared`：调用方承担的显式环境假设。
- `refuted`：已知条件不满足。
- `unknown`：尚无充分信息；省略的项也按 unknown 处理。

七项条件要求 verified：入口范围、完整关系谓词、键相等与非空、分组键唯一、消费者全序、整数结果转换、同一数据库与上下文。五项条件要求 declared：固定读取状态、立即读取整数对、成功执行范围、无可观察读取副作用、固定运行时绑定。另外检查绑定记录是否对应当前源文件 SHA-256。

准入要求证据种类匹配且引用非空；例如调用方把“分组键唯一”写成 declared，不能代替实际核验。源码哈希不同意味着旧绑定失效，未提供哈希意味着尚待绑定。这里哈希采用整个文件，其他函数或注释变化也会要求重绑，是有意保守的首版策略。

| 结果 | 含义 | 动作 |
|---|---|---|
| unsupported_pattern | 源码不在当前语法子集中 | 保留原路径；不等于证明该程序不可改写 |
| needs_evidence | 有必要条件未知或证据种类不符 | 保留原路径，不提供 lowering 描述 |
| rejected | 条件被否定，或绑定源码不匹配 | 保留原路径，不提供 lowering 描述 |
| eligible_for_lowering | 给定的核验记录与声明满足协议 | 仅准备候选；附左连接/零值/保留语义描述，execution 仍为 not_implemented |

**信任边界：** v0 信任外部提供的 verified 标签和引用，不会自行证明引用内容、真实性或时效性。它不是安全证明器。正向协议测试使用明确标注的合成证据；这些测试不构成真实入口已自动准入的结论。执行阶段还必须由绑定器提供实际查询及参数，并由后续生成器验证产物。

## 3. 源码识别的严格范围

识别器读取 Python AST，不导入上游模块，不调用 ORM，不执行输入源码。匹配的是完整 helper 函数体，支持参数/局部变量一致改名、注释和 docstring 差异。

当前语法只支持已取证的分组 distinct count、字典、零分支和 CASE 形式；ORM 符号须有直接的 Django import。目标查找名 `pk`、聚合别名 `c`、输出名 `document_count` 固定，任意等价改写、输出别名变化、额外语句、装饰器或新的分支暂不接纳。

这是一条保守结构识别规则，没有做一般控制流/别名/副作用分析。完整函数体匹配可以排除当前语法内额外的字典用途，不能排除运行时自定义对象在同样调用外形下产生副作用；后者仍受显式契约约束。参数名和真实数据值不用于判断业务能力。

## 4. 实际输出与验证

对固定 Paperless 的 `annotate_document_count_by_ids`，自动恢复：T=`queryset`、L=`through_model`、A=`document_ids`、分组键参数=`related_object_field`、计数参数=`target_field`，映射变量=`counts_by_pk`，distinct=true、缺失值=0。恢复结果没有硬编码 Tag 模型或样本键。

运行离线入口：

```sh
python3 scripts/inspect_aggregate_contract.py
python3 -m unittest discover -s tests -p test_transform_contract.py -v
```

真实源码输出为 **needs_evidence / keep_original**，列出 13 项待满足条件。已有 Tag 绑定报告仍是人工审计证据；本轮没有自动导入这些结论或把历史测试结果转换成新运行环境的事实。

10 项本地检查通过，包括真实源码角色恢复、局部变量改名、语义变异拒绝、未知上下文、声明不能冒充核验、过期/缺失源码绑定、被否定的快照条件、空引用/拼错条件名，以及合成完整证据的准入协议。

语义变异子测试实际覆盖四种修改：去掉 DISTINCT、默认值改成 1、增加字典外部消费者、使用错误 CASE 键。它们在语法匹配阶段被拒绝，不是重新运行数据库得到的反例。既有数据库差分证据保持原样。

- [真实源码计划](../evidence/aggregate-contract/20261103-interface-v0/source-plan.json)
- [验证摘要](../evidence/aggregate-contract/20261103-interface-v0/validation.json)
- [代码与证据摘要](../evidence/aggregate-contract/20261103-interface-v0/manifest.json)

## 5. 下一步

只为已核对的 Tag 入口接入查询绑定器：接收实际 ORM 输入，提供目标查询与原分组查询、参数及元数据核验结果；调用方继续显式声明执行范围。然后把通过准入的计划接到现有 B2 的生成步骤，对照原 helper 验证。

下一个难点是**如何让核验记录与将要执行的查询、源码版本及消费者对应，避免使用已经失效的证明条件**。当前源码 SHA 只解决其中一个版本关系；查询绑定和执行生命周期仍待实现。保持单一模式推进，不扩展完整软件架构恢复，也不以当前效率提前限制实现。
