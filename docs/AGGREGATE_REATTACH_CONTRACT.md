# 聚合映射回填的变换契约 v0

日期：2026-11-03。契约形成时完成源码边界核对与条件化关系推导，尚未生成候选 SQL。本文落实 [当前计划](PLAN.md) 的第一步，约束 [原型方案](PROTOTYPE.md) 中的 B2/B3。

后续进展：[单个 Tag helper 绑定核对](TAG_BINDING_AUDIT.md)与[受控 B2 实验](TAG_B2_RESULTS.md)已完成；完整 API 等价仍未证明。本文下方清单与下一步描述保留契约形成时的通用义务和进度，不代表其他调用者已被准入。

研究对象只有一种结构：**数据库分组聚合 → 应用键值映射 → 同一映射作为常量回填后续查询**。候选变换把聚合结果保留为数据库关系，在原回填位置关联。适用性由程序、查询结构和明确的环境条件决定，不由测试中出现了哪些数据决定。

## 1. 已核对的实例与边界

固定源码：Paperless-ngx `1b8b861a6072fd85aad68847c0d312d7a9fd2ca0`，本轮核对工作树无修改。依赖锁定 Django 5.2.17；既有 B0 trace 记录 PostgreSQL 18.6、Read Committed、无并发写入。

| 位置 | 源码事实 | 对变换的约束 |
|---|---|---|
| [permissions.py:543](../sources/paperless-ngx/src/documents/permissions.py#L543) | `annotate_document_count_by_ids` 按关系键分组，`Count(target_field, distinct=True)` | 保留两个成员资格过滤、计数目标列与去重语义 |
| 同函数 | 字典推导式立即遍历 `counts`；空字典回填 0，否则构造 `Case/When` | 聚合读取先发生；映射只供空分支和 CASE 构造使用，不返回给调用者 |
| 同函数 | 返回的带注解 queryset 尚可被调用者过滤、排序和分页 | 回填位置必须保留；不能把最终页提前当作聚合输入 |
| [permissions.py:497](../sources/paperless-ngx/src/documents/permissions.py#L497)、`:613` | 包装器用 `permitted_document_ids(user)`；文档权限涉及 root document，默认排除删除文档 | 复用原权限查询，不重新猜测权限规则；Tag 可见性另外保留 |
| [views.py:510](../sources/paperless-ngx/src/documents/views.py#L510) | mixin 从基础 queryset 调用计数包装器 | 不能把此处计数与外部请求授权检查合并或调换 |
| [views.py:1000](../sources/paperless-ngx/src/documents/views.py#L1000) | selection 路径先把文档 ID 变成列表，再供多个模型复用 | 列表是已固定输入；本次变换不把它重新替换成实时权限查询 |
| [models.py:1309](../sources/paperless-ngx/src/documents/models.py#L1309) | `CustomFieldInstance` 继承 `SoftDeleteModel` | 关系输入包括 manager 的实际过滤，不能只取物理表；具体 manager SQL 尚待绑定核对 |

[既有 B0 SQL](../evidence/paperless-b0/20261102-224105-1d6b9197b6f6/b0-trace.json) 已观察到 Tag 关系的 `COUNT(DISTINCT document_id) ... GROUP BY 1`，随后执行 CASE 查询；部分 API 查询仅按 `name` 排序。该证据确认此调用实例的模式，不证明所有动态 queryset 都满足条件。源码的 name 约束按 owner 分组，不能据此推出跨 owner 的全局排序唯一。

原 helper 注释说明它曾为规避相关子查询和直接 M2M 聚合的不良计划而采用当前实现。这是原实现的设计理由，不是本项目已验证的性能结论。新变换可能更慢。

## 2. 参数化输入与可观察结果

在同一固定状态 S 下定义：

- `T`：进入 helper 的目标查询结果，按多重集处理；每行有非 NULL 整数键 `k`。即使外层连接产生相同键的多行，也不得去重这些行。
- `K`：原表达式 `queryset.values("pk")` 所确定的键集合。必须保留其完整查询语义；不能随意替换为最终页面的键。
- `L`：`through_model.objects` 的有效关系，包含 manager、租户、软删除等实际谓词；有列 `k,d`，允许重复关联记录。
- `A`：调用者传入的有效目标 ID 成员集合。列表保持原值；queryset 保持原查询、参数和执行上下文。它不一定就是“全部可见文档”。
- `H`：回填之后的消费者，包括后置过滤、投影、排序、分页、序列化和树构造。首版只接纳已核对的消费者。

基础观察对象是完整的 `(原行字段, document_count)` **多重集**。提升为接口等价还须证明 H 保持相同值、对象转换、顺序和分页结果，以及原权限允许/拒绝决策。不能把数组重新排序来掩盖差异。

SQL 条数、传输字节、映射分配和时间是优化指标，不要求相同。业务写入、外部消息、审计副作用、锁协议和异常行为不在该关系推导中被消除或自动豁免。

## 3. 唯一候选规则

令 `eligible(k,d)` 表示 L 中该行同时满足原来的 `k IN K` 与 `d IN A`，以 SQL 谓词为 TRUE 为准。定义：

```text
G = GROUP BY k (
      SELECT eligible rows FROM L
      AGGREGATE COUNT(DISTINCT d) AS n
    )
M = { g.k : g.n | g in G }

原形式：R0 = T 中每行 r 追加 M.get(r.k, 0)
新形式：R1 = T LEFT JOIN G ON T.k = G.k
             投影原 T 全部字段，并追加 COALESCE(G.n, typed_zero)
输出：H(R0) 或 H(R1)
```

数据库表达式的结构模板如下，**不是可执行 SQL，也不是已经通过验证的编译产物**：

```sql
WITH target_rows AS (<原输入查询>),
target_keys AS (<原 queryset.values('pk') 的成员关系>),
allowed_ids AS (<原 A；已物化列表仍用其原值>),
effective_links AS (<原 L，包含 manager 过滤>),
counts AS (
  SELECT l.k, COUNT(DISTINCT l.d) AS n
  FROM effective_links l
  WHERE l.k IN (SELECT k FROM target_keys)
    AND l.d IN (SELECT d FROM allowed_ids)
  GROUP BY l.k
)
SELECT <原 t 字段>, <经类型核验的 COALESCE(c.n, 0)> AS document_count
FROM target_rows t LEFT JOIN counts c ON t.k = c.k
-- 原 H 在此逻辑结果之上继续应用；不是把 LIMIT 提前放入 counts。
```

用成员资格表达过滤，避免把权限关系中的重复行传播给外层。CTE 不代表强制物化，也不代表性能保证。SQL 标识符只能来自核验过的模型元数据，值必须参数化。

## 4. 条件化语义推导

前提：两端输入 T/K/L/A 相同，G 对 k 唯一，键比较语义一致，计数与零的类型转换对消费者等效，且两端成功完成计算。

对 T 中任意一行 r：

1. G 存在键 r.k 时，唯一对应行的 n 就是原字典值；CASE 命中同一个键，左连接也只匹配该行，两端追加相同 n。
2. G 不存在键 r.k 时，CASE 的 default 为 0；左连接提供 NULL，经 COALESCE 得到相同的类型化零。
3. G 每键最多一行，左连接不会扩增或删除 T 的行。上述结论对 T 的每一次行出现分别成立，所以保留多重性。
4. 空 G 是第二种情形的特例，覆盖原空字典分支。原字段投影不变，因此 `R0 = R1`，等号为多重集相等。
5. 若 H 是对该结果的相同、确定且类型兼容的计算，则 `H(R0) = H(R1)`。顺序/分页另须满足下表条件，不能从多重集相等直接推出。

这是对任意满足前提的数据规模和值的推导，非“20% 数据通过所以其余也通过”。它也不是机械化证明：Python/Django/SQL 到上述模型的映射义务仍须逐条核验。

## 5. 准入条件与当前证据

`已核对` 只指该项源码或旧 trace 事实；`待核对` 与 `环境前提` 均不能被优化器当成自动成立。任何必要条件未知，保留原实现。

| 条件 | 核验方式与首版限制 | 当前状态 |
|---|---|---|
| 区域封闭 | 数据流确认映射仅用于零判断与 CASE；不逃逸、无额外统计/日志消费者 | helper 源码已核对；自动识别器未实现 |
| 输入关系一致 | 保留 K/A/L 的全部谓词、参数、manager、DB alias、会话授权与 RLS 上下文；禁止自行放宽权限 | Tag 旧 trace 部分已核对；完整绑定待核对 |
| 键与分组唯一 | 非 NULL 整数键、无自定义转换；SQL 相等与 Python 键相等一致；实际 G 只按 k 分组 | Tag 旧 SQL 分组已核对；模型/运行时元数据准入待实现 |
| 重复与 NULL | 保留 COUNT DISTINCT 和 SQL IN；A 的重复不放大计数，NULL 不当成普通相等值；首版键/目标列限定非 NULL | 源表达式已核对；NULL 扩展不在 v0 范围 |
| 外层行多重性 | 仅与每键唯一 G 左连接，不额外 DISTINCT，不把缺失计数行改成内连接 | 抽象规则已满足；生成 SQL 待核对 |
| 数值与转换 | 验证原 CASE 参数类型、DB 表达式类型、Django output field、驱动和消费者；不能无条件强转 int32 | 待核对，不以小样本的 Python int 相同替代 |
| 查询组合位置 | 在原注解位置回填；首版拒绝未建模的输入切片、分组、集合运算、窗口、raw/extra、自定义表达式及已求值缓存复用 | 待核对具体入口；不声称通用 QuerySet 支持 |
| 快照与输入冻结 | 从解析 A、聚合到所有消费/重新求值，依赖数据、参数和权限上下文固定；同事务内也不能有相关自写入 | 仅作为实验环境前提；旧 B0 为无并发写入，不是生产保证 |
| 顺序与分页 | 原消费者具有充分确定的顺序；并列项若可区分，必须已有确定规则。不得给候选单独加 PK 或事后排序 | 完整 API 未通过准入；旧 trace 的 name-only 排序存在待解条件 |
| 效果与调用时机 | 不跨权限决策、事务/锁边界；无 volatile 查询、读副作用或额外外部观察；返回 queryset 的再执行也须受控 | helper 局部映射已核对；调用上下文待核对 |
| 错误行为 | 本推导仅针对双方成功求值，不能推出查询故障、取消、超时、资源耗尽时等价 | 未证明，未沿用 B1 异常豁免 |

PostgreSQL `count` 返回 bigint；源 `IntegerField()` 标注本身不足以证明数据库端 CASE 类型或后续运算类型相同。候选可能改变后续整数运算的类型/溢出行为，必须分析实际消费者后确定转换与值域；不要为对齐标注而盲目加 `::integer`。[PostgreSQL 聚合文档](https://www.postgresql.org/docs/18/functions-aggregate.html)

Django 查询构造与求值分离；原字典推导会触发聚合，返回的注解 queryset 可稍后才执行。Read Committed 下多条命令可看到不同快照，因此“放在同一事务里”不足以满足本契约。固定数据实验可满足前提；若用 Repeatable Read，需要覆盖所有依赖读取、禁止相关自写入并控制会话状态，不可只给候选修改隔离级别。[Django 求值文档](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#when-querysets-are-evaluated)、[PostgreSQL 隔离文档](https://www.postgresql.org/docs/18/transaction-iso.html)

只有 count 或 name 的排序可能有并列项；查询计划改变后，并列行的分页位置可能不同。契约要求原有充分确定的排序，或明确限定为多重集输出的区域实验。调整两端公共排序属于另一个行为契约变更，须单独处理。[PostgreSQL 分页文档](https://www.postgresql.org/docs/18/queries-limit.html)

## 6. 用来反驳错误规则的最小情形

以下是逻辑反例与后续测试设计，本轮未执行新数据库测试。符号表示任意合法键，不参与改写决策。

| 错误规则 | 最小情形 | 区别 |
|---|---|---|
| LEFT JOIN 改 INNER JOIN | T 有 a，但 L 无符合条件的 a | 原输出 `(a,0)`；错误版本丢行 |
| COUNT DISTINCT 改 COUNT | L 含两条 `(a,x)`，A 含 x | 原为 1；错误版本为 2 |
| 忽略 A | L 有 `(a,x)`，x 不在 A | 原为 0；错误版本为 1，可能泄露不可见数量 |
| 忽略有效 L 的过滤 | 某关联被 manager 排除，物理表仍保留 | 原不计入；错误版本计入 |
| 字典来源不唯一仍直接连接 | 来源含 `(a,1)` 与 `(a,2)`，Python 覆盖前值 | 原映射最终只有一项；连接产生两行；此结构不满足规则 |
| 排序分页提前 | T 有 a、b，计数分别 0、1；先按键取一行，再按计数降序 | 正确第一项 b；错误版本只能返回 a |
| 忽略读取时刻 | 原先计算 a 的计数为 1，随后关联被删除，再执行外层查询 | 原仍回填 1；合并查询在删除后得到 0 |
| 把部分排序当作全序 | a、b 同名且字段不同，page_size=1 | 两个合法计划可能选不同第一行；多重集论证不保证页面相同 |

对 DISTINCT 的反例属于允许重复关联的通用关系模型。若具体 Tag through 表禁止重复，应在允许重复的独立夹具验证该规则，而不是破坏上游约束来制造 Tag 数据。

## 7. 本步结论与下一步停止线

现在可以支持的结论：**在列明前提下，用唯一聚合关系的左连接替换 map/CASE，保持回填结果的多重集语义。** 尚不能支持“Paperless API 已可透明替换”，尤其类型、默认 manager、消费者排序和读取生命周期仍有绑定义务。

下一步只做一个 Tag helper 入口的绑定核对：原 SQL/参数/类型、K/A/L 与 manager 谓词、最终消费者与已有排序、固定数据生命周期。先输出条件清单的通过/拒绝/未知结果；只在条件闭合后实现 B2，并分别验收区域结果与 API。未知条件不靠增加随机用例冲掉。

第一轮区域实验可沿用旧 trace 中显式 `order_by("pk")` 的计数映射读取，避免把完整 name-only API 排序问题混入关系验证；这不会自动授权改动 API 排序。故障行为仍单独保留，任何正常执行范围的准入必须显式记录，不能引用 B1 的豁免来替代。

后续再比较强 B1 与 B2 的总成本。该关系恒等式本身不是新颖性主张；潜在研究贡献须来自可检查的跨层识别/准入、受控生成及相对强基线的实证结果，目前尚未完成这些工作。
