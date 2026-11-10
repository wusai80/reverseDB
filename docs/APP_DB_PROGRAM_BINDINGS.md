# 两个研究问题的程序绑定

2026-11-04。本轮完成[研究方案](APP_DB_RESEARCH_PROBLEMS.md)的程序绑定关：人工对应真实源区域与形式模型，区分源码事实、历史运行证据、环境假设和未知条件。没有实现新的数据库变换或开展性能测试。

**后续进展：** 已完成[限定数据库测试](APP_DB_SMALL_DATABASE_TEST.md)，标量桥接和明确分页消费者在实验夹具中通过。下列判定保留绑定阶段的证据状态；数据库实验没有将真实 API 或通用运行上下文的 unknown 改为 accept。

复现入口为 [binding_review.py](../prototype/app_db_questions/binding_review.py)，记录为 [binding-review.json](../evidence/app-db-bindings/20261104/binding-review.json)。脚本定位人工指定的 17 个源码锚点、记录 18 个文件摘要，核验反编译来源与既有审计来源，计算两个语义模型反例。文本锚点核验不是语义推理或通用程序分析器。

```bash
python3 prototype/app_db_questions/binding_review.py
```

## 绑定结果

| 对象与候选 | 判定 | 成立的范围或尚缺的义务 |
|---|---|---|
| P1 StockLevel 标量关系化 | unknown，保持原路径 | 依赖区域闭合；类型桥接、实际数据库上下文及候选表达式仍需闭合 |
| P1 Tag 聚合/map/CASE | 历史实验有条件 accept；当前运行 unknown | 源码与 8 组旧差分来源匹配；不把旧环境事实当作当前实时绑定 |
| P2 既有完整主键序整数对消费者 | 需求规则 accept，但无缩减 | 输出需要全部目标的计数，需求键集就是全部目标键 |
| P2 明确切片的消费者夹具 | 数学模型 accept；数据库绑定 unknown | 仅消费固定顺序切片与计数；尚无真实数据库消费者实现 |
| P2 Tag 整个接口共用一个“根页面键集” | reject | 接口有两个聚合生产者，子标签消费者仍需要页面外的键 |
| P2 仅缩减 Tag 第一次聚合 | unknown | 上一条反例不能否定这个更小区域；需分别分析每个生产者的行需求与值需求 |

这些判定不表示否定整个研究问题。`reject` 针对具体错误规则；`unknown` 不允许被测试通过改写成 accept。

## P1：标量区域的具体对应

来源为已实际重建运行的 [StockLevelService](../evidence/tpcc-roundtrip/20261103-9954559e225c/bundle/sources/candidate/reversedb/tpcc/StockLevelService.java)，以及同归档的 Sql、CandidateApp。依赖如下：

```text
Q1: SELECT d_next_o_id WHERE warehouse=1 AND district=?
  ├─ 没有行 → SQLException("Missing district")
  └─ getInt → n:int32 → Java int32(n−20)
                     → Integer 装箱 / setObject
                     → Q2: count distinct item, order ∈ [n−20,n), quantity<threshold
                     → getInt → JSON.low_stock_count
```

中间 n 只用于 Q2 的窗口边界，没有发现外部消费者。Q2 的去重、严格库存比较和半开窗口均有明确源位置。缺失地区的分支不能与“地区存在但没有匹配行”合并；最终 `getInt` 也应保留，不把数据库 bigint 直接返回成另一种接口类型。

本地 [DDL](../work/tpcc/source/src/main/resources/benchmarks/tpcc/ddl-postgres.sql) 声明 district 复合主键与 `d_next_o_id int NOT NULL`，但没有保证该值足够大以避免减 20 下溢的 CHECK。应用的输入检查约束 district、threshold，没有检查这个数据库值。本地 AppRuntime 源码表明使用 SERIALIZABLE 且 StockLevel 设置只读；这些属于设计和源码事实，本轮没有查询运行中数据库确认。

### 一个不能被普通样本排除的类型反例

令 `n = −2147483647`，存在订单行 `ol_o_id = −2147483648`，其库存低于阈值：

| 表达式 | 下界 | 对这条订单行的窗口判断 |
|---|---:|---|
| Java int32 的 n−20 | 2147483629（回绕） | 不包含，计数 0 |
| 直接拓宽为数学整数/bigint 的 n−20 | −2147483667 | 包含，计数 1 |

直接使用 SQL int32 减法又产生另一项义务：数学结果超出 int32，不能假设它等于 Java 回绕。以上是按源码类型构造并执行的算术模型反例，没有创建真实数据库状态或声称已跑过驱动。

它说明 `int NOT NULL` 不足以推出朴素改写正确。固定种子产生正数也不是全输入依据。若将 κ 限定为从已知正值快照、仅经受控事务可达的状态，可以研究 `n >= 20` 的归纳不变量，但必须覆盖所有写入口，不能直接宣布成立。

后续求解有两个明确选项：证明这样的程序不变量；或在关系表达式中保留原类型运算。后一种候选对 int32 输入可用精确宽整数中间量表达：`z=n−20; z<−2^31 时加 2^32，否则 z`。这是待验证的类型桥接表达式，不是本轮已生成或执行的 SQL。缺失分支、快照和结果解码仍需同时保留。

## P1：map 区域的具体对应

[真实 helper](../sources/paperless-ngx/src/documents/permissions.py) 的绑定为：

```text
T: 传入 queryset；K: T.values("pk")
L: through_model.objects（包含实际 manager 谓词）
A: document_ids / permitted_document_ids(user)
Q1: L 中 key∈K 且 document∈A，按 key 做 Count DISTINCT
  → 立即物化 Python dict M
  → 空 M 回填 Value(0)，否则 CASE(key→M[key], default=0)
  → 返回尚未求值的 QuerySet
H: read_tag_counts_b0 中立即读取有序 (pk, document_count) 整数对
```

M 在 helper 内只用于空判断和 CASE 构造；**QuerySet 会逃逸到消费者**，因此局部 map 不逃逸不等于整个区域已闭合。既有消费者采用按 pk 排序的全部目标，不能直接替换成名称分页接口。

核验当前 helper、局部绑定器与 B2 模板与修复后审计来源匹配；旧报告中的 8 组差分确实使用 `immediate-pk-ascending-int-pairs/v1`。旧数据库隔离记录是 **READ COMMITTED**，固定输入、无相关写入、立即消费和无观察副作用来自实验声明。隔离级别本身没有证明跨语句固定快照。

既有机制已具备实际 manager、键/NULL、分组唯一、转换器、身份、查询形状和执行前重新绑定的入口。本轮保留这些成果，不重复实现。对新调用点、当前数据库或 P2 新消费者，仍需重新核验，不能复制旧 `accept` 标志。

## P2：需求必须绑定到生产者，而不是只绑定到页面

模型夹具的消费者是“已有顺序的 T → 切片 W → 输出 W 的键和计数”。因此 `Δ = keys(W)` 在该数学域内充分。既有真实整数对消费者则输出全部 T，`Δ = keys(T)`，没有可报告的计算域缩减。

真实 [TagViewSet.list](../sources/paperless-ngx/src/documents/views.py) 与 [TagSerializer.get_children](../sources/paperless-ngx/src/documents/serialisers.py) 的数据流更复杂：

```text
第一次聚合 G1 → 注解根查询 T → 请求过滤/排序 → all_tags
                                              ├─ 根页面 W → 根字段与计数
                                              └─ 后代键发现
                                                   → 权限过滤 → 第二次聚合 G2
                                                   → children_map → 递归子标签及计数
另有 display_count、旧版本 all、分页 count/链接等输出
```

若根页面只有 a，而其返回子标签 b 有一个关联文档，将 `keys(W)={a}` 同时用于 G1 与 G2，b 的计数会由 1 变 0。模型记录保存了这对不同的嵌套响应。这否定的是**整个接口共用根页面键集**，不是证明“第一次聚合完全不能缩减”。保留 G2、只缩减 G1 是另一个需要独立绑定的候选。

因此对形式问题作进一步具体化：对每个聚合生产者 Gi 分别求 `Δi`，所有需求同时替换时满足完整 Obs 等价。还要区分：

- **行需求**：发现后代、计数元数据或旧版本 all 需要哪些目标行/键。
- **派生值需求**：哪些目标行的 document_count 确实被输出、排序或过滤使用。

需要某一行的 ID，不自动等于需要它的计数；不需要其计数，也不授权删除那一行。`display_count` 是 children_source 的行数，不是计数总和，不能把它误写成“必须计算所有 document_count”的理由。

当前接口允许 `document_count` 排序，默认 `Lower(name)` 也没有建立充分全序；这些条件需要按请求分别核验。本轮不增加递归树需求求解，首版仍只支持已约定的有限消费者语法。

## 下一步的限定求解任务

程序绑定关已完成，结果包括已建立的源码事实、两个反例及明确未决义务。下一关范围固定为：

1. 在实验副本中表达标量类型桥接与缺失行结果，保留原最终解码；类型边界无法闭合就保持 unknown，不凭正常数据准入。
2. 复用既有 map 区域机制，定义一个明确的数据库消费者夹具：基字段确定排序、分页、逐行返回计数；为它绑定唯一聚合生产者的需求表达式。真实 Tag API 不接入。
3. 比较原路径、候选与故意错误规则的有类型完整输出；复核权限谓词、重复、空集、页面边界和状态不变。该关不计时、不新增消费者语法。

这一步验证的是类型/需求条件能否落实为可执行的受限变换，不是缓存或调用次数调优。通过后才进入已固定的收益实验；未通过则报告哪项形式义务无法满足。
