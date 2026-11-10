# 提取候选接入实时 ORM 绑定

2026-11-04。现有 Tag、CustomField 两个候选已接入实时绑定，并在固定范围内完成验收：**32 项接入检查和 46 项原有回归全部通过；另有 10 项离线提取检查通过。** 本阶段完成，不自动追加实例、语法支持或性能实验。

## 接入结果

| 源码调用点 | 从 helper 键解析出的 ORM 字段 | 匹配的人工审计规格 | 对照结果 |
|---|---|---|---|
| TagViewSet | tag_id → tag | tag | 8 个既有场景一致 |
| CustomFieldViewSet | field_id → field | custom_field | 8 个既有场景一致 |

[接入层](../prototype/aggregate_reattach/callsite_binding.py)接收离线提取的候选，读取运行环境中的 views.py，核对已固定的 SHA-256，并重新提取配置作完整对照。符号只在本次明确允许的模型集合内解析，不执行候选提供的表达式或动态导入。helper 键通过实时 ORM 字段的 attname 和关联目标解析，不靠删除 `_id` 后缀猜测。

解析结果匹配唯一的已审计 RelationSpec 后，进入既有绑定器。manager 条件、模型/helper 源码、转换器、数据库 schema、身份、查询形状和事务上下文等核验继续由原机制承担。执行前重新检查候选来源与绑定事实，随后使用现有执行器重建 SQL；候选只能使用一次。未知配置或未满足的前提通过调用方提供的原始区域操作回退，执行后的 SQL 错误不重试。

此次自动化减少了调用方手工指定规格键和关系字段的工作；**人工审计注册表仍然是准入依据**。这里没有自动生成新的 RelationSpec，也没有从测试样本推导完整能力。

## 固定验收

16 个差分场景直接复用现有 Tag、CustomField 各 8 组夹具，包括空数据、无关联、删除过滤、稀疏与稠密关联；使用固定种子和无并发写入的数据。每个场景同时比较：

- 候选解析与人工指定规格获得的完整绑定事实，包括关系、manager 谓词、schema 与环境。
- SQL 文本和参数，以及候选执行、人工规格执行、原 helper 和夹具期望的完整整数对序列。
- 候选路径实际执行一条变换后的业务查询。

另有 16 项必要检查覆盖两个关系的未知调用点、篡改关系键、来源版本变化、缺少声明、未准入消费者、名称排序查询、绑定后来源失效和重复使用。均保留原路径，未执行候选业务 SQL。

SQL 与人工规格的对照验证的是配置接入是否选中同一已审计关系；两条路径共用绑定器和生成器，因此这不是独立实现之间的正确性证明。原 helper 与夹具期望提供额外的结果对照，既有错误 SQL 对照及失效测试也已回归。

| 检查集合 | 测试数 | 报告记录数 |
|---|---:|---:|
| 本次候选接入 | 32 | 32 |
| CustomField 复用回归 | 13 | 14 |
| Tag 绑定回归 | 23 | 23 |
| B2 回归 | 10 | 14 |
| Docker 合计 | 78 | 83 |

首次运行也通过 78 项测试，但报告完整性验收失败：pytest-django 将事务测试排到其他模块之后，原 Tag 绑定报告的 module fixture 再次初始化，只留下最后一条记录。将其生命周期改为 session 后，重跑同一套验收，78 项测试及全部报告检查通过。首次失败的运行、输入和报告原样保存在归档的 `attempts/initial-report-failure`，没有把失败状态改写为成功。

## 保留的边界

实验入口仍是完整目标关系、按 pk 排序、立即消费整数对、已保存且启用的 superuser、固定数据及成功执行条件。真实 ViewSet 的名称排序、MRO、请求过滤、分页和消费者语义继续保持 `consumer_admission: not_established`。本次不修改上游源码、人工注册表或 API 执行路径，不测性能；有限差分结果也不构成完整能力等价证明。

## 复现与证据

```sh
python3 scripts/tag_binding_audit.py --callsites
python3 -m unittest discover -s tests -p test_callsite_extraction.py -v
```

- [接入实现](../prototype/aggregate_reattach/callsite_binding.py)、[固定验收](../prototype/aggregate_reattach/test_callsite_binding.py)
- [候选与人工规格对照报告](../evidence/count-callsite-binding/20261104-432d1491b8e1/tag-callsites.json)
- [测试日志](../evidence/count-callsite-binding/20261104-432d1491b8e1/probe.log)、[运行与清理记录](../evidence/count-callsite-binding/20261104-432d1491b8e1/run.json)
- [来源、输入与文件摘要](../evidence/count-callsite-binding/20261104-432d1491b8e1/manifest.json)

冻结源码版本仍为 `1b8b861a6072fd85aad68847c0d312d7a9fd2ca0`；运行环境沿用既有镜像锁。两次隔离运行均已清理各自的容器、网络和数据库卷。本阶段已达到约定结束条件。
