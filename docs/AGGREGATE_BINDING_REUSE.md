# 聚合回填绑定的第二个关系实例

后续进展：[真实调用点配置的离线提取](COUNT_CALLSITE_EXTRACTION.md)已完成，两项声明与人工规格及对应历史字段证据一致；提取结果仍只作为候选，未自动准入完整 API。

2026-11-04。**Tag 与 CustomField 已使用同一绑定器、准入协议、SQL 生成模板和执行生命周期。** CustomField 的 8 组结果对照通过；两个错误版本被检出；manager 变化、跨规格候选误用和缺少条件时按要求回退。连同原有 Tag 全部回归，共 46 项 pytest 通过。

这次验证的是同一应用、同一源 helper 下两个模型关系实例的复用。CustomField 有独立数据和不同的关系过滤语义，但不是第二个软件项目，也没有新增 AST 语法模式或自动发现调用点。

## 1. 第二个实例的来源与差异

固定 Paperless 的 [CustomFieldViewSet](../sources/paperless-ngx/src/documents/views.py#L5226) 使用 count mixin，配置 `CustomFieldInstance` 和 `field_id`，进入与 Tag 相同的 `annotate_document_count_for_related_queryset`。本实验仍限定 active superuser、全部目标行、按 pk 升序、立即返回完整整数对。实际 API 默认按名称排序；本轮没有替换完整 API。

| 绑定内容 | Tag | CustomField |
|---|---|---|
| 目标模型 | Tag | CustomField |
| 关联模型 | 自动生成的 Document.tags.through | 显式 CustomFieldInstance |
| 关系键 | tag_id | field_id |
| 计数对象 | document_id，distinct | document_id，distinct |
| 关联 manager | 标准 Manager | SoftDeleteManager |
| 关联关系谓词 | 无额外 WHERE | 关联行自身 deleted_at IS NULL |
| 文档权限/删除过滤 | 原 permitted_document_ids | 同一个原函数，完整保留 |
| 最终消费者 | 主键升序整数对 | 同一协议 |

冻结镜像中的 SoftDeleteManager 源码确认默认查询只包含 `deleted_at IS NULL`。CustomFieldInstance 自身软删除和 Document 软删除是两层独立条件：原计数同时过滤关联行与允许文档关系。审计源码及摘要保存于[manager-source.json](../evidence/aggregate-reuse/20261104-8db9a3e5b787/manager-source.json)。

CustomFieldInstance 的业务值（如 value_int）可为 NULL。计数表示关联到多少个不同文档，不能把业务值列当成计数目标。原 `(document, field)` 唯一约束仍保留；没有破坏约束制造重复关联，也没有删去 DISTINCT。

## 2. 参数化了什么

在 [tag_query_binding.py](../prototype/aggregate_reattach/tag_query_binding.py) 内引入显式 `RelationSpec` 注册表，登记两个实例的目标模型、关联模型、关系字段、manager 类、允许的 manager NULL 谓词，以及关联模型是否自动生成。文件名沿用原名，核心已由两个规格共享。

共用接口：

```python
bound = bind_count_query("custom_field", user, target_queryset, declarations)
rows, audit = execute_count_candidate(
    "custom_field", bound, user, target_queryset, declarations,
)
```

Tag 旧接口保留为委托到 `"tag"` 规格的包装器。两者共用源码识别、七项事实核验、五项声明检查、指纹生成、执行前重绑和单次使用规则。没有复制第二套绑定/执行算法。

具体改动：

- 根据模型元数据恢复关系字段的 attname/列名及 FK 目标，检查其指向登记的目标模型；不从样本 ID 猜测关系。
- 查询构造始终从真实 `.objects` manager 出发，保留其过滤。manager 必须符合登记形状；CustomField 当前只接纳一个非否定的 `deleted_at IS NULL`，多出或减少条件都拒绝候选。
- 活体数据库核验增加软删除列的类型与可空性；保留主键、整数 FK、RLS、连接/事务等原有检查。
- 生成模板将连接键参数化；键名来自已审计字段元数据并作为 SQL 标识符转义。旧 Tag 模板委托给共用模板，原业务 SQL 保持一致。
- 指纹加入关系规格、目标/关联模型、manager 查询与实际 get_queryset 源码。旧候选不能用于另一种关系；manager 行为变化会导致拒绝或指纹失配。

CustomField 审计配置另行固定 SoftDeleteManager/SoftDeleteModel 的文件摘要，来源是同一冻结镜像；没有借用 Tag 的“无额外过滤”结论。规格、调用点映射与审计配置仍由人工登记；未知规格拒绝调用，已登记规格中条件不足时保留对应原 helper。

## 3. 查询保持与错误版本

八组 CustomField 场景均确认：候选 counts CTE 内的完整分组 SQL 与原 helper 第一条 SQL 逐字相同，参数保持；连接使用 `field_id`，输出保持主键全序与 Python 整数值。

实际分组 SQL 同时包含：

```sql
WHERE documents_customfieldinstance.deleted_at IS NULL
  AND documents_customfieldinstance.document_id IN (<原允许文档查询>)
  AND documents_customfieldinstance.field_id IN (<原目标字段键查询>)
GROUP BY field_id
```

该片段仅简写以便阅读，完整原始 SQL 与参数在证据中。允许文档子查询内部仍有自己的 `Document.deleted_at IS NULL` 条件。

实际执行两个错误版本：

| 错误 | 原计数序列（省略键） | 错误计数序列 | 结果 |
|---|---|---|---|
| 删除关联表的软删除过滤 | 2, 3, 2, 2 | 3, 3, 3, 3 | 差分检出 |
| 将 document_id 改为可空 value_int | 2, 3, 2, 2 | 0, 0, 0, 0 | 差分检出 |

这说明保留谓词和计数对象都是复用条件，不能只替换模型名称和 JOIN 键。

## 4. 验证结果与边界

新增 13 项 pytest：8 个完整结果场景、1 项包含两个错误 SQL 的检查、2 个 manager 变化检查、1 个跨规格候选检查、1 个缺失契约/查询变化检查。另运行原 Tag 绑定的 23 项检查及 B2 的 10 项回归，合计 **46 项通过**。

CustomField 场景包括空目标、无文档、无关联、全部关联软删除、全部文档软删除、两层删除与 NULL 业务值、固定种子的稀疏和密集关联。期望值在建数时独立累计；字段创建时间刻意与 pk 顺序不同，消费者仍按 pk 对比完整序列。

manager 变化测试分别移除删除过滤或增加额外过滤。旧候选未被执行，回退结果与变化后原 helper 的当前语义一致。跨规格测试将 CustomField 候选用于 Tag 输入，重新绑定后拒绝旧候选并执行 Tag 原路径。

固定数据、同一作用域、成功执行等环境声明依然是前提，不由本轮样本推导。完整 API、任意身份、额外 manager 谓词或新查询形状尚未准入。本轮没有性能测量，也没有为降低检查开销放宽条件。

## 5. 证据与下一步

- [CustomField 明细、SQL、核验事实与错误版本](../evidence/aggregate-reuse/20261104-8db9a3e5b787/tag-reuse.json)
- [Tag 绑定回归](../evidence/aggregate-reuse/20261104-8db9a3e5b787/tag-query-binding.json)、[B2 回归](../evidence/aggregate-reuse/20261104-8db9a3e5b787/tag-b2.json)
- [测试日志](../evidence/aggregate-reuse/20261104-8db9a3e5b787/probe.log)、[运行记录](../evidence/aggregate-reuse/20261104-8db9a3e5b787/run.json)、[代码/证据摘要](../evidence/aggregate-reuse/20261104-8db9a3e5b787/manifest.json)

复现：`python3 scripts/tag_binding_audit.py --reuse`。环境与依赖仍使用冻结镜像；一次性数据库、容器、网络及卷已清理，上游源码和历史证据保持原样。

当前可复用部分是“显式关系规格 + 保留原查询谓词 + 相同聚合回填规则 + 条件失效回退”。仍需讨论的难点是：哪些规格信息能从真实调用点与 ORM 元数据恢复，哪些语义必须继续显式审计。下一步可先离线提取这两个真实调用点的模型/字段配置，与当前人工注册表对照；提取结果先作为候选，不直接自动准入。这样检验的是减少人工绑定的能力，范围继续限定在同一模式内。
