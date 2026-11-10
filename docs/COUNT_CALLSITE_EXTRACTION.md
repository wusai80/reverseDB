# 计数调用点配置的离线提取

2026-11-04。**已从真实 TagViewSet 与 CustomFieldViewSet 自动提取模型、关联模型和 helper 关系键，两项均与当前人工规格一致。** 10 项离线检查通过。提取结果保持 review_only_keep_original，尚不自动修改注册表或准入完整 API。

这一步减少的是调用点配置的手工抄录。manager、实际 ORM/数据库语义和执行环境仍由既有[共享绑定机制](AGGREGATE_BINDING_REUSE.md)核验或由调用方显式声明。

## 1. 从哪些源码恢复

两个类都通过 `PermissionsAwareDocumentCountMixin.get_queryset` 调用 `annotate_document_count_for_related_queryset`。Mixin 把类上的 `document_count_through` 作为 through_model，把 `_get_document_count_source_field()` 的结果作为 related_object_field；后者返回 `document_count_source_field`。

提取器解析 views.py 的 AST，沿这个受限 mixin 结构读取直接声明的类属性。导入别名被展开为完整符号路径，不导入 Django 或上游应用，也不执行源码中的表达式。

| 真实调用点 | 自动提取目标模型 | 自动提取关联模型 | helper 接收的键 |
|---|---|---|---|
| TagViewSet | documents.models.Tag | documents.models.Document.tags.through | tag_id |
| CustomFieldViewSet | documents.models.CustomField | documents.models.CustomFieldInstance | field_id |

每项保留类、属性和 mixin/helper 调用行号、原表达式、源文件 SHA-256、queryset 表达式和声明的排序。CorrespondentViewSet、DocumentTypeViewSet、StoragePathViewSet 没有本地 through 配置，记录为不属于本次提取子集；不据此判断其代码或能力无用。

## 2. 如何对照人工规格

使用同一离线 AST 方法读取当前 `RelationSpec` 字典，比较目标模型与关联模型的符号路径。人工规格里的关系字段为 `tag`/`field`，源码 helper 接收的是 `tag_id`/`field_id`，两者不能仅凭字符串后缀猜测对应。

字段 attname 来自此前实际 ORM 绑定的归档记录。本次校验了归档元数据文件摘要，以及注册表源码、模型源码和 helper 源码的对应摘要；它们不一致就不再使用该历史字段证据。同一规格多个归档记录若出现不同 group_key，也拒绝对照。

最终两个候选均得到 `matches_registered_relation`。这只表示：**提取的声明与当前人工规格及对应历史字段证据一致**。历史元数据不是新运行环境的准入事实；进入实际执行时仍需要实时绑定和执行前重核验。

## 3. 没有跨过的消费者边界

真实源码中的 queryset 排序分别为：

- TagViewSet：`Lower("name")`。
- CustomFieldViewSet：`"name"`。

现有区域实验输出的是按 pk 排序的完整整数对。因此，即使模型和关系字段完全匹配，两个候选的 `consumer_admission` 仍为 `not_established`。本轮没有把 API 名称排序替换为主键排序，也未接管分页、Tag 树构造或请求权限流程。

结果另列出未确定项：运行时 MRO/类装饰器、`super().get_queryset()` 语义、请求过滤/权限与消费者、实时 ORM/schema/manager，以及执行环境契约。这些不是补齐几个类属性就能消除的条件。

## 4. 支持范围与拒绝情况

实现：[callsite_extraction.py](../prototype/aggregate_reattach/callsite_extraction.py)。当前仅支持直接声明的类属性与已审计的 mixin 方法体语法，不是通用 Python 继承或数据流分析器。

支持显式导入别名和普通/带类型注解的属性赋值。下列情况保留待审查状态或明确报告不匹配：

- 模型或字段由函数调用等动态表达式提供。
- 同一属性缺失或被多次赋值。
- 类本地覆盖 get_queryset 或关系字段 getter。
- mixin 的调用参数/方法体偏离当前识别结构。
- 导入符号在模块层被重新绑定。
- 提取出的 helper 键与审计字段 attname 不一致。
- 对照依据对应的源码版本已变。

未实现一般运行时写入检测；也不会执行元类、装饰器或动态属性求值来补齐信息。提取器允许保守地漏掉等价写法，避免静默选取不明确的声明。提取覆盖率与运行时正确性是两个不同验收项。

## 5. 验证与产物

10 项本地 unittest 通过，覆盖真实源码、导入别名、错误关系键、动态模型/字段、重复赋值、本地覆盖、mixin 改写、导入重绑、缺少 attname 证据和旧证据失效。动态表达式测试中的工厂函数不存在，提取器仍能返回待审查记录，因为它不执行这些表达式。

- [候选、来源、排序与规格对照](../evidence/count-callsites/20261104-source-v0/candidates.json)
- [检查摘要](../evidence/count-callsites/20261104-source-v0/validation.json)、[代码与来源摘要](../evidence/count-callsites/20261104-source-v0/manifest.json)
- [命令入口](../scripts/inspect_count_callsites.py)、[测试](../tests/test_callsite_extraction.py)

复现：

```sh
python3 scripts/inspect_count_callsites.py
python3 -m unittest discover -s tests -p test_callsite_extraction.py -v
```

本轮没有新数据库或性能运行，未修改上游源码、人工注册表或已有归档证据。

## 6. 下一步边界

可从源码减少手工登记的内容已经明确：目标模型、关联模型、传给 helper 的关系键，以及这些信息的来源链。仍需显式审计的内容包括 manager 语义、适用消费者、权限范围与环境前提。

下一步可将已审查的提取候选送入实时 ORM 绑定，用元数据解析 helper 键到实际关系字段，核对来源版本，并与现有手工规格进行并行对照。先保持原主键排序区域实验，不自动接管完整 API；若任一来源、消费者或语义条件无法对应，继续原路径。这将验证候选能否实际替代配置抄录，而不是只在报告里重复已有规格。
