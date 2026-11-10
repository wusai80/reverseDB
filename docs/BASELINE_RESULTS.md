# Paperless B0 首轮运行与问题检查

运行时间：2026-11-02 22:41:05–22:41:24（Asia/Shanghai）。本轮完成了未修改上游源码的后端小型基线；尚未进行性能比较或实现 B1/B2/B3。

## 验证结果

| 检查 | 结果 |
|---|---|
| 上游标签层级测试 | 18 passed |
| 上游权限安全测试 | 92 passed |
| 本项目 B0 helper/API trace fixture | 1 passed |
| 本地运行器与 SQL wrapper 检查 | 17 passed |
| 结果复制、测试容器和数据库卷清理 | 通过；独立清点未发现实验容器、卷或网络残留 |
| 固定上游版本及静态证据 | 保持三个固定 checkout；未修改应用源码 |

上游 commit 为 `1b8b861a6072fd85aad68847c0d312d7a9fd2ca0`。实际环境：Linux arm64、Python 3.14.9、Django 5.2.17、PostgreSQL 18.6、Docker Engine 29.9.2。Docker VM 为 12 CPU、8,319,238,144 字节内存，未设置额外容器资源限额。

原始证据位于 [本轮目录](../evidence/paperless-b0/20261102-224105-1d6b9197b6f6/)：

- [机器可读汇总及文件哈希](../evidence/paperless-b0/20261102-224105-1d6b9197b6f6/summary.json)
- [运行命令、输入哈希、镜像锁与退出码](../evidence/paperless-b0/20261102-224105-1d6b9197b6f6/run.json)
- [SQL、调用栈与 API 响应](../evidence/paperless-b0/20261102-224105-1d6b9197b6f6/b0-trace.json)
- [完整测试日志](../evidence/paperless-b0/20261102-224105-1d6b9197b6f6/tests.log)、[上游 JUnit](../evidence/paperless-b0/20261102-224105-1d6b9197b6f6/upstream.xml)、[trace JUnit](../evidence/paperless-b0/20261102-224105-1d6b9197b6f6/trace.xml)

## 确认的应用行为

fixture 只有三个标签（父、子、零计数标签）和一个关联父/子标签的文档，使用 active superuser。helper 返回完整映射 `[(1,1), (2,1), (3,0)]`。

| 观测阶段 | SQL 数 | GROUP BY 数 | 含 CASE 的 SQL 数 |
|---|---:|---:|---:|
| 构造原 helper 的 annotation | 1 | 1 | 0 |
| 求值 helper 的最终 queryset | 1 | 0 | 1 |
| v9 标签 API，清理 Django cache 后 | 7 | 3 | 2 |
| v9 标签 API，重复请求 | 7 | 3 | 2 |
| v10 标签 API，清理 Django cache 后 | 7 | 3 | 2 |
| v10 标签 API，重复请求 | 7 | 3 | 2 |

这确认了原先的静态观察：分组计数在 Python 字典推导时已经求值，计数随后被放回 SQL CASE。本轮不是只看 ORM 调用名称来推断 SQL。

**新增发现：在本 fixture 中，每次 API 请求前两条 GROUP BY SQL 完全相同，且都没有绑定参数。** 调用栈显示它们分别来自：

1. DRF `check_permissions → has_permission → _queryset → get_queryset → annotate_document_count_by_ids`。
2. `TagViewSet.list → get_queryset → annotate_document_count_by_ids`。

第三次 GROUP BY 用于后代标签计数。其余四条 SQL 为两次带 CASE 的标签读取，以及用户/组授权列表各一次读取。这使“避免权限检查阶段触发不必要的计数、请求内复用计数”成为值得先验证的常规 B1 候选；还不能据此断言所有用户/过滤条件都有相同查询数或已有性能收益。

`is_root=true`、`page_size=1` 时，v9/v10 均返回 `count=2`、`display_count=3`，一条父标签结果包含其子标签。v9 的 `all=[1,2,3]`；v10 不包含 `all`。分页没有把子节点计数丢掉。

## 遇到的问题及处理

**Docker 启动卡住：恢复后未复现，根因未确定。** 首次实验的 PostgreSQL/Valkey 已健康，应用容器却停在 Created；后续无挂载启动及直接 Docker API 启动也超时，没有进入应用代码。正常重启超时后使用 Docker 官方停止/启动流程恢复。此前失败记录保存在 [启动诊断](../evidence/paperless-b0/20261102-222049-startup-hang/startup-diagnosis.json)。

同时把结果目录从宿主 bind mount 改为每轮独立的 Docker volume，完成后用 `docker cp` 导出。因为重启与挂载方式均发生变化，不把恢复归因于某一个未经独立验证的原因。恢复后两次完整后端运行均通过。

**一次性容器回收遗漏：已修复并复跑验证。** `compose down --volumes` 曾返回 0，但保留了 `compose run` 创建的一次性应用容器，导致结果卷也留存。运行器现在先复制结果，再精确删除当次应用容器，最后清理同一项目的服务、卷和网络。最终运行清点没有残留；没有执行全局 prune 或清理其他项目。

**结果不完整时的成功误报：已加防护。** 缺少 trace 或只复制了部分结果时，即使 pytest 退出码为 0，也判为失败。回归检查覆盖了这两条路径，并检查复制、删除应用容器、删除项目资源的顺序。

**非阻塞警告：** 上游测试产生 12 条、trace 测试产生 1 条 `/app/static/` 不存在的警告。这个镜像只运行后端，未构建前端静态文件；API 测试通过不代表前端验证完成。构建另有基础镜像 ARG 无默认值的提示，实际构建已显式传入锁定 digest。

## 尚未得出的结论与下一步

本轮使用 pytest 事务、read committed 和无并发写入的合成测试库。APIClient 不包含真实网络 HTTP；trace 会增加请求开销，execute 时间也不含结果 fetch。首次请求还可能包含导入/初始化成本。原始单次耗时仅保留作诊断，不报告加速比、稳定尾延迟或生产热点比例。

下一步扩展标签/文档规模、普通用户授权与树深 fixture，细分 API 阶段并采集独立的无 trace 计时及 EXPLAIN；在此基础上先验证普通 B1 去重复方案，再与 B2 grouped join/CTE 比较。阶段 2 的完整数据矩阵和性能基准仍未完成。

## REA 与实际 HTTP/SQL 关联实验（2026-11-03）

本轮在原锁定 B0 镜像上增加独立实验 harness，不修改上游源码或旧 B0 输入。pytest 创建独立 PostgreSQL 测试库，在应用容器内启动仅绑定 `127.0.0.1` 随机端口的 WSGI 服务；标准库 HTTP 客户端发起真实请求。Compose 不发布宿主端口。数据仍为合成数据，串行执行、无并发写入；不是性能基准。

| 请求 | HTTP | SQL 数 | GROUP BY 数 | 响应 |
|---|---:|---:|---:|---|
| 超级用户首次 | 200 | 8 | 3 | 可见父/子标签，计数均为 1 |
| 同用户重复 | 200 | 8 | 3 | 同上 |
| 有 view_tag 模型权限、无对象访问权限的普通用户 | 200 | 7 | 2 | results 为空 |
| 匿名用户 | 401 | 0 | 0 | 拒绝访问 |

超级用户比原 APIClient 基线多出的第一条 SQL 是 token 认证查询；旧基线使用 force_authenticate，绕过了这一步。前三个请求均观察到两次相同的计数 SQL 模板，分别来自 DRF 权限检查的 `_queryset → get_queryset` 和列表处理的 `list → get_queryset`。普通用户空结果也出现这一模式，扩展了此前仅 active superuser 的观察范围。参数值未采集，模板重复不能直接证明查询等价或可复用。

服务端 WSGI 包装器生成请求 ID，通过 `X-ReverseDB-Trace-ID` 响应头返回；在处理请求的线程内对默认数据库连接安装 SQL wrapper，覆盖认证、权限检查和响应迭代。客户端记录实际 HAR，省略 Authorization 并明确标注。REA 6.8.0 离线读取 HAR：输入 SHA256 一致，四个完整记录与输入逐项相等。我们自己的关联器按 ID 联接服务端 trace，得到 23 条 SQL 的请求归属；该归属来自显式埋点，不是 REA 自动推断。

一个真实 HTTP fixture 通过；全部 22 项宿主工具检查通过，其中新增 5 项验证相同 URL/乱序记录仍正确关联，以及缺失 ID、重复 ID、缺失服务端证据和跨请求 SQL 会被拒绝。本轮容器、卷、网络已清理并独立清点；派生测试镜像保留用于复现。

### 暴露的难点与待讨论方案

1. **请求归属与语义恢复是两件事。** REA 保留 HTTP 证据，但仅凭 URL、状态码或响应体无法解释权限路径及查询必要性。本轮所有请求使用同一 URL，普通用户和超级用户均可返回 200，却执行不同查询。请求 ID 可以解决本轮归属问题，不能证明跨线程、任务队列或多数据库场景的完整性。
2. **检查权限时构造 queryset 会提前触发计数。** 普通用户最终得到空结果，仍有两次 GROUP BY。下一步可比较：A）确定权限检查所需的最小信息，尝试推迟计数求值；B）在请求内复用计数，但要建立参数、权限作用域、写入与快照的有效性条件。建议先做 A，作为常规 B1 基线；两条路线尚未实现，不能宣称可安全删除查询。
3. **模板相同不足以自动合并。** 当前参数仅记录类型。后续若验证 B 路线，需先讨论如何在不记录真实敏感值的前提下识别绑定值，并处理查询之间的状态变化。不能将 trace 中一次相同视为静态合法性证明。

复现入口：`python3 scripts/paperless_http_probe.py`（需原 B0 锁与镜像）；运行 REA `inspect-web-network-capture <本轮/http.har> har --json` 并将结果存为同目录 `rea-result.json`，再执行 `python3 scripts/correlate_http_sql.py <本轮目录>`。REA 运行需要上一节独立 Node 环境及 macOS 辅助程序执行权限；临时环境删除后需重新准备。

[本轮证据汇总](../evidence/rea-http/20261103-849e2b37694c/summary.json)包含输入快照、版本、镜像、哈希、测试与清理记录；[关联结果](../evidence/rea-http/20261103-849e2b37694c/correlation.json)和[原始服务端 trace](../evidence/rea-http/20261103-849e2b37694c/server-trace.json)支持上述限定结论。尚无 ORM 自动恢复、优化改写、并发等价或性能收益结论。

## B1：权限检查阶段避免提前计数（2026-11-03）

**结论与用户确认的范围：** B1 在固定合成数据、无并发写入、无查询故障的正常执行条件下通过本轮差分验证。用户已选择将它作为限定范围的常规优化基线。异常行为并不完全等价，反例已保留；未得到性能加速结论，也没有实现自动优化器。

### 原因与改动

锁定镜像中的 DRF 版本为 3.18.3。`DjangoModelPermissions.has_permission` 调用 `_queryset(view)`，之后仅消费 `queryset.model` 来构造权限名称并执行 `user.has_perms`；其 `_queryset` 优先调用 `view.get_queryset()`。Paperless 的 `PermissionsAwareDocumentCountMixin.get_queryset` 在返回前已经执行计数 helper，因而模型权限检查触发了原本供列表计数使用的 SQL。

实验补丁 [b1_permissions.py](../prototype/rea_http/b1_permissions.py) 仅替换精确 `TagViewSet` 类型、`action=list`、方法 GET 的权限 `_queryset`：返回基础 `view.queryset.all()`，供原权限实现读取模型元数据。IsAuthenticated、权限映射、has_perms、列表 get_queryset、对象过滤与计数逻辑均继续调用上游实现。详情接口、HEAD、OPTIONS、POST 等走原路径。补丁通过实验上下文临时安装，退出后恢复；三个上游 checkout 未修改。

### 实际结果

共 45 组 B0/B1 对照、90 次实际回环 HTTP 请求：40 组列表请求覆盖 10 种身份与 4 种版本/排序/分页组合；5 组控制覆盖可见/隐藏详情、HEAD、OPTIONS 和无写权限 POST。身份包括超级用户、普通 owner、直接授权、组授权、两种授权重叠、无对象授权、仅标签授权、仅对象授权但缺少模型权限、停用用户及匿名用户。测试验证全响应正文逐字一致、状态码一致，并独立检查隐藏对象排除、重叠授权不重复计数、零计数和文档权限独立性。

以下为本轮 `root_page1`，与此前空结果 fixture 的数据不同（本轮包含一个公开标签与公开文档）：

| 身份 | HTTP | B0 SQL → B1 SQL | B0 GROUP BY → B1 GROUP BY |
|---|---:|---:|---:|
| 超级用户 | 200 | 8 → 7 | 3 → 2 |
| 普通 owner | 200 | 10 → 9 | 3 → 2 |
| 直接 / 组 / 重叠授权 | 200 | 11 → 10 | 3 → 2 |
| 无对象授权，仅可见公开对象 | 200 | 9 → 8 | 2 → 1 |
| 仅标签授权，相关私有文档不可见 | 200 | 11 → 10 | 3 → 2 |
| 缺少模型权限，即使有对象授权 | 403 | 4 → 3 | 1 → 0 |
| 停用用户 | 401 | 1 → 1 | 0 → 0 |
| 匿名用户 | 401 | 0 → 0 | 0 → 0 |

32 组适用列表请求各减少一条权限阶段 GROUP BY，其他 13 组保持 SQL 数不变。45 组的 B0 SQL 合计 340、B1 合计 308；这不是延迟或吞吐测量。所有候选列表 trace 中，权限阶段的分组计数已消失，列表及子标签计数仍保留。

REA 6.8.0 对 90 条 HAR 记录的输入哈希与完整内容核验通过，按显式请求 ID 与 SQL trace 关联成功。3 项实验测试通过（含原 HTTP smoke、45 组差分及故障反例验证）；候选补丁下 110 项上游标签层级/权限测试通过；22 项宿主工具检查通过。容器、卷、网络清理后独立清点为空；派生镜像保留用于复现。

### 明确不成立的更强结论

故障注入对没有模型权限、但有对象授权的用户触发计数 SQL 异常：B0 在检查模型权限前执行计数，返回 500；B1 直接进行模型权限判断，返回 403，未触发计数异常。这是刻意构造的可复现反例，不是生产故障报告。它说明“返回结果不依赖某次查询”不足以证明“查询可无条件删除”；求值可能产生异常等可观察效果。

用户已确认本轮 B1 采用正常执行基线，保留此反例。该范围收窄不构成对任意代码、异常、缓存失效、权限变化或并发快照的等价性承诺。自动化研究仍需分别表达：消费者只需要什么信息、生产该信息会产生什么效果，以及改写依赖哪些前提。本次补丁是人工实现的基线，不计作已证明的新方法。

下一步在该已声明契约下采集无 trace 计时及 EXPLAIN，判断剩余 `GROUP BY → dict → CASE` 往返是否值得做 B2；不能把少一条 SQL 直接写成加速比。

[本轮证据汇总](../evidence/paperless-b1/20261103-66f4c2cd3d5b/summary.json)、[45 组差分结果](../evidence/paperless-b1/20261103-66f4c2cd3d5b/b1-results.json)、[异常反例](../evidence/paperless-b1/20261103-66f4c2cd3d5b/b1-fault-counterexample.json)和输入快照均已归档。复现：`python3 scripts/paperless_http_probe.py`，在新隔离项目中运行正常差分、反例及启用候选补丁的上游回归。
