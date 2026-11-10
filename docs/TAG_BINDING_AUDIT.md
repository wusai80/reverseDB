# Tag helper 的契约绑定核对

后续进展：[B2 区域改写与差分验证](TAG_B2_RESULTS.md)及[实际查询绑定与执行前核验](TAG_QUERY_BINDING.md)已完成。本文保留最初人工绑定核对阶段的证据与结论。

2026-11-03。结论：**下述单个入口可进入固定数据、成功执行范围内的 B2 实验；完整 API 尚不能替换。** 本轮执行原实现的绑定探针，未实现候选 SQL，未测量性能。准入依据为[通用契约](AGGREGATE_REATTACH_CONTRACT.md)加固定源码、实际查询、模型/数据库约束和运行环境；不是仅凭一组数据相同。

## 1. 固定入口与输出

```python
annotate_document_count_for_related_queryset(
    Tag.objects.order_by("pk"),
    Document.tags.through,
    "tag_id",
    user=active_superuser,
).values_list("pk", "document_count")
```

输出立即完整求值为按主键升序的 `(int, int)` 序列。消费者不进行数据库端计数算术、不分页、不读取结果列 OID，也不把 queryset 返回给其他调用者后再执行。目标行仍全部保留，包含零计数标签。

绑定仅覆盖 active superuser、上述查询形状和独立无并发写入测试库。保留原 `permitted_document_ids` 查询，不据本例删除普通用户、组授权或版本文档的权限能力。后续扩大调用范围须另外绑定，不能将当前结论推广到任意用户/queryset。

固定环境来自既有 B0 镜像：Paperless commit `1b8b861a6072fd85aad68847c0d312d7a9fd2ca0`、Django 5.2.17、PostgreSQL 18.6。运行器检查原始镜像与实验输入锁，不重新构建或拉取镜像。新探针复制到本轮一次性容器；上游源码、旧 B0/B1 harness 与既有证据均未修改。

## 2. 适用条件核对结果

| 契约项 | 结果 | 实际证据与限制 |
|---|---|---|
| 封闭区域 | 通过 | helper 的映射仅用于空分支与 CASE；选定消费者只取两个整数列 |
| T / K | 通过 | T 为完整 Tag 查询，K 为其 pk 成员关系；无切片、分组或集合运算。实际 IN 子查询未保留无关排序，外层明确 `ORDER BY 1 ASC` |
| L 的隐含过滤 | 通过，仅此 through 模型 | 自动生成的 `Document.tags.through` 使用标准 `django.db.models.manager.Manager`；实际 L 查询无 WHERE，manager 源码已保存。不能外推到 `CustomFieldInstance` |
| A 的权限/删除边界 | 通过，仅此身份入口 | 原 A 子查询保留 root_document 连接和 `deleted_at IS NULL`；合成已删除文档的关联没有计入。未简化该查询 |
| 键、NULL 与重复 | 通过 | 两个 FK 均为非 NULL integer/AutoField；实际迁移数据库存在 `(document_id, tag_id)` 唯一约束。仍保留 COUNT DISTINCT；不因本 through 表唯一就扩展新的消重规则 |
| 聚合唯一性 | 通过 | 实际 SQL 仅按 tag_id 分组，`GROUP BY 1`；与 T 每行最多匹配一个计数 |
| 会话/行安全 | 通过，仅本实验库 | 三张相关表的 RLS 与 FORCE RLS 均为 false；数据库 alias 为 default，连接仅指向隔离 db 服务 |
| 类型观察 | 通过，仅上述消费者 | 原 CASE 的 SQL 类型随值变化，但读取后均为 Python int；新 COUNT 可保留 bigint，继续采用等效整数转换。候选不得增加 int32 强制转换 |
| 顺序 | 通过，仅此入口 | 主键排序形成确定序列；并列名称不影响该序列。API 按名称/计数排序仍未准入 |
| 快照与惰性求值 | 环境前提已落实 | 本轮先完成全部数据写入，再构建/求值；只有测试进程访问新库，无其他写入者。隔离级别仍为 Read Committed，不声称其自身提供跨命令快照一致性 |
| 故障及副作用 | 不在本轮证明范围 | 原 helper 构建时读聚合、求值时读 CASE 的时机已确认；正常成功结果实验不能证明取消、超时、查询故障或授权拒绝时等价，也不提供生产切换准入 |

上述“通过”表示为受控 B2 实验确定了输入和观察边界，**不是 B2 生成物已通过验证**。本轮没有新增运行时优化器或自动准入检测器。

## 3. 类型差异的处理

在冻结运行时中，用与源 helper 相同的 `Case/When/Value/IntegerField` 构造合成计数表达式，观察 PostgreSQL cursor description：

| 表达式 | 观察到的 SQL 类型 |
|---|---|
| CASE，计数为 0、1、32767、32768、2147483647 | integer，OID 23 |
| CASE，计数为 2147483648、9223372036854775807 | bigint，OID 20 |
| 空映射分支 `Value(0, IntegerField())` | integer，OID 23 |
| `COUNT(DISTINCT d)` 与 `COALESCE(COUNT(DISTINCT d), 0)` | bigint |

大数来自表达式参数探针，**不是生成了数十亿条文档或实际计算了那么大的分组**。该检查用于定位类型边界。

同时保存了冻结 Django 的参数适配和结果转换源码及其文件哈希：`IntegerField.get_db_prep_value` 调用 PostgreSQL 参数适配；整数表达式的 `convert_value` 对非 NULL 结果执行 `int(value)`，不执行 int32 截断。实际普通映射、零分支和七个合成边界值均核对了值与 Python 整数结果。

因此，在当前消费者只观察整数值的前提下，SQL integer/bigint 差异不要求把 COUNT 压回 int32。候选必须保持完整计数值、零值与整数转换。若后续加计数算术、函数重载、数据库类型观察或不同序列化转换，需重新核对；例如数据库端 integer 运算与 bigint 运算的溢出边界不同，本报告不授权将两者互换。

## 4. 实际执行与证据

探针使用三个标签、两个文档：两个不同 owner 的标签同名，一个标签零关联；一个文档正常、一个软删除。原实现输出 `[(1,1), (2,1), (3,0)]`。计数构建阶段一条 GROUP BY，求值阶段一条 CASE；另行核对非空目标的空映射分支和空目标查询。

首轮定位类型差异后，补充运行时转换源码、数据库唯一约束及 RLS 证据，再运行同一个探针。最终 **1 项 pytest 通过**；两轮是证据补全，不计作两个独立覆盖场景。运行耗时不作为性能指标。

- [最终原始报告](../evidence/tag-binding/20261103-01d29f9dd193/tag-binding.json)：完整 SQL、合成参数、类型 OID、manager/转换源码、约束与环境。
- [运行记录](../evidence/tag-binding/20261103-01d29f9dd193/run.json)、[pytest 日志](../evidence/tag-binding/20261103-01d29f9dd193/probe.log)、[证据清单](../evidence/tag-binding/20261103-01d29f9dd193/manifest.json)：冻结镜像、命令、文件摘要及清理状态。
- [探针](../prototype/aggregate_reattach/test_tag_binding.py)、[运行器](../scripts/tag_binding_audit.py)：可重复执行 `python3 scripts/tag_binding_audit.py`。

每次新建独立 Compose 项目，内部网络、不发布端口、不挂载宿主目录或既有数据库；采集后删除本轮应用容器及服务、网络、卷。最终记录 `status=passed`、`cleanup_errors=[]`。

## 5. 下一步的具体边界

可以开始 B2：只实现该入口的数据库内聚合与回填，保留原 A/K/L 和输出排序，先比较完整整数序列，再使用零计数、删除范围与错误变体检验。B2 的普通成功执行范围必须与本报告一起记录；不沿用 B1 的异常豁免，不接入请求权限流程。

必须拒绝或保持未知的推广：完整 Tag API、任意权限身份、CustomField manager、非唯一排序分页、返回可长期重用的惰性 queryset、并发写入与异常等价。收益比较在候选正确性验证后进行；当前没有加速结论。
