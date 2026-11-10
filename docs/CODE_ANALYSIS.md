# 第一轮固定源码分析

后续进展：本文保留首轮静态分析口径。Paperless 已补充限定 fixture 的实际运行证据，并发现权限检查路径触发额外计数；见 [B0 运行结果](BASELINE_RESULTS.md)。

2026-11-02。所有结论限定于 `sources.lock.json` 中的三个 commit。`F` 表示源码直接可见，`I` 表示静态推断，`H` 表示待验证假设。本轮无应用运行 trace；下面的调用链是人工恢复的程序路径，不是实测执行轨迹。引用编号链接到固定 SHA 的源码，完整原文在 `evidence/static-evidence.json`。

## 1. Chatwoot：消息创建的提交后程序

Commit：`8304670cfd76e1d94ef04df89e40f62a080ffc2f`。

### 调用链和数据访问

| 步骤 | 源码位置 | 已核实行为 |
|---|---|---|
| HTTP controller | `app/controllers/api/v1/accounts/conversations/messages_controller.rb:8–14` [C01] | `create` 构造 MessageBuilder 并调用 `perform`，捕获 StandardError。这个 action 内没有显式事务块。 |
| builder | `app/builders/messages/message_builder.rb:24–33` [C02] | `conversation.messages.build`，处理附件、邮件内容，再 `save!`。`build` 与 `save!` 不能都计作 INSERT。 |
| validation | `app/models/message.rb:64–74,297–307` [C03] [C05] | `before_validation` 调用最近一分钟的 `.where(...).count` 限流校验；没有看到该校验中的显式行锁。 |
| persist → callback | `app/models/message.rb:132–144,333–342` [C04] [C06] | `after_create_commit` 顺序执行 reopen、pending 处理、活动时间、事件、发送回复、模板 hook、联系人活动。 |
| status write | `app/models/message.rb:414–445` [C07] | incoming 且未 muted 才重开；snoozed 可 `open!`，resolved 依据 bot/API 条件改变状态。 |
| activity write | `app/models/message.rb:459–462` [C08] | `conversation.update_columns(last_activity_at: created_at, updated_at: Time.current)`；与普通 `update` 的回调语义不同。 |
| event + further writes | `app/models/message.rb:389–397,348–369` [C07] [C06] | 先 dispatch MESSAGE_CREATED；首回复可 dispatch FIRST_REPLY_CREATED 再更新会话，否则维护 waiting_since。 |
| jobs | `app/models/message.rb:408–412` [C07] | SendReplyJob 入队；有附件时延迟两秒。不是在这里同步发送渠道消息。 |
| worker | `app/jobs/send_reply_job.rb:18–29` [C13] | 重新 `Message.find`、读取渠道，调用渠道 service。后台路径的数据库访问要另计。 |

F：`Dispatcher#dispatch` 同时调用同步 dispatcher 和异步 dispatcher（`app/dispatchers/dispatcher.rb:15–17` [C11]）；异步部分通过 EventDispatcherJob 分发给 automation、notification、webhook 等 listener（`app/dispatchers/async_dispatcher.rb:1–23` [C12]）。因此不能把所有 dispatch 都记为“纯入队”。具体 listener 内部副作用未全部展开。

F：Conversation 普通更新还会触发状态通知、activity 和 unread count invalidation 等回调（`app/models/conversation.rb:273–279` [C10]）。仅删除重复-looking 的 `update`，可能改变嵌套回调次数和事件顺序。

### 事务与失败边界

F：消息的这些状态维护显式注册为 `after_create_commit`。创建 action/builder 本身未包住整条后处理链。I：标准 ActiveRecord 保存事务成功提交后，后处理可以单独失败；外层若有事务，回调还要等待实际提交。尚未核验全部中间件与所有调用者。

I：控制器捕获异常并返回创建失败信息，并不证明已提交消息会回滚；如果错误发生在提交后，数据库可能已有消息。这是下一步故障注入的必要用例，不是本轮复现的缺陷。

**研究约束：即使只融合数据库写入，把 post-commit 会话更新移到 message INSERT 的同一个事务里，也改变原子的范围、可见中间状态和失败行为。保留“外部事件仍在提交后”还不够。** 第一阶段把 after-commit 当不可跨越的 effect barrier；任何新 outbox 方案都作为改变交付语义的单独设计。

### 数据结构与索引

F：`db/schema.rb:1445–1477` [C15] 定义 account/inbox/conversation、polymorphic sender、整数 message_type、时间、JSON/JSONB 属性；已有 `(conversation_id, account_id, message_type, created_at)` 复合索引。未实测该索引在具体 workload 下的选择率或命中。

F：`Conversation.unread_messages_count_arel` 在 `app/models/conversation.rb:239–257` [C09] 构造相关 COUNT，约束 conversation、account、incoming 与 agent_last_seen_at。**它是相邻的列表/排序研究路径，不能算成每次消息创建必做的一次查询。**

F：EE 会重写 reopen 与 pending 行为并查询 CaptainInbox（`enterprise/app/models/enterprise/message.rb:41–77` [C14]）；本次主要路径以 OSS 为基线，EE 是显式变体，不声称两者等价。

### 优化假设

- H-C1：同一 post-commit 子区域存在可消除的重复对象读取；先 trace association cache，再判断是否跨数据库往返。
- H-C2：减少独立状态更新可以省成本，但必须证明回调序列、时间戳、异常与事件 payload 等价；暂不生成写融合。
- H-C3：限流 COUNT 的并发语义与竞争应单独验证。加入锁可能改善某种正确性却改变基线语义，不能偷偷作为“性能优化”。

## 2. Paperless-ngx：计数结果从数据库出来，又被编译回 SQL

Commit：`1b8b861a6072fd85aad68847c0d312d7a9fd2ca0`。

### 已核实的路径

入口是 `TagViewSet.list`（`src/documents/views.py:580–650` [P02]）。Tag 的 queryset 用 `select_related("owner")`，默认按 `Lower("name")` 排序，过滤器包含对象权限。顺序如下：

1. `self.get_queryset()` 进入 count mixin，调用 `annotate_document_count_for_related_queryset`（`views.py:511–521` [P01]）。
2. helper 调用 `permitted_document_ids(user)`，再调用 `annotate_document_count_by_ids`（`permissions.py:614–632` [P05]）。
3. through 表按 related id、visible document id 过滤，按 tag 分组 `Count(document_id, distinct=True)`；字典推导式真实遍历 queryset，构造 `counts_by_pk`（`permissions.py:588–598` [P05]）。
4. Python 把每个 `(pk,count)` 转成 `When(pk=..., then=Value(...))`，加到 tag queryset 的 `Case` annotation；空计数用 Value(0)（`permissions.py:600–610` [P05]）。
5. 回到 `list` 后，才执行 endpoint filters、ordering；`all_tags = list(queryset)` 物化全部匹配标签（`views.py:611–618` [P02]）。
6. 合并各标签的 descendant PK；有后代时，再选择“全部匹配标签 ∪ 后代”、检查 tag 权限、做相同计数 annotation，然后构造 `children_map[parent_id] -> list[Tag]`（`views.py:620–640` [P02]）。
7. 最后分页原 queryset、递归序列化，用 `display_count=len(children_source)`，API <10 时可能覆盖 `all` 为含后代的 ID 列表（`views.py:642–650` [P02]）。

F：这是具体的 **SQL GROUP BY → Python dict → SQL CASE → Tag objects → Python hierarchy → JSON**。不是只凭 `.annotate` 猜测执行方式；字典推导的求值和 CASE 构建都在当前源码中。

I：初始计数发生在用户的搜索/标签过滤和权限 filter backend 之前，可能对比最终匹配集合更广的标签做计数；实际 SQL 范围、耗时与 planner 行为仍需 trace/EXPLAIN 验证。已有 helper 会按传入 queryset 的 PK 限制聚合，不能误说它在所有调用中无条件扫描全量数据。

### 权限不是一个简单的 user_id 谓词

F：`permissions.py:310–400` [P03] 区分匿名、停用用户、active superuser；普通用户可见自己拥有、无 owner、用户授权或所属组授权的对象。Guardian object_pk 是字符串，代码对行键 Cast，再与 user/group grants 的 UNION ALL 子查询匹配。注释中的数据库性能比较属于上游开发者说明，本轮没有重新测量。

F：文档版本通过 `root_document` 的 owner/grants 授权，`permitted_document_ids` 使用该参数（`permissions.py:497–520` [P04]）；只检查文档本身 owner 会改变语义。Tag 权限和 document count 权限是两套筛选，不能混为一个可见集合。

F：serializer 优先从 children_map 取子节点；缺少 map 时才走独立 ORM 查询，递归复用 context（`serialisers.py:607–647` [P07]）。附加字段还有批量 permission_grantees 与 fallback 查询分支（`serialisers.py:350–384` [P12]）。不能宣称整个 endpoint 固定两次 SQL，也不能宣称已经彻底没有 N+1。

### 分页、层级与事务约束

F：当前请求在分页前已建立全部匹配对象/后代集合。`count`、`display_count`、`all`、`results` 不是同一个集合。`StandardPagination` 默认 25、API <10 返回 `all`（`src/paperless/views.py:60–94` [P08]）。把 LIMIT 直接推到最前面将漏掉必要元数据或子节点。

F：Tag 继承 TreeNodeModel，clean 约束最大深度 5（`models.py:103–139` [P09]）。`get_descendants_pks` 来自第三方库，锁定为 django-treenode 0.25.0（`uv.lock:1213–1219` [P13]），此轮没有读取依赖内部或验证缓存。因此不声称每次后代访问都会发 SQL，也不声称递归 CTE 必然更快。

F：被分析的 list/count helpers 没有显式 atomic 块或数据库写语句。仓库设置支持 SQLite/PostgreSQL/MariaDB（`src/paperless/settings/custom.py:221–307`），未在检查范围找到 ATOMIC_REQUESTS。I：正常部署可能是多条独立读语句，但不能据此保证整个请求使用单一快照，也不能保证第三方树/权限缓存没有副作用。

I：合成单条 SQL 会改变多语句在并发更新下的观察窗口。第一轮正确性实验必须在同一冻结数据或为所有实现统一设置的只读快照中比较，并明确这只是该契约下的等价性。

### 原型候选与可证伪假设

- H-P1：将分组计数作为关系结果保留在 SQL 中，通过 grouped join/CTE 合入 tag 查询，可以省下 Python dict 和 CASE 文本/参数生成成本。
- H-P2：对初始计数合法地下推标签 filter/权限，可能减少计数工作；ordering=document_count、搜索谓词与后代补全必须逐项证明。
- H-P3：更大标签集合时收益更明显。小集合或数据库 planner 选择不同计划时，可能无收益甚至变慢。

先研究计数这一小区域，保留原有 hierarchy 与 serializer。不要一开始同时替换树算法、权限、分页、JSON 输出。上游已有“隐藏子标签不可见”和“父标签分页时仍含子标签计数”的测试（`test_tag_hierarchy.py:19–44,208–241` [P10] [P11]）；本轮只是读取，未执行。

## 3. Saleor：嵌套事务中的库存分配

Commit：`0e3fa1b022558b33fb9ff5efdadebd3bbccdb83b`。

### 域调用链与真正锁住的对象

`create_order_from_checkout` 在 `saleor/checkout/complete_checkout.py:1620–1655` [S01] 开启外层 atomic、锁 Checkout，再调用 `_create_order_from_checkout`。后者在 1531–1540 [S02] 调用 `_handle_allocations_of_order_lines`，wrapper 在 1283–1313 [S03] 调用 `allocate_stocks` 并设置 check_reservations=True。这是一个已核对的域路径，不代表已穷举所有 GraphQL 入口。

F：`allocate_stocks` 的 decorator 是 `@traced_atomic_transaction()`（`warehouse/management.py:90` [S04]）；该包装实际使用 `transaction.atomic()`（`core/tracing.py:10–15` [S09]）。因此不能假定 allocate_stocks 返回就提交：在上述调用链中它处于外层事务之内。

F：stock queryset 通过 `order_by("pk").select_for_update(of=["self"])`（`warehouse/lock_objects.py:4–5` [S08]），再由 `list(...values(...))` 求值（`management.py:145–149` [S04]）。当前函数里 Allocation 的分组查询没有调用 allocation lock helper。**函数 docstring 说锁 stocks and allocations，但不能据此宣称实际显式锁了 Allocation 行。** 当前能直接证实的是 Stock 行锁；其他写入者是否都遵守同一锁协议仍待审计。

### ORM 与应用算法的交替

| 顺序 | 文件/行号 | 数据与操作 |
|---|---|---|
| 候选库存 | `management.py:128–149` [S04] | 按 variants、channel/country 或 collection point 形成 stock 查询，锁并物化 id/variant/quantity/warehouse。 |
| 预留 | `management.py:152–154,243–265` [S04] [S06] | 可选 Reservation 分组；排除当前 checkout，忽略到期 reservation。到期谓词在 `models.py:624–632` [S11]。 |
| 已分配量 | `management.py:156–168` [S04] | 正数量 Allocation 按 stock 聚合，转 defaultdict(int)。 |
| 优先级 | `management.py:170–181,268–313` [S04] [S07] | 按 high-stock 或 channel warehouse 顺序排序；再生成 variant→list[StockData]。warehouse 顺序 queryset 是惰性的，不能无条件算作一次 SQL。 |
| 分配 | `management.py:183–198,316–355` [S04] [S07] | 按订单行迭代，减 allocation/reservation，取 min(需求余量,可用量)，创建未保存 Allocation 对象；不足则抛异常。 |
| 写入 | `management.py:200–213` [S05] | bulk_create Allocation；获取 alloc.stock，累加本批 quantity，用 F 表达式 bulk_update Stock.quantity_allocated。 |
| 写后检查 | `management.py:219–240` [S05] | 每条 allocation 再聚合 stock 的总分配量，满足条件则登记 on_commit 库存事件。 |

F：数据模型含 Stock.quantity 与 quantity_allocated，Stock 对 `(warehouse,product_variant)` 唯一；Allocation 对 `(order_line,stock)` 唯一（`warehouse/models.py:527–594` [S10]）。所以只是把可用库存存进普通进程字典并不等价。

F：写后循环内有 `.aggregate(Sum(...))`，不是一次统一批量聚合（[S05]）。I：多条 allocation 时它会构成重复 SQL 成本；`.stock` 关系访问也可能触发额外加载，因为 Allocation 构建时传入的是 stock_id。实际次数必须按 queryset 求值、关系缓存与分支测量，不能给固定总查询数。

### 副作用和正确性待查项

F：库存事件注册在 `transaction.on_commit`（`management.py:227–239` [S05]），不是随内层函数返回立刻发送；嵌套事务、保存点回滚、外层回滚都必须保留。事件处理函数内部的完整 webhook/插件链未在本轮闭合。

以下是**需要动态复现的风险线索**，不是已确认业务缺陷，也未修改上游源码：

- I-S1：`stocks_id` 是一次性 generator（150 行），先用于 reservation helper，再用于 Allocation filter。check_reservations=True 时，ORM 是否在构造/执行第一个 `__in` 时消耗它，必须按固定 Django 版本复现。若已被消耗，后续集合可能为空。
- I-S2：循环中的 on_commit lambda 引用 loop variable `allocation`（227–230 行），存在 Python 延迟绑定风险；多个 stock 耗尽时应记录最终事件对象与次数。
- I-S3：`_create_allocations` 读取 stocks_allocations，但本次查看的循环没有把前面订单行新分配量写回该 map。需要检查调用者是否保证 variant 唯一，并用同 variant 多行 fixture 验证；不能跳过这个前置契约直接融合算法。

### 优化假设与强基线

- H-S1：先把写后逐条聚合变为一次按 stock 分组，消除可避免的关系加载，构成 B1；若已获得主要收益，不归功于新编译机制。
- H-S2：锁住同样行、使用同样锁序/隔离级别/排序/异常的服务端分配函数可能缩短持锁期间的跨进程往返；尚无性能或正确性证明。
- H-S3：Stock.quantity_allocated 可否直接替代 Allocation SUM 取决于一致性约束和其他写入者，不能仅凭字段名字替换。

## 4. 本轮结论与下一步

三个案例都是真实的应用/数据库计算交替，但难点不同：Chatwoot 是提交后副作用与回调；Paperless 是关系结果物化后又生成查询；Saleor 是锁、嵌套事务、分配算法与提交后事件。

第一原型选 **Paperless 计数区域**：边界具体、只读、已有优化基线，且能保留大部分 API 层不动。下一步先跑固定源码的 isolated PostgreSQL 基线，确认实际 SQL 和成本，再实现 [ReverseDB-0](PROTOTYPE.md)。这不是“三个系统已完成深入全量分析”的结论，也没有证据说明需要生成全新数据库引擎。

<!-- Source reference definitions are generated from regions.json. -->

[C01]: https://github.com/chatwoot/chatwoot/blob/8304670cfd76e1d94ef04df89e40f62a080ffc2f/app/controllers/api/v1/accounts/conversations/messages_controller.rb#L8-L14
[C02]: https://github.com/chatwoot/chatwoot/blob/8304670cfd76e1d94ef04df89e40f62a080ffc2f/app/builders/messages/message_builder.rb#L24-L33
[C03]: https://github.com/chatwoot/chatwoot/blob/8304670cfd76e1d94ef04df89e40f62a080ffc2f/app/models/message.rb#L64-L74
[C04]: https://github.com/chatwoot/chatwoot/blob/8304670cfd76e1d94ef04df89e40f62a080ffc2f/app/models/message.rb#L132-L144
[C05]: https://github.com/chatwoot/chatwoot/blob/8304670cfd76e1d94ef04df89e40f62a080ffc2f/app/models/message.rb#L297-L307
[C06]: https://github.com/chatwoot/chatwoot/blob/8304670cfd76e1d94ef04df89e40f62a080ffc2f/app/models/message.rb#L333-L369
[C07]: https://github.com/chatwoot/chatwoot/blob/8304670cfd76e1d94ef04df89e40f62a080ffc2f/app/models/message.rb#L389-L445
[C08]: https://github.com/chatwoot/chatwoot/blob/8304670cfd76e1d94ef04df89e40f62a080ffc2f/app/models/message.rb#L451-L468
[C09]: https://github.com/chatwoot/chatwoot/blob/8304670cfd76e1d94ef04df89e40f62a080ffc2f/app/models/conversation.rb#L239-L257
[C10]: https://github.com/chatwoot/chatwoot/blob/8304670cfd76e1d94ef04df89e40f62a080ffc2f/app/models/conversation.rb#L273-L287
[C11]: https://github.com/chatwoot/chatwoot/blob/8304670cfd76e1d94ef04df89e40f62a080ffc2f/app/dispatchers/dispatcher.rb#L10-L18
[C12]: https://github.com/chatwoot/chatwoot/blob/8304670cfd76e1d94ef04df89e40f62a080ffc2f/app/dispatchers/async_dispatcher.rb#L1-L23
[C13]: https://github.com/chatwoot/chatwoot/blob/8304670cfd76e1d94ef04df89e40f62a080ffc2f/app/jobs/send_reply_job.rb#L4-L30
[C14]: https://github.com/chatwoot/chatwoot/blob/8304670cfd76e1d94ef04df89e40f62a080ffc2f/enterprise/app/models/enterprise/message.rb#L41-L77
[C15]: https://github.com/chatwoot/chatwoot/blob/8304670cfd76e1d94ef04df89e40f62a080ffc2f/db/schema.rb#L1445-L1477
[P01]: https://github.com/paperless-ngx/paperless-ngx/blob/1b8b861a6072fd85aad68847c0d312d7a9fd2ca0/src/documents/views.py#L511-L530
[P02]: https://github.com/paperless-ngx/paperless-ngx/blob/1b8b861a6072fd85aad68847c0d312d7a9fd2ca0/src/documents/views.py#L580-L650
[P03]: https://github.com/paperless-ngx/paperless-ngx/blob/1b8b861a6072fd85aad68847c0d312d7a9fd2ca0/src/documents/permissions.py#L310-L400
[P04]: https://github.com/paperless-ngx/paperless-ngx/blob/1b8b861a6072fd85aad68847c0d312d7a9fd2ca0/src/documents/permissions.py#L497-L520
[P05]: https://github.com/paperless-ngx/paperless-ngx/blob/1b8b861a6072fd85aad68847c0d312d7a9fd2ca0/src/documents/permissions.py#L543-L632
[P06]: https://github.com/paperless-ngx/paperless-ngx/blob/1b8b861a6072fd85aad68847c0d312d7a9fd2ca0/src/documents/filters.py#L1137-L1164
[P07]: https://github.com/paperless-ngx/paperless-ngx/blob/1b8b861a6072fd85aad68847c0d312d7a9fd2ca0/src/documents/serialisers.py#L607-L650
[P08]: https://github.com/paperless-ngx/paperless-ngx/blob/1b8b861a6072fd85aad68847c0d312d7a9fd2ca0/src/paperless/views.py#L60-L94
[P09]: https://github.com/paperless-ngx/paperless-ngx/blob/1b8b861a6072fd85aad68847c0d312d7a9fd2ca0/src/documents/models.py#L103-L139
[P10]: https://github.com/paperless-ngx/paperless-ngx/blob/1b8b861a6072fd85aad68847c0d312d7a9fd2ca0/src/documents/tests/test_tag_hierarchy.py#L19-L44
[P11]: https://github.com/paperless-ngx/paperless-ngx/blob/1b8b861a6072fd85aad68847c0d312d7a9fd2ca0/src/documents/tests/test_tag_hierarchy.py#L208-L241
[P12]: https://github.com/paperless-ngx/paperless-ngx/blob/1b8b861a6072fd85aad68847c0d312d7a9fd2ca0/src/documents/serialisers.py#L350-L384
[P13]: https://github.com/paperless-ngx/paperless-ngx/blob/1b8b861a6072fd85aad68847c0d312d7a9fd2ca0/uv.lock#L1213-L1231
[S01]: https://github.com/saleor/saleor/blob/0e3fa1b022558b33fb9ff5efdadebd3bbccdb83b/saleor/checkout/complete_checkout.py#L1620-L1655
[S02]: https://github.com/saleor/saleor/blob/0e3fa1b022558b33fb9ff5efdadebd3bbccdb83b/saleor/checkout/complete_checkout.py#L1531-L1540
[S03]: https://github.com/saleor/saleor/blob/0e3fa1b022558b33fb9ff5efdadebd3bbccdb83b/saleor/checkout/complete_checkout.py#L1283-L1313
[S04]: https://github.com/saleor/saleor/blob/0e3fa1b022558b33fb9ff5efdadebd3bbccdb83b/saleor/warehouse/management.py#L90-L198
[S05]: https://github.com/saleor/saleor/blob/0e3fa1b022558b33fb9ff5efdadebd3bbccdb83b/saleor/warehouse/management.py#L200-L240
[S06]: https://github.com/saleor/saleor/blob/0e3fa1b022558b33fb9ff5efdadebd3bbccdb83b/saleor/warehouse/management.py#L243-L265
[S07]: https://github.com/saleor/saleor/blob/0e3fa1b022558b33fb9ff5efdadebd3bbccdb83b/saleor/warehouse/management.py#L268-L355
[S08]: https://github.com/saleor/saleor/blob/0e3fa1b022558b33fb9ff5efdadebd3bbccdb83b/saleor/warehouse/lock_objects.py#L1-L22
[S09]: https://github.com/saleor/saleor/blob/0e3fa1b022558b33fb9ff5efdadebd3bbccdb83b/saleor/core/tracing.py#L10-L15
[S10]: https://github.com/saleor/saleor/blob/0e3fa1b022558b33fb9ff5efdadebd3bbccdb83b/saleor/warehouse/models.py#L527-L594
[S11]: https://github.com/saleor/saleor/blob/0e3fa1b022558b33fb9ff5efdadebd3bbccdb83b/saleor/warehouse/models.py#L624-L632
