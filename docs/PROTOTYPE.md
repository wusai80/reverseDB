# ReverseDB-0：Paperless 计数区域的可验证实验

当前定位（2026-11-03 更新）：用户确认收敛回 ReverseDB 的跨应用/数据库计算变换。本文的只读计数区域重新作为首个实例；研究对象是“聚合 → 应用层键值映射 → 查询回填”的通用结构，而非具体数据值。先明确适用条件和语义保持论证，再执行差分与收益比较，当前执行顺序以 [PLAN](PLAN.md) 为准。

范围限制：不新增通用架构恢复或全应用能力证明任务。[变换契约 v0](AGGREGATE_REATTACH_CONTRACT.md) 已给出关系模型、适用条件与条件化多重集语义推导；单个 Tag helper 的绑定及 B2 结果对照已完成，完整 API 顺序与端到端等价仍未证明。原权限决策与外部副作用保持边界。条件无法确认的区域保留原路径，不以测试样本相同作为自动改写依据。下文通用 IR/SQL 为设计方案，实际受限 B2 见[结果报告](TAG_B2_RESULTS.md)；B3 未实现。

状态：B0 隔离运行已完成；2026-11-03 B1 权限阶段避免提前计数的实验原型完成 45 组正常执行差分、110 项上游回归，并保留一个异常语义反例，见 [运行结果](BASELINE_RESULTS.md)。限定 Tag helper 的 B2 执行器已完成 8 组差分及错误版本/回退验证，共 10 项测试通过；随后完成[本机强基线成本初筛](TAG_COST_RESULTS.md)，结果随场景变化，尚未完成独立重复收益验证或 B3。当前基础运行边界见 [RUNTIME](RUNTIME.md)。

2026-11-03 绑定核对：原 Tag helper 的主键排序、整数值读取入口已在冻结镜像和新建测试库中核对，1 项探针通过；类型/manager/快照与消费者边界见[报告](TAG_BINDING_AUDIT.md)。该入口可进入固定数据、成功执行的 B2 区域实验，完整 API 尚未准入。

用户确认的 B1 契约：固定数据、无并发写入、无查询故障的正常执行基线。故障注入时 B0 500 → B1 403 的差异已知且保留，不声称异常行为等价。这个限定不能自动推广到后续任意优化或并发场景；报告收益必须明确适用前提。

## 1. 最小优化区域

源区域：`src/documents/permissions.py:543–632`，由 `TagViewSet` 的 count mixin 调用。原始流程是 grouped count → Python counts_by_pk → Case/When annotation。

首版仅尝试把 count 关系保留在数据库中。保留 TagViewSet 的子树收集、children_map、分页和 serializer，避免同时改动多个变量。不把手工 SQL 包装成“已自动生成的编译器”。

输入契约：经过授权的请求用户、目标 Tag queryset 语义、Document.tags through relation、可见文档 ID relation、排序/过滤条件、固定数据库快照。输出：与原查询相同的 tag 集合、相同整数 document_count（包括 0），并支持原来的 count 排序和分页行为。

语义约束：owner/unowned/user/group 权限、停用用户、active superuser、root_document 授权、soft delete、DISTINCT 计数、空集合、零计数都必须保持。Tag 可见性与 Document 可见性分开表示。禁止从请求拼接 SQL；字段/表来自固定允许清单，值参数化。

## 2. 计划的 IR 与生成边界

```text
Region: tag_counts
Read: Tag, Document, Document.tags.through, User/Group grants
AuthorizedDocs = DocumentVisibility(user, root_document, soft_delete)
TargetTags = TagRelation(filter_contract)
Counts = GroupDistinctCount(through ⋉ TargetTags ⋉ AuthorizedDocs, tag_id, document_id)
Result = LeftJoin(TargetTags, Counts).Project(tag_fields, Coalesce(count, 0))
Effects: read-only SQL; permission/tree caches remain explicit external boundaries
Consistency: frozen data or identical read-only snapshot contract across implementations
```

以上是**通用受限关系 IR 的设计目标**。当前 `regions.json` 是人工挑选的证据索引；`inspect_regions.py` 输出语法调用位置，尚不能从任意代码自动证明上述变换合法。[最小转换接口](TRANSFORM_INTERFACE.md)已实现单一 AST 模式的角色恢复与准入计划，随后接入实际 ORM/元数据核验及共用 B2 生成模板，并由 [Tag/CustomField 两个关系实例](AGGREGATE_BINDING_REUSE.md)验证复用。调用点与模型字段规格仍人工登记，尚无任意 ORM 绑定或完整 B3。

早期候选 SQL 形状（设计示意，不是已捕获/已执行 SQL；当前更严格的模板与准入条件以[变换契约](AGGREGATE_REATTACH_CONTRACT.md#3-唯一候选规则)为准）：

```sql
WITH authorized_documents AS (...权限关系...重用原语义...),
target_tags AS (...目标标签关系...),
counts AS (
  SELECT r.tag_id, COUNT(DISTINCT r.document_id) AS n
  FROM document_tag_relation r
  JOIN target_tags t ON t.id = r.tag_id
  JOIN authorized_documents d ON d.id = r.document_id
  GROUP BY r.tag_id
)
SELECT t.*, COALESCE(c.n, 0) AS document_count
FROM target_tags t LEFT JOIN counts c ON c.tag_id = t.id;
```

实际列名、权限子查询、SQL 编译、NULL 与去重规则以固定版本生成的真实 SQL 为准。若使用临时表/物化关系，创建与清理成本计入总耗时；不能只计最终 SELECT。CTE 是否物化由实际 PostgreSQL 版本和执行计划确认，不预先宣称更快。

## 3. 必须比较的基线

| 实现 | 定义 | 作用 |
|---|---|---|
| B0 | 固定版本原始 helper 和完整 API | 真实起点，包含已有批量优化 |
| B1 | 常规 ORM 调整：合法 filter 下推、复用计数、减少无谓物化 | 排除简单工程优化的解释 |
| B2 | 手工 grouped join/CTE 专用 SQL | 衡量数据库内执行的上限与往返收益 |
| B3 | 从受限、可审计 IR 生成与 B2 同契约的 SQL | 衡量生成正确性、覆盖率与自动化成本 |

先实现/验证 B0–B2，再逐步完善受限转换机制。用户明确首版尚未专门优化，当前成本初筛不作为推进 B3 的门槛；自动化、正确性与性能收益分别评估。B3 仅自动化达到 B2 时如实报告，不要求或暗示一定能更快。

## 4. 正确性优先

使用同一合成数据快照，比较 count helper 的完整 `(tag_id,count)` 映射，再比较完整 `/api/tags/` 响应。序列顺序是契约的一部分；只在明确不可观察的 JSON 对象键序上规范化，不对数组排序掩盖错误。

覆盖：

- 无标签/无文档/零匹配、标签无文档、同一文档多标签。
- owner、unowned、直接授权、组授权、两种授权重叠、无权限、停用用户、active superuser；HTTP 拒绝情况也比较。
- root document 与 version 的授权不同，软删除文档，隐藏父/可见子、可见父/隐藏子。
- 树深 1/3/5，名字大小写与重复排序键，文档计数排序，`is_root` 过滤。
- page_size 1/25/100，分页边界；API v9/v10 的 all、display_count、count、results 与 children。
- 缺少 map 的 serializer fallback 不在首次替换范围；不能因优化 list 破坏 detail 接口。

先复用上游 `test_tag_hierarchy.py` 与 `test_permission_filtering_security.py`，再添加区域差分 fixture。故意删除一个授权谓词或错误改变 count=0 行为时，差分检查必须能失败。

原多语句读与单条 SQL 在并发更新下可能观察不同快照：首轮比较固定快照；后续单独比较权限撤销、文档/标签变化和树缓存失效，不把固定快照的结果推广成并发透明等价。

## 5. 实验矩阵与指标

第一批：tags=100/1K/10K，documents=1K/10K/100K，平均每文档标签数=1/5，树深=1/3/5，可见比例=1%/50%/100%。先用约 12 个有代表性的组合，避免全笛卡尔积。随机种子固定 42、43、44，记录实际 tag-document 边数和权限表行数。

并发 1/8/32；先本机隔离容器，再增加应用与数据库间 0/1/5ms RTT 的受控实验。分别记录树/权限缓存冷态与热态。原版本和优化版本使用相同索引、内存限制、连接池、隔离级别；不能偷偷新增索引只给候选实现。

记录 SQL 数、SQL/参数字节、返回行数、DB execution/planning time、BUFFERS、Python CPU/峰值内存、helper 耗时、完整 HTTP p50/p95/p99 与吞吐。重型 trace 单独跑，常规计时关闭 trace；每组预热后至少 5 个独立重复，保存原始样本。p99 需要足够请求量，不能用几十次样本声称稳定尾延迟。

新增 costs：IR 分析/验证/生成耗时，首个查询编译成本、缓存建立成本都单独报告并计入摊销。语义失败率必须为 0。

## 6. 验收与可证伪性

1. 动态 trace 确认 count 字典/CASE 构建确实发生，且是值得优化的成本；否则本候选不通过。
2. 差分 fixture 与原有相关测试通过，原始结果/SQL 可重放；否则不能发布性能结论。
3. 相对 B1 的收益在重复实验中稳定，说明收益来源（往返、对象物化、SQL planning 或扫描量）。未达到就报告无显著收益。
4. B3 生成物与 B2 对比。只改善自动化时记录生成成功率、人工约束数与验证成本。

初始设计验收到：真实源码证据、可复现版本和明确的可执行实验设计。后续已完成 [B0 小型基线](BASELINE_RESULTS.md)，但尚无 benchmark 比较或加速比结果，不预设“至少 N 倍”的目标。
