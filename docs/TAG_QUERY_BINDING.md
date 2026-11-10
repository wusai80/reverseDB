# Tag 实际查询绑定与执行前核验

后续进展（2026-11-04）：[CustomField 关系实例](AGGREGATE_BINDING_REUSE.md)已复用相同核心，模型/关系键/manager 条件参数化，全部 Tag 回归通过。本文保留单入口阶段的证据与结论。

2026-11-03。**单个已审计入口已贯通：源码识别 → 实际 ORM/元数据核验 → 条件化计划 → B2 模板生成 → 执行前重核验 → 完整结果对照。** 8 组场景与原 helper 一致，失效回退与旧 B2 回归合计 33 项 pytest 通过。该实现仍是 Tag 专用适配器，不是任意调用点自动发现或完整 B3。

## 接口与一次执行的过程

实现：[tag_query_binding.py](../prototype/aggregate_reattach/tag_query_binding.py)。调用方只传目标查询、用户、消费者标识及五项显式声明，不能自行填入 verified 查询事实。

```python
bound = bind_tag_query(user, target_queryset, declarations)
rows, audit = execute_tag_candidate(bound, user, target_queryset, declarations)
```

1. 读取实际加载模块对应的源码，调用 v0 AST 识别器恢复角色；核对已审计的源码、模型、运行时版本和转换代码文件摘要，以及相关函数的加载字节码与源码编译结果。
2. 核对 active superuser、default 数据库、未求值的全 Tag 主键排序查询。当前实际输入只接纳已审计的形状，其他身份/查询走原路径。
3. 保留 `permitted_document_ids(user)` 与 through manager 构造分组查询，检查整数键、NULL、分组结构、连接上下文和真实数据库目录信息。此时不读取业务计数或物化允许 ID。
4. 由绑定器生成七项 verified 记录，与调用方五项 declared 条件一起进入 `prepare_plan`。缺少条件时不产生候选。
5. 通过后，将实际目标查询、分组查询及参数交给共用 B2 模板。模板从旧 B2 提取为 `render_tag_counts_sql`，没有另写权限规则或换一种计数算法。
6. 执行器重新绑定当前输入并比较指纹。只使用本次重新构造的 SQL；不直接执行传入对象保存的 SQL 字段。候选仅供一次尝试，重复使用回原 helper。

业务输出仍为立即读取、按 pk 升序的整数对。消费者标识是这一固定协议的版本标记，不是任意消费者的自动语义分析。

## 哪些事实现在由代码核对

| 准入条件 | 当前核验依据 |
|---|---|
| entry_scope | 已保存的 active superuser；Tag 查询类、模型、数据库与审计形状一致；目标查询未求值、不锁行 |
| relation_predicates | 原权限函数与 through manager，原两个 IN 条件，实际编译 SQL/参数；限定模式下按原结构构造 |
| key_equality_and_nullability | ORM 的非 NULL AutoField 目标键，加实际 PostgreSQL 目录中的非 NULL integer 列及 Tag/Document 主键 |
| group_key_unique | 实际 group_by 恰好为 tag_id 的一个 Col，聚合为 distinct Count；唯一性由该分组结构得到 |
| consumer_total_order | 目标查询编译结果与已审计的 pk 全序入口一致，消费者版本匹配；生成模板显式排序 |
| integer_result_conversion | 沿用已审计的冻结 Django/转换代码，核对文件摘要；执行器按既定协议转 Python int |
| same_database_and_context | target/allowed/counts 的 alias 一致；核对数据库、用户、search_path、隔离级别、后端进程和事务 ID；拒绝未绑定的 RLS 配置 |

审计配置 [tag_binding_profile.json](../prototype/aggregate_reattach/tag_binding_profile.json) 来源于既有绑定证据，固定 Django 5.2.17、PostgreSQL 18.6、源码/模型/转换代码摘要。它保留人工审计知识；本轮自动化的是当前实例与这些条件的对应核验，没有自动证明新版本的兼容性。

## 条件如何与当前执行对应

指纹包括源码与配置摘要、目标/分组/权限/through SQL 及参数、关键列与约束、表 OID/RLS、连接上下文、用户标识与状态、消费者标识，以及声明内容。

执行前重新生成这些事实，有一项不匹配就不执行旧候选。拒绝或缺少证据时调用原 helper，使用**当前传入的查询与用户**，不会把已变化的输入偷偷换回全 Tag 查询。

要求绑定与执行处在同一显式事务作用域内；事务 ID 用于检测跨事务使用。它不证明 Read Committed 提供固定快照，也不能检测所有并发写入或同事务自写入。固定读取状态、立即消费、成功执行范围、无可观察读副作用和固定运行时绑定仍须由调用方声明并落实。

单次候选是进程内协作协议，不是持久化计划缓存或防篡改令牌。检查与 SQL 执行间仍依赖上述执行环境约束；不声称解决任意并发下的检查/执行竞态。

数据库执行错误直接传播，不在错误后重试原查询。新增的目录核验本身也可能报数据库错误，因此本轮不声称异常行为与原 helper 等价。

## 实际验证

| 检查 | 数量 | 结果 |
|---|---:|---|
| 空集合、零计数、删除范围、混合/稀疏/密集等完整结果对照 | 8 | 与原 helper 和原 B2 SQL 一致 |
| 五项声明逐项缺失 | 5 | needs_evidence，回原路径 |
| 查询、消费者、声明、审计配置、运行时代码、search_path、事务变化 | 7 | 不执行旧候选，回原路径 |
| 未准入身份及伪造 verified 输入 | 1 | 身份回退；外部查询事实输入被拒绝 |
| 候选 SQL 注入故障 | 1 | 异常传播，未重试原 helper |
| 修改保存的 SQL 及重复使用候选 | 1 | 使用重建 SQL；第二次回原路径 |
| 共用模板提取后的旧 B2 回归 | 10 | 全部通过 |

共 **33 项测试通过**。运行时代码变更测试在实验进程中临时替换函数为行为相同的包装器，仍检测到代码身份变化；未修改上游源码。更改查询的用例包含参数化筛选，回退结果与当前筛选相符。

每次核验需要数据库目录查询。本轮正常执行阶段捕获到 13 条核验查询加 1 条候选业务查询；准备阶段还进行一次核验。因此当前带核验的整个读取**不是总共只有一条 SQL**。未做本轮耗时测量或检查缓存优化，符合先贯通机制的阶段目标。

## 证据与复现

- [绑定事实、准入计划与执行记录](../evidence/tag-query-binding/20261103-8ab372b945a2/tag-query-binding.json)
- [旧 B2 回归结果](../evidence/tag-query-binding/20261103-8ab372b945a2/tag-b2.json)
- [pytest 日志](../evidence/tag-query-binding/20261103-8ab372b945a2/probe.log)、[运行与清理记录](../evidence/tag-query-binding/20261103-8ab372b945a2/run.json)、[代码/证据摘要](../evidence/tag-query-binding/20261103-8ab372b945a2/manifest.json)

复现：`python3 scripts/tag_binding_audit.py --bind-query`。冻结镜像、独立测试数据库、无宿主端口映射；本轮资源已清理。旧证据与上游源码不变；B2 原型仅提取共用生成模板，已执行回归。

## 尚未自动化的部分

Tag 调用点到模型/字段的绑定、固定消费者选择及审计配置仍是人工指定。v0 识别器识别的是一个保守 AST 家族；本轮没有恢复任意程序中的权限语义，也没有实现查询级通用证明或完整数据库优化器。

下一步可在同一模式内梳理哪些 Tag 专用绑定可以参数化、哪些需要新实例审计；先用一个独立的同结构实例检验接口是否能复用，再判断是否值得扩展识别范围。当前性能数据继续作为以后优化的对照，不作为这一步的准入条件。
