# alf.io 预订流程的统一前端扩展

**本阶段已收敛。** 最后选票／数量／类别重读测试完成9个真实数据库场景；原快照差异、缺失异常、锁生命周期和失败前缀均要求保留。32项数据库、21项来源／选择器检查通过；仍无准入改写和第二系统加速结论。停止下方历史候选推进及新计时，成果作为适用边界案例保留。[最终测试与范围说明](ALFIO_SHARED_PLACEMENT.md)。

后续账单／审计效果契约已完成：将中间Java取时明确拆为host保留阶段。原UPDATE→Date→审计顺序禁止当前模型中的2→1融合，保留全部参数、原整数语义及异常前缀。41项离线检查通过，原算法和旧选择不变，无数据库或性能重跑。[具体契约与结论](ALFIO_SHARED_PLACEMENT.md)。

最新统一方法应用：新全局索引已绑定原8个作用域／39阶段，新增共享适配器从3个不完整作用域分离7段可分析片段，交给第一项目原DP；未知、live-out和显式保留边界继续约束选择。74项检查通过，NewOrder／Payment完整选择不变；当前仍无准入的alf.io数据库区域或整体计划。详见[同一方法的实际应用及缺口](ALFIO_SHARED_PLACEMENT.md)。

## 最新：全局结构索引与 JDBC 边界定向反编译（2026-11-06）

按“全局扫描、关键链深入反编译、模糊边界保留”的路线完成一轮离线分析。此前字节码提取只覆盖51个类，调用展开限于同一Manager的33个方法；本轮不再沿用这个局部覆盖范围，但不改变第一项目的DP、成本目标或任何运行时实现。

| 范围 | 本轮结果 | 含义 |
|---|---:|---|
| 官方应用字节码 | 1,048类／10,743方法 | 全部方法体、调用、lambda句柄、分支、异常表、字段／类型引用、注解 |
| 官方运行时依赖 | 157个JAR／42,913个有效类条目 | 类型、继承、接口、方法签名和注解；依赖方法体未全量展开 |
| 合并后的索引 | 43,879类／337,525方法 | 按官方classpath顺序选择；82个遮蔽条目另行记录 |
| 业务潜在调用范围 | 219个应用类／1,173方法 | 4类入口名称的6个重载方法作为显式根，静态展开及应用继承候选 |
| 调用图环合并 | 1,157个分量，其中3个有环 | SCC凝聚图已检验无环；不是把原调用图直接当DAG |
| SQL连接线索 | 764个查询注解／244条既有观察 | 保留全部旧匹配；SQL文本相符仍不证明代理实际分派 |

依赖JAR使用Java17的multi-release视图；应用类逐个对照官方JAR哈希，依赖类数量另用ZIP目录和manifest独立核对。排除JDK模块体、Boot启动器、非classpath工具和运行时／外部生成类，这些不因缺席而可删除。应用中没有被入口图连到的方法仍是未分类，不能当成无用代码。

入口名称由已有业务场景明确给出，并包含该名称全部重载；不是自动发现所有业务入口。图中的接口实现／继承／lambda边表示**可能调用**，不是观测到的动态执行；接收者类型、参数和路径可行性尚未收紧。仍明确记录2,807处开放虚分派、1,697处未解析声明、462处依赖体边界、365处动态bootstrap，以及注解、抽象／native方法和隐式初始化边界。计数是结构位置，不是耗时、独立模块数或全部未知的完备清单。框架回调可能再进入应用，故该图也不声称覆盖了所有真实执行路径。

### 定向反编译得到的具体关系

进一步从索引对应的固定依赖JAR中抽取16个class文件，反编译出4个外围类的源码，并保留各外围类的javap指令证据：`QueryFactory`、`QueryType`、`ConstructorAnnotationRowMapper`、`NamedParameterJdbcTemplate`。这一步只读源码和字节码，没有重编译替换框架、调用数据库或部署。

人工对照源码与指令确认以下结构；它们尚未成为自动准入的语义摘要：

1. `QueryFactory`创建运行时代理，分别处理查询注解、默认接口方法及Object方法。Repository接口到实际SQL分派不能仅凭普通调用边闭合。见[代理分派源码](../evidence/alfio-jdbc-boundary/20261106-d4669d4eb0ec/decompiled/ch/digitalfondue/npjt/QueryFactory.java)。
2. `QueryType`执行参数转换并根据返回类型选择单对象／列表等处理。高级参数转换器收到包含JDBC对象的上下文，因此**不能未经分析就假设参数转换只做无副作用的拷贝**；这不是断言当前具体转换器一定执行额外SQL。见[参数与返回路径](../evidence/alfio-jdbc-boundary/20261106-d4669d4eb0ec/decompiled/ch/digitalfondue/npjt/QueryType.java)。
3. 原映射器逐列转换后反射调用模型构造器，Java映射仍是有异常语义的边界，继续按既有决定保留。见[映射器](../evidence/alfio-jdbc-boundary/20261106-d4669d4eb0ec/decompiled/ch/digitalfondue/npjt/ConstructorAnnotationRowMapper.java)。
4. Spring模板接收参数与RowMapper，再委托JdbcOperations；模板已有SQL解析缓存。看到这些层并不证明层层都有显著重复开销，也不授权删除事务／校验。见[模板源码](../evidence/alfio-jdbc-boundary/20261106-d4669d4eb0ec/decompiled/org/springframework/jdbc/core/namedparam/NamedParameterJdbcTemplate.java)。

因此下一项研究可以聚焦：把**具体调用点的代理选择、参数转换与返回映射**恢复成可校验的依赖和效果摘要，再判断是否有通用调度能专化消除；不能直接以“绕过JDBC”为目标去删除未知效果。本轮没有生成或准入此类改写，没有加速结论。

### 验证与冻结证据

27项检查通过，覆盖继承实现、多实现候选、lambda、递归SCC、反射保留、switch／异常范围、注解、依赖仅索引签名、classpath遮蔽、全量覆盖、旧调用／注解及SQL匹配保留。原DP三个核心文件哈希不变。首次运行的样例断言错误地假设一个catch只有一段异常范围，javac实际因多个return产生三段；修正断言后全新运行成功，失败归档保留，未修改官方字节码。

- [全局索引运行与检查](../evidence/alfio-structure/20261106-e2dc533105d2/run.json)、[覆盖及遮蔽条目](../evidence/alfio-structure/20261106-e2dc533105d2/coverage.json)、[业务图／未知边界／凝聚DAG](../evidence/alfio-structure/20261106-e2dc533105d2/business-graph.json)、[SQL线索](../evidence/alfio-structure/20261106-e2dc533105d2/sql-witness-links.json)。
- [定向反编译记录](../evidence/alfio-jdbc-boundary/20261106-d4669d4eb0ec/run.json)、[首次样例断言失败记录](../evidence/alfio-structure/20261106-ddb98e7a9da8/run.json)。
- [独立归档与图一致性复核](../evidence/alfio-structure/20261106-e2dc533105d2-audit.json)：成功索引30文件、定向反编译28文件、失败样例21文件哈希均通过；凝聚图分量覆盖与跨分量边重新核对，原33个Manager方法仍在新潜在范围内。
- 复现：`python3 -B scripts/index_alfio_structure.py`；`python3 -B scripts/decompile_alfio_jdbc_boundary.py`（后者明确绑定上述冻结索引）。二者均离线执行，不启数据库。

## 前一阶段：保守方案性能基线

后续性能基线已完成：保守方案没有新增运行时转换，两组均执行原实现，12步累计中位数106.45／108.75 ms。该A/A结果仅记录重复性，不是加速结论；夹具无附加服务，未测G>0成本。16次完整回放一致，映射保留决定及原选择器不变。详见[最新性能报告](ALFIO_FUSION_PERFORMANCE.md)。下文“无计时”描述的是此前边界验证阶段。

2026-11-06。按用户“保守删除、模糊边界不动”的决定，**明细读取与逐组定义查询的原Java映射／校验边界明确保留**。原查询→原映射→成功后进入下一组的顺序不变，单JOIN及跨映射器的JSON批量路径停止推进。18项保守边界检查、32项原层级回归通过；NewOrder／Payment及分组选择不变，原选择器／目标函数／SQL解析器未改。本轮新增删除模块0，无数据库重跑、部署或计时。原8层级／39阶段的表示及全部实验反例保留。

当前结果是“更多结构已表达，整条流程的输入仍不完整”，**不是全局选择器判定alf.io没有优化机会**。

## 当前决定：不跨越尚未闭合的映射边界

这一决定优先于下方历史实验中“继续迁移映射校验”或“限定正常数据域”的建议。保留原NPJT查询、模型构造／枚举转换、异常传播和Java循环，不增加替代校验器，不假设数据库中的值必然可映射，不裁剪数据，也不改变隔离级别。实验函数仅作为冻结证据，不接入默认应用。

共享分组契约对 `read_items` 和 `lookup_definitions` 写入显式 `host_retention.required=true`。逐组处理另有效果顺序契约：查询本组→原Java映射成功→形成原Pair→进入下一组；映射失败则传播原异常，不查询后续组。实现继续使用原方法，没有新增模拟映射器或失败后重试路径。

共享前端在调用原DP前检查保留约束：若候选同时声明“必须留在Java”和“提供数据库实现”，返回 `retention_conflict_no_plan`，不交给优化器，也不静默改写输入。即使候选把语义保证全部勾上，也不能覆盖明确的保留决定。这是用户范围约束的通用一致性检查，独立于项目／节点名称，不是新的选择算法。其他完整输入仍原样进入原DP。

回归引用已有真实数据库反例：原映射失败时只查询服务101，原始JSON循环已查询两组。因为运行路径没有替换，沿用已验证的原异常顺序；本轮不为相同反例再次启动数据库。18项检查覆盖显式保留、成功后才能推进、冲突拒绝、输入不被静默改写、更名后的约束保持、原核心及选择结果。另32项原层级回归通过。

此区域暂按原 `1+G` 路径执行，不计入新增融合收益。后续只有新的明确范围决定和完整证据才能重新打开该边界；不能把“缺少证据”当作继续尝试删除的理由。

## 服务器内有序查询验证

本轮限定于定义查询循环。原Java的完整明细读取和分组仍保留，服务器函数接收原组顺序的ID数组，每组执行一次原服务视图查询，按输入顺序返回全部定义列。没有在SQL里重做HashMap顺序，没有去重或截断数据，也没有更改应用隔离级别。函数仅装在隔离恢复数据库的 `reversedb_probe` schema，属性实测为VOLATILE、SECURITY INVOKER；新函数调用权限显式授予两个测试角色，未授予额外服务表／视图读取权限。

若接入调用适配器，预期结构成本是：G=0时Java跳过函数，保留一次明细读取；G>0时一次明细读取加一次有序函数调用，共2次客户端执行。相对原 `1+G`，潜在差额为G−1（非空），单组无收益。本轮取原收集器输出的组顺序测试查询机制，尚未实现完整调用分流及返回对象适配器，**这些是结构成本，不是端到端实测加速或原DP已选择的新后端**。

| 验证 | 原路径 | 服务器路径／结论 |
|---|---|---|
| 静态完整定义 | 按原组顺序读全部定义字段，含视图计算列 | 同顺序完整JSON字段一致 |
| READ COMMITTED并发 | 第一组读完后，另一连接把两组价格一次提交为200；原结果 `[100,200]` | VOLATILE循环同为 `[100,200]`；STABLE负控为 `[100,100]` |
| REPEATABLE READ对照 | `[100,100]` | 两种函数均为 `[100,100]`；只改变实验连接，不修改应用设置 |
| 缺失定义 | NPJT抛 `EmptyResultDataAccessException`，数据库事务仍可查询 | 循环返回停止标记，保留此前记录，不查询后续组；Java测试重建的异常类／消息一致，事务仍可查询 |
| 空输入权限 | 不访问服务定义 | 空ID数组不执行定义SQL，受限角色成功；非空时两者SQLSTATE均为42501 |
| RLS拒绝 | 原策略隐藏服务定义 | invoker函数返回零行停止标记，没有绕过组织过滤 |

并发同步点使用实验专用的第二次查询前advisory-lock屏障；控制连接确实观察到查询等待后才提交更新并放行，没有按睡眠时长猜测交错。原始无屏障函数、仅加屏障的VOLATILE版本、仅改变易变性声明的STABLE对照分别冻结，测试插桩不进入候选应用代码。缺失处理使用结果标记而非直接RAISE，避免把原客户端基数异常变成导致SQL事务失败的服务器异常；当前仅验证了零行异常重建，不声称所有异常诊断已等价。

**新增的实质边界是对象构造与校验时机。** 数据库的 `vat_type` 字符串列允许一个不属于Java枚举的值。原真实NPJT在第一组映射时抛 `IllegalArgumentException`，尚未查询第二组；原始JSON循环却完成了两组查询。这个反例没有禁用约束，说明仅保留SQL顺序仍不足以保留“查询→映射校验→下一次查询”的顺序。

共享契约已增加 `preserve_per_query_materialization_timing`，连同此前三个义务交给原DP的通用缺失保证检查。按当前保守决定，进一步明确保留原Java边界；不采用“全部数据均可映射”的额外假设，也不迁移未闭合的校验。完整对象引用／返回接口、全部异常和并发覆盖的缺口作为保留原因记录。原DP还要求区域在参数下界处不增加调用；G=0分流同样没有被完整实现，不把始终调用空函数当成预期成本模型的实现。

## 单次关系查询的准入检查

本轮用锁定官方JAR、原NPJT工厂／映射器、原JDBC驱动和原 `ReservationCostCalculator`，连接由完整冻结夹具恢复的独立PostgreSQL数据库。应用连接使用非超级用户、NOBYPASSRLS角色，真实外键及RLS保持启用。候选仅为保留全部列的诊断性LEFT JOIN，未实现Java返回对象重建，也未替换应用调用。

| 检查 | 观察与结论 |
|---|---|
| 静态完整数据 | 3条明细、2个服务组，原路径3次Repository查询；JOIN与原查询完整JSON行的多重集相等，视图计算出的available_count也保留。此检查未证明结果顺序。 |
| 定义缺失 | 构造符合全部外键的跨事件引用：明细属于事件1，所引用服务属于事件2。真实原NPJT抛 `EmptyResultDataAccessException`；LEFT JOIN返回空定义，INNER JOIN直接丢掉明细。二者都不能直接冒充原方法。 |
| 并发可见性 | 本地默认隔离为READ COMMITTED。两组定义价格初始均为100，在第一组读完后由另一连接一次提交改为200，原结果为 `[100,200]`；更新前／后的单JOIN分别读到全100／全200。仅比较同一静态状态下的关系代数结果不足以处理这个执行差异。 |
| 稳定快照对照 | 仅在实验连接使用REPEATABLE READ时，原多查询与JOIN都保持旧价格100。没有修改生产隔离级别，这一对照也不证明其他异常、顺序及权限条件已解决。 |
| 空输入权限 | 合成的“只能读明细”角色下，空输入原方法成功，JOIN仍因服务视图权限不足而报SQLSTATE 42501。该角色是负控，不声称实际应用账号具有这种授权配置。 |
| RLS | 同一非超级用户开启组织过滤、拒绝当前组织时，原明细与JOIN都为空；这是一个拒绝可见性的控制，未穷尽全部租户／视图权限组合。 |

**纠正代理测试的解释范围：** 上轮代理人为返回null定义时，真实Java收集器确实保留该Pair；本轮进一步确认，真实NPJT在零行查询时抛异常。前者不能作为“数据库缺失记录会返回null”的证据。缺失定义的全业务入口可达性仍未证明：预订父流程会从事件服务列表中匹配请求，而本轮跨事件反例证明的是数据库约束及收集器边界不足以支持无条件替换。

两个连接通过明确的查询／提交位置交错，无随机调度或等待猜测。并发反例在原收集器及真实仓储层成立；没有执行整个预订入口的并发端到端证明，也不能据此断言所有父级路径都允许同样交错。尚无已验证的父级契约可保证整段视图输入稳定，因此不据局部读写锁或外键推断可以融合。视图还读取event货币及FREE明细计数，后续稳定性证明必须覆盖这些依赖。

共享分组表示现在显式要求 `preserve_statement_snapshots_and_visibility`、`preserve_lookup_cardinality_failures`、`preserve_privilege_checks_on_empty_input`。这些是执行语义义务，不是alf.io专用选择规则。原DP已有的 `unmet_semantic_obligation` 检查会拒绝缺少保证的合成后端；三个核心文件未变，实际节点继续 `database=null`。源码／SQL支持、组内及组间顺序、每组定义对象与完整返回值编码、首个失败位置仍未全部闭合。

**当前决定：** 保留原查询路径及原隔离级别。静态正常基线的完整行结果支持继续研究，但不能据此宣称通用等价。下一步若继续，应比较“明确约束为稳定数据快照”与“在服务器内保留原查询顺序和可见性”两种研究契约；后者同样需要验证，不能仅因放进一个函数就假定等价。当前没有批准任何一种语义放宽，也不开始性能测量。

## 覆盖范围与实际结果

| 层级 | 阶段数 | 本轮表示内容 | 是否能交给原选择器 |
|---|---:|---|---|
| 预订入口 | 13 | 促销、预订头、类别循环、附加服务、总价、账单、审计、支付检查 | 否，8个阶段成本未闭合；审计已闭合为1次调用 |
| 单个类别处理 | 8 | 票码、锁定选票、数量检查、初始化、首票读取、价格处理 | 否，3个阶段成本未闭合 |
| 锁定选票 | 3 | 读取类别、构造状态值、互斥锁定查询 | 是，保留原路径，共2次execute |
| 普通票初始化子分支 | 3 | 完整批次、计数检查、全部附加字段处理 | 否，附加字段成本公式已恢复，但两个参数的有限合法范围未建立 |
| 非空普通批次 | 3 | 原Java构造、批次执行、计数求和 | 是，与上轮相同，共1次execute |
| 空批次边界 | 3 | 原方法的空输入边界 | 是，与上轮相同，0次execute |
| 审计默认方法转发 | 2 | 完整实参→SQL调用→原返回值 | 是，1次execute，保留原路径 |
| 附加服务完整分组收集 | 4 | 全部明细→原分组→逐组定义查询→完整返回 | 是，`1+G`次execute，保留原路径；类型上界为保守外包络 |

空批次仅是Repository方法的边界检查，不声称业务接口允许预订零张票。普通初始化是 `specialPrices.isEmpty()` 下的子流程；特殊票价分支仍留在父阶段中，没有删除，也没有将两个分支当作顺序执行。

预订入口、类别处理、锁定选票和普通初始化各自范围内的50、50、9、12个调用指令／lambda句柄条目，都恰好分配到一个阶段。合计121个静态条目的归属已检查。这只是调用覆盖，不是121个调用的效果全部被理解，更不是完整自动依赖图。

## 已核实的成本关系

**锁定选票是2次调用，不是3次。** 先读取类别，然后根据有界／无界类别条件，执行两条锁定SELECT中的一条。两条原SQL均保留，前端将整个互斥阶段记为一次调用；没有从样本里选择某一个分支并删掉另一个。

**可选促销查询直接复用原仿射模型。** 定义输入参数 `promotion_code_present ∈ {0,1}`，其值由输入Optional是否存在决定；该阶段成本为这个参数。字节码确认对应lambda只有原Event ID读取和一次促销Repository查询。这里与第一项目Payment的布尔成本参数使用同一模型，没有新增概率权重或选择规则。

折扣扩展、动态类别循环、成本计算器和支付配置等仍有11个阶段使用显式 `java_calls=null`。附加字段与审计在原层级底稿中保持未知，由摘要在最新组合入口补充；原夹具没有附加字段配置不能证明其查询或写入可以消失，测试中的1／2／3张票也没有变成循环次数上限。

### 分组查询：有依据的有限成本域与完整数据依赖

`ReservationCostCalculator.collectAdditionalServiceItems` 的Event路径先查询全部附加服务明细，按 `AdditionalServiceItem.getAdditionalServiceId()` 分组，再对每个组查询一次服务定义，返回每个定义及对应的完整明细列表。字节码提取连接4个方法／24处调用和lambda句柄，原两条SQL全文保留。分组语义仍是固定源码／字节码绑定的人工核验，尚未自动识别任意循环。

成功完成原完整收集操作时，成本为 `1+G`，G是不同服务ID的数学数量。每项ID是Java int，所以 `0 ≤ G ≤ 2^32`；这个上界来自键的值域，不来自夹具、Map.size上限、票数假设或用户新增约束。它是可达输入域的**保守外包络**，不声称应用能实际生成这么多组，也不声称原DP在该参数箱上的最优性等于真实工作负载的最优性。原DP可直接消费该有限非负域，无需改目标函数。共享构造器对未知引用类型的键保留未知上界，仍拒绝生成方案。

这段的调用次数随数据变化，具备进一步审查的价值：若将来共享后端能合法地用一次执行实现完整区域，调用次数的条件化差额为G；G为零时无差额。目前所有阶段仍 `database=null`，缺少已准入的改写和传值编码，原DP保留原路径。这个条件计算不是已选方案、性能收益或删除权限。

恢复的值／控制关系为“完整明细→分组列表→按组取定义→完整Pair列表”。测试确认：同一对象重复出现、内容相同的不同实例都保留；组内顺序及服务定义原引用保留；组之间只遵循原HashMap迭代次序，不假定排序。输入明细不被改写或去重。服务定义返回null时，原收集器保留Pair；不能自行过滤它，后续价格计算行为仍在本子流程之外。

**成本必须绑定实际终结操作。** 原流构造阶段已经读完明细并完成分组，尚未查询服务定义；取一个结果只查询一个组，构造后直接关闭不会补查。当前Java17探针还确认，对有大小信息的原流调用 `count()` 可跳过map中的定义查询。因此不能把 `1+G` 套给所有流消费者；它只适用于原 `collect(toList())` 成功完成的路径。明细中的null会在分组期间失败，此时尚未查询任何定义；第一个或第二个定义查询失败时，保留之前的调用前缀，不执行剩余组。

最新组合入口只给父级 `reservation_total` 标记已知子调用。预订读取、上下文查找、票券、折扣／订阅及价格计算仍未全部闭合，父阶段继续 `java_calls=null`。原11个未知阶段数量未减少，不把一个子流程的成本冒充整个总价计算的成本。订阅分支仍由父级保留，位于本子流程范围之外，本轮未删除或验证其完整行为。

### 自动恢复包装层的参数与返回值来源

新增共享分析器 `forwarding_contracts.py`，从JVM操作数栈和参数局部变量槽恢复传值来源，不包含alf.io包名、方法名或业务判定规则。当前支持接口实例方法的一小段直线字节码：参数读取、部分常量、已知null的强制转换、方法调用、丢弃结果和返回。long等双槽参数按实际局部变量槽处理。分支、异常处理、非空值强制转换、未知指令及递归都保留未知；类／静态方法也不在当前适用范围内，避免遗漏初始化行为。

成本组合需要明确提供SQL终端契约，并假定调用原接口默认实现、同一原NPJT代理上没有自定义覆盖。外部接收者／未闭合调用不会被算成零。即使返回值被丢弃，调用及其顺序仍保留。原形式参数分别用来源符号表示：重复使用一个参数保留同一引用；不同参数不因内容相等而合并，也不凭不同符号断言运行时一定不互相别名。

在锁定版本的审计调用中，自动恢复出：七个参数依次传给八参数SQL方法，最后补 `(String)null`，返回原SQL方法的int结果。SQL的八个命名绑定逐项连接到参数来源。由此生成有类型的“终端调用→返回”两节点DAG；返回值依赖和原效果顺序显式保留，不再以不透明frame表示这个包装层。SQL终端的单次调用契约仍由原NPJT注解和固定版本绑定提供，不能仅从“有一个调用指令”推断出SQL成本。

父级审计阶段的正常路径成本由未知收敛为1。父流程仍保留原frame依赖，Event ID、装箱和 `new Date()` 留在原时点；没有将时钟读取搬进数据库。新增DAG同样交给原DP，结果保留Java路径。所有新节点 `database=null`、传值编码目录为空，恢复参数来源不自动授权序列化、融合或删模块。

官方二进制代理测试核对0／1／-3三种返回计数、完整实参、不同实例但内容相等的字符串、Date原引用、异常原实例及不重试。另一个合成接口验证两层转发、重复引用、long值、丢弃返回值但保留调用、两次有序效果；负控覆盖分支、异常处理、外部调用、非空强制转换、递归、类／静态范围及错误终端契约。更换包名和方法名后得到相同传值／成本结果。合成控制验证共享机制，不代表第二个真实系统的完整泛化证明。全部测试未执行真实SQL。

### 附加字段：公式与语义已恢复，仍不授权融合

普通Event路径成功完成时，单次 `updateOrInsert` 成本为 `2 + w`；整个普通票初始化附加字段循环成本为 `2A + W`。`A` 是原票券循环中附加映射非null的处理次数，`W` 是实际发出的UPDATE／DELETE／INSERT调用总数，**不是受影响行数，也不是输入字段数量**。W受配置、已有数据、只读性及值内容共同决定，不能直接当作一个独立输入长度。完整初始化子流程因而是 `1 + 2A + W`（非空普通批次）。这些公式只描述成功路径的调用次数。

原始二进制默认方法的验证确认：

- null／空映射直接传入Repository都会先读配置、再读已有值。父方法的原 `hasAdditionalData()` 则跳过null映射，保留空映射。
- 版本条件选择两种已有值查询之一；不能把两种SQL相加，也不能删掉另一个分支。
- 已有且可写的值根据转换后的内容更新或删除；缺失但已配置且非空的值插入。只读标记阻止修改已有值，**缺失只读字段仍可插入**。已有值即使不在当前配置中也可能更新。
- 更新／删除使用读回值携带的ID；插入使用调用参数、组织ID和配置ID。列表中的顺序、重复项、null都保留；不是按样本裁剪字段或数据。
- 先完成两次读取和映射构造，再按原映射迭代顺序处理。重复配置／已有值名称会在写入前失败；null字段列表即使属于未知字段也会失败，且此前的写入调用已发出。查询或写入失败后不再处理后续项。

摘要保留六处原SQL、条件关系、值来源与失败前执行顺序；新增节点仍保留原Java依赖、`database=null`及空传值编码目录。两项成本参数显式为未知范围，没有从夹具发明上界。现有DP需要有限参数域，因此仍不生成全局方案。该摘要是固定源码／字节码绑定的人工核验结果，尚非自动恢复的精确SSA。

探针调用官方JAR中的真实默认方法和模型；用代理记录抽象Repository调用、返回合成数据并注入错误。它验证控制／传值行为，**不验证真实JDBC、数据库最终状态、并发或回滚效果**；订阅分支未纳入本次验证。所有生产类及库逐文件与锁定JAR核对，并沿用官方classpath顺序。

## 同一算法如何接收不完整输入

新增[共享输入完整性检查](../scripts/placement_frontend.py)，不含项目名称、业务规则或新的优化算法：

- 成本及所有参数域完整：原样调用 `choose_placement`，返回原选择结果。
- 存在未知成本或缺失／非法参数域：返回 `incomplete_frontend_no_global_plan`、缺口清单，以及 `selection=null`。

它不会给未知阶段补零，不会用测得平均耗时或某次执行次数代替合法输入域的成本，也不会把未知区域手工切掉后声称得到整个事务的最优方案。对NewOrder和Payment增加未知成本的负向检查中，确认没有调用优化器；原完整输入则得到与历史归档完全相同的选择JSON。

仍保持不变的三个核心文件是 `fusion_placement.py`、`fusion_regions.py`、`fusion_placement_contracts.py`，SHA-256与第一项目归档一致。

## 目前还缺什么

1. **调用成本摘要。** 分支和循环必须从源码义务建立参数化成本，跨模块调用不能仅由动态SQL次数替代。
2. **精确值依赖和传值接口。** 审计包装层已有自动恢复的参数／调用结果来源；其他父级阶段仍使用保守的Java状态依赖表达顺序，没有为这些状态虚构数据库传值编码。这还不是全流程逐变量SSA、堆别名或可变对象分析。
3. **共享SQL能力和合格后端。** 原SQL全文保留，旧解析器对命名参数、常量、类型转换和部分锁定语法的限制仍在；本轮没有为alf.io放宽。新增阶段也没有凭借已有SQL就自动获得数据库后端资格。

因此，锁定选票虽已有完整调用成本，仍没有已准入的数据库改写和完整值接口，当前同一选择器保留原路径。预订入口等则根本还没有全局选择结果。这两种状态不能混为一谈。

父级阶段划分仍由人工核验，并绑定固定版本的方法指令范围。适配器检查Manager／Repository字节码与反编译源码哈希；源码变化后拒绝沿用旧范围。局部转发分析自动处理其支持的指令，但不能使整个前端成为处理任意版本的完整软件分析器。

## 验证与下一步

最新保守保留归档 `20261106-abb2849f09ce`：12文件／18项检查通过，新增模块删除数为0；当前原方法继续承担全部映射、校验和异常行为。共享前端变更后的原层级回归为 `20261106-c38523d2f5e0`，26文件／32项检查通过。两个归档均无数据库执行或性能样本。

最新有序查询归档 `20261106-ce2f659e8185`：9场景／21项数据库检查、12项来源／回归检查通过，共51个manifest文件。使用前轮完整数据库dump，官方仓储、映射器、驱动及原收集器保持原版；冻结函数属性、无插桩函数、两个对照函数、每例结果、最终dump和清理记录。没有新增性能样本或替换默认应用。

最新共享义务回归 `20261106-c5649c536147`：11文件／11项检查通过，引用真实映射时机反例，验证四项语义义务均被原通用检查覆盖。原核心文件、NewOrder／Payment和分组的完整选择结果不变，未新增数据库执行。

JOIN审查归档 `20261106-11a212bff8f2` 共46个manifest文件，7个数据库场景／16项检查、11项来源及原算法回归通过。完整行结果、真实缺失异常、两连接交错结果、视图／约束定义、最终数据库dump与资源清理记录保留。`20261106-60797b4039f0` 的合成事件唯一键冲突、`20261106-1ab37b0970c7` 的镜像初始化临时服务就绪竞态均属于准备失败；修正为新唯一键和正式TCP就绪检测后通过，失败归档及清理记录也保留。没有禁用约束来使测试通过。

共享语义义务回归归档 `20261106-4e6aa67916e0` 共11文件、10项检查通过，没有新增数据库执行。它核验既有分组选择及NewOrder／Payment完整选择不变，并用缺少上述三项保证的假想后端验证原DP拒绝理由。该假想后端仅是负控，没有写入能力目录。

最新分组归档 `20261106-6d78cea0bd77`：12场景／61项官方二进制行为检查、39项前端／来源／回归检查通过，共35个manifest文件。覆盖空输入、单组、多组交错、重复引用、相同内容的不同实例、整数键边界、惰性消费／不消费／count、查询失败前缀、null明细及null定义。官方计算器与原模型执行，Repository由记录代理替代；没有真实SQL、事务、并发或性能验证。初始11场景／60检查归档 `20261106-facad5ede85e` 保留，最终版补充count反例。

三个原优化核心文件、NewOrder／Payment完整选择、前一轮全部7个层级的选择结果均不变。字节码提取器只新增lambda句柄事实，原审计全部方法的转发分析结果逐项不变。共享分组表示更换来源名称后成本与选择不变；字符串键没有证据上界时明确阻断选择。

前轮归档 `20261106-f5e1b6f4d3f7`：46项前端／来源／回归检查、24项二进制检查通过，共44个manifest文件。除审计阶段外，先前父级阶段／子流程保持原契约。普通初始化的两个参数域继续未知，预订入口8个、类别3个成本阶段继续未知。

开发记录也完整保留：`20261106-ac3f4748f3ca` 首次分析保守拒绝了原方法的 `CHECKCAST null`，二进制行为探针本身已通过；随后只添加已证明为null的转换处理，非空转换继续未知。`20261106-ea502c269542` 为通过的中间版本；最终版本进一步限定接口实例方法范围，并检查终端返回类型。没有重跑性能或按测试结果挑选数据。

原层级套件32项检查覆盖核心身份、旧项目选择回归、未知成本阻断、两种成本关系、源码范围覆盖、源码变更拒绝、父子关系、初始化必须先于首票读取、未附加摘要时未知保留，以及原批次选择不变。共享检查更新后的回归归档为 `20261106-54038c937dd0`。

附加字段最终归档 `20261106-5db5c3f27397` 包含14场景／47项二进制检查和37项前端及来源检查。覆盖全部三种写入及其错误、只读／缺失／未知字段、重复名称、空值／多值、参数来源、原始顺序；使用0受影响行的代理返回值，确认调用次数与行数不是同一概念。另核实有限域缺失、负值、倒序、非整数、布尔值和长度错误都会阻止调用优化器。有限合成参数仅用于检查转发机制，不充当应用合法范围。早期10场景／33项探针归档 `20261106-14f071254af2` 也保留。

本轮保留三个静态开发归档：`20261106-9ef3c64242d8`完成初始层级表示；`20261106-bff416c900ac`补充可选促销成本；最终 `20261106-cda72b0950d6`增加源码版本绑定检查。初始测试脚本的一处括号错误在启动实验前修复。没有按性能结果改变目标、过滤数据或追加性能试验。

该映射边界当前收敛为保留，不再自动推进迁移、替代校验或正常数据域收缩。后续优化只考虑证据已闭合且不跨越保留边界的区域，继续使用同一算法；本报告中的历史实验建议不恢复执行。

## 产物

- [显式保留契约](../scripts/grouped_lookup_contracts.py)、[共享前端约束检查](../scripts/placement_frontend.py)
- [最新保留决定与18项回归](../evidence/grouped-lookup-admission/20261106-abb2849f09ce/run.json)、[保留契约](../evidence/grouped-lookup-admission/20261106-abb2849f09ce/contract.json)、[覆盖保证也不能迁移的负控](../evidence/grouped-lookup-admission/20261106-abb2849f09ce/retention-negative-controls.json)、[原层级32项回归](../evidence/alfio-flow-placement/20261106-c38523d2f5e0/run.json)
- [有序查询机制验证入口](../scripts/test_alfio_ordered_service.py)、[数据库探针](../prototype/alfio/AlfioOrderedServiceProbe.java)、[未准入的有序函数](../prototype/alfio/ordered_service_lookup.sql)
- [9场景及检查结果](../evidence/alfio-ordered-service/20261106-ce2f659e8185/result.json)、[函数安全／易变性属性](../evidence/alfio-ordered-service/20261106-ce2f659e8185/function-properties.json)、[准入边界](../evidence/alfio-ordered-service/20261106-ce2f659e8185/admission.json)、[51文件manifest](../evidence/alfio-ordered-service/20261106-ce2f659e8185/manifest.json)
- [最新四项义务的负控](../evidence/grouped-lookup-admission/20261106-c5649c536147/negative-control.json)、[11项共享回归](../evidence/grouped-lookup-admission/20261106-c5649c536147/run.json)
- [真实数据库审查入口](../scripts/audit_alfio_service_join.py)、[原仓储／计算器探针](../prototype/alfio/AlfioServiceJoinAudit.java)、[未准入的JOIN诊断SQL](../prototype/alfio/service_group_join_diagnostic.sql)
- [数据库审查结果](../evidence/alfio-service-join-audit/20261106-11a212bff8f2/result.json)、[缺失定义实测](../evidence/alfio-service-join-audit/20261106-11a212bff8f2/missing-definition.json)、[准入判断](../evidence/alfio-service-join-audit/20261106-11a212bff8f2/admission.json)、[46文件manifest](../evidence/alfio-service-join-audit/20261106-11a212bff8f2/manifest.json)
- [共享义务回归入口](../scripts/test_grouped_lookup_admission.py)、[负控拒绝理由](../evidence/grouped-lookup-admission/20261106-4e6aa67916e0/negative-control.json)、[10项回归记录](../evidence/grouped-lookup-admission/20261106-4e6aa67916e0/run.json)
- [共享分组成本／值关系构造器](../scripts/grouped_lookup_contracts.py)、[alf.io源码绑定](../scripts/alfio_service_group_contracts.py)、[官方计算器探针](../prototype/alfio/AlfioServiceGroupProbe.java)、[验证入口](../scripts/test_alfio_service_groups.py)
- [最新分组运行记录](../evidence/alfio-service-groups/20261106-6d78cea0bd77/run.json)、[35文件manifest](../evidence/alfio-service-groups/20261106-6d78cea0bd77/manifest.json)、[12场景与完整检查](../evidence/alfio-service-groups/20261106-6d78cea0bd77/binary-probe.json)
- [完整分组契约及类型上界](../evidence/alfio-service-groups/20261106-6d78cea0bd77/service-group-contract.json)、[原DP结果](../evidence/alfio-service-groups/20261106-6d78cea0bd77/service-group-result.json)、[结论和边界](../evidence/alfio-service-groups/20261106-6d78cea0bd77/conclusion.json)
- [共享传值与组合分析器](../scripts/forwarding_contracts.py)、[字节码提取器](../prototype/alfio/ForwardingBytecodeExtract.java)、[审计组合入口](../scripts/alfio_forwarding_contracts.py)、[验证入口](../scripts/test_alfio_forwarding.py)
- [最新转发运行记录](../evidence/alfio-forwarding/20261106-f5e1b6f4d3f7/run.json)、[44文件manifest](../evidence/alfio-forwarding/20261106-f5e1b6f4d3f7/manifest.json)、[二进制行为检查](../evidence/alfio-forwarding/20261106-f5e1b6f4d3f7/binary-probe.json)
- [恢复的参数→SQL绑定](../evidence/alfio-forwarding/20261106-f5e1b6f4d3f7/audit-summary.json)、[有类型的转发DAG](../evidence/alfio-forwarding/20261106-f5e1b6f4d3f7/audit_forwarding-contract.json)、[原DP选择结果](../evidence/alfio-forwarding/20261106-f5e1b6f4d3f7/audit_forwarding-result.json)、[结论及边界](../evidence/alfio-forwarding/20261106-f5e1b6f4d3f7/conclusion.json)
- [附加字段摘要与组合入口](../scripts/alfio_field_contracts.py)、[原始二进制探针](../prototype/alfio/AlfioFieldContractProbe.java)、[新增验证入口](../scripts/test_alfio_field_contract.py)
- [最新运行记录](../evidence/alfio-field-contract/20261106-5db5c3f27397/run.json)、[完整性manifest](../evidence/alfio-field-contract/20261106-5db5c3f27397/manifest.json)、[14场景原始参数和检查](../evidence/alfio-field-contract/20261106-5db5c3f27397/binary-probe.json)
- [源级效果摘要](../evidence/alfio-field-contract/20261106-5db5c3f27397/field-summary.json)、[组合后的初始化契约](../evidence/alfio-field-contract/20261106-5db5c3f27397/ordinary-initialization-contract.json)、[未生成方案的原因](../evidence/alfio-field-contract/20261106-5db5c3f27397/ordinary-initialization-result.json)
- [共享完整性检查](../scripts/placement_frontend.py)、[alf.io层级前端](../scripts/alfio_flow_contracts.py)、[静态验证入口](../scripts/test_alfio_flow_placement.py)
- [运行记录](../evidence/alfio-flow-placement/20261106-cda72b0950d6/run.json)、[26文件manifest](../evidence/alfio-flow-placement/20261106-cda72b0950d6/manifest.json)
- [层级连接](../evidence/alfio-flow-placement/20261106-cda72b0950d6/hierarchy.json)、[阶段能力与成本清单](../evidence/alfio-flow-placement/20261106-cda72b0950d6/capability-ledger.json)、[结论和覆盖范围](../evidence/alfio-flow-placement/20261106-cda72b0950d6/conclusion.json)
- [预订入口缺口](../evidence/alfio-flow-placement/20261106-cda72b0950d6/reservation_entry-result.json)、[类别缺口](../evidence/alfio-flow-placement/20261106-cda72b0950d6/category_body-result.json)、[锁定选票结果](../evidence/alfio-flow-placement/20261106-cda72b0950d6/locked_selection-result.json)

```sh
python3 -B scripts/test_alfio_flow_placement.py
python3 -B scripts/test_alfio_field_contract.py
python3 -B scripts/test_alfio_forwarding.py
python3 -B scripts/test_alfio_service_groups.py
python3 -B scripts/audit_alfio_service_join.py
python3 -B scripts/test_alfio_ordered_service.py
python3 -B scripts/test_grouped_lookup_admission.py
```
