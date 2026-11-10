# 两个真实源码来源的契约标签首批

2026-11-04。首批已完成：**两个仓库、四个源码区域、八条契约记录，得到四个条件可删标签和四个必要性反例**。576 对原版/删除版比较（1152 次边界执行）与五项标签边界检查完成。没有改动上游应用、现有提取器、模型参数或评分规则，也没有用新标签拟合模型。

这些是固定真实源码边界的标签，**不是完整应用等价性标签，也不是新的反编译产物评测**。两个项目之前已被本项目研究过，但本批选中的区域没有参与前面的特征拟合；仍由同一研究者选择和裁定，不是独立人员盲测。

实现：[source_labels.py](../prototype/module_pruning/source_labels.py)；协议：[source_label_protocol.json](../prototype/module_pruning/source_label_protocol.json)；执行：[collect_source_labels.py](../scripts/collect_source_labels.py)。

## 来源与分组

| 来源 | 锁定 commit | 本批区域 |
|---|---|---|
| Paperless-ngx | `1b8b861a6072fd85aad68847c0d312d7a9fd2ca0` | `BulkArchiveStrategy._formatted_filepath`、`_filename_only` |
| Saleor | `0e3fa1b022558b33fb9ff5efdadebd3bbccdb83b` | `_append_line_to_update` 内的自定义价格与价格原因两个守卫区域 |

执行前将六份相关源文件逐字节与锁定 commit 的 Git blob 对比，冻结源码、调用方、DTO/元数据方法及许可证；再封存删除差异和契约论证，之后才进行比较。所有删除仅作用于归档中的 AST/源码副本。

两个命名策略和两个价格区域分别同源，且每个区域的启用/禁用契约成对。将来划分训练/验证时必须按仓库整组隔离，不能把八条记录当成八个独立软件系统，也不能拆开相同区域的两个契约。旧自编有限程序保持另一来源组，不与这些新记录混算覆盖率。

## 标签定义

标签 1：在声明契约内，原版成功，删除版出现可观察差异，保存实际反例。

标签 0：必须同时满足源码不可达论证、明确能力限制和全部回归比较一致。不能只因有限测试没有触发某个分支就标 0。没有论证或反例时，标签为 null。全应用标签 `full_application_label` 对八条记录均为 null。

**禁用契约是明确规定每次调用都关闭某项功能的能力配置，不是从一批请求推断出来的规律。** 当前公开接口仍可请求启用这些功能；本批没有在应用里部署该配置限制，因此不能直接据标签删除生产代码。

## Paperless：真实 ZIP 命名策略

执行整份冻结的 `bulk_download.py`，只移除一个私有方法，其他方法和调用不重写。真实标准库 ZIP 操作读取临时文件并生成归档；文档 ORM 对象通过声明的文档接口替身提供字段和确定性的公开文件名生成。HTTP、权限解析、ORM、源码反射及其他直接调用私有方法的入口不在该边界内。

构造函数由固定 `follow_formatting` 选择一个命名方法。源码检查确认被删除方法的唯一语法引用就是该构造分支中的绑定；未选择分支不执行，因此在禁止反射/动态替换/其他入口的契约下，可删除未绑定方法。论证不依赖文档 ID、文件字节、某个已观察文件名或样本数量。

每条契约包含四组文档（空、单个、同名碰撞、原件/存档回退）、三种导出策略、两种压缩方式，共 24 对比较。观察有序 ZIP 条目、完整文件内容以及异常；不比较时间戳等导致变化的原始 ZIP 编码。

| 删除区域 | 保留能力契约 | 标签 | 比较对数 / 差异数 |
|---|---|---:|---:|
| `_formatted_filepath`，48–70 行 | 固定 `follow_formatting=False`，保留普通命名导出 | 0，条件可删 | 24 / 0 |
| 同一区域 | 固定 `follow_formatting=True`，保留格式化路径导出 | 1 | 24 / 24 |
| `_filename_only`，24–46 行 | 固定 `follow_formatting=True`，保留格式化路径导出 | 0，条件可删 | 24 / 0 |
| 同一区域 | 固定 `follow_formatting=False`，保留普通命名及碰撞处理 | 1 | 24 / 24 |

必要性反例首先出现在策略构造阶段：原版成功，删除被选择的方法后产生 `AttributeError`，甚至不需要文档非空。这是所声明“物理移除方法且保留调用方”的反例，不能表述为已证明所有其他重写方案都不可行。

调用依据：`BulkDownloadSerializer` 接受 `follow_formatting`；`BulkDownloadView.post` 将它传给策略。其默认值 false 不等于所有允许请求都是 false，所以仅凭默认值不能给整个现有 endpoint 的格式化能力打可删标签。

## Saleor：对象状态与数据库写入计划边界

编译原始 `_append_line_to_update`、真实 `CheckoutLineData` dataclass 与原始 `store_value_in_metadata` 方法的 AST，保留函数体，只补充所需标准库导入和延迟注解。行对象采用普通字段适配器，观察 quantity、price_override、price_override_reason、metadata，以及保留重复和顺序的 `to_update` / `to_delete` 身份列表。

禁用契约明确要求两个价格标志始终为 false，保留数量/元数据更新。源码检查确认被删区域只由精确的普通 dataclass 布尔字段控制，没有 else；该字段读取无自定义属性钩子，禁用时区域不可达。价格、数量、原因等业务值不被限制为测试中的几个常量。

启用契约允许两个价格标志变化。每个区域覆盖数量更新标志、负/零/正数量、追加/替换、待删除状态、已在写入列表状态；元数据载荷按固定规则变化。禁用契约 48 对，启用契约 192 对。

| 删除区域 | 保留能力契约 | 标签 | 比较对数 / 差异数 |
|---|---|---:|---:|
| `if line_data.custom_price_to_update`，344–347 行 | 数量/元数据能力，价格与原因更新始终关闭 | 0，条件可删 | 48 / 0 |
| 同一区域 | 同时支持价格与原因更新 | 1 | 192 / 48 |
| `if line_data.custom_price_reason_to_update`，348–352 行 | 数量/元数据能力，价格与原因更新始终关闭 | 0，条件可删 | 48 / 0 |
| 同一区域 | 同时支持价格与原因更新 | 1 | 192 / 48 |

价格反例中，原版将 `10.00` 改为 `7.50` 并加入写入列表，删除版保持 `10.00` 且没有该写入；原因反例中，原版把 `previous` 改为 `manual` 并入列，删除版保持旧原因。

这些值只是必要性反例的见证，不定义支持数据的全集。禁用契约的可删论证依赖控制前提，不依赖这些金额恰好出现。

调用方 `add_variants_to_checkout` 会把 `to_update` 交给 `checkout_lines_bulk_update`，明确指定 quantity、price_override、price_override_reason、metadata 字段，说明这两个区域与数据库写入需求有关。不过本机未加载 Django/ORM，全应用流程未执行；**此处只是源码函数的对象/写入计划标签，不能替代事务、触发器、权限或提交状态标签**。辅助函数接受的 DTO 状态也不能自动视为所有公开 API 都可达。

## 模型边界与尚缺的证据

四个负例全部来自“契约禁用功能使区域不可达”这一机制。它们适合校验能力边界，但还不代表一般的“已经执行、结果却无消费者”的冗余模块。因而不能声称本批覆盖了所有可删模式，或发现了新的运行加速机会；本轮没有计时。

现有有限 Python 夹具提取器不能直接处理这些真实类、属性、ZIP/ORM 接口。没有手工从标签填写布尔特征，也没有把未知变成 false；八条记录的 features 均为 null，评分全部 abstain，training_admitted=false。真实源码标签与模型可接受的自动证据之间仍缺少保守的绑定步骤。

下一步宜固定本批数据，只验证一个真实入口到模块的依赖提取，例如 Paperless 构造选项到策略方法的绑定。保留外部效果未知，不把真实程序改写成旧夹具格式来迁就模型。随后才能讨论按来源留出的评分验证。

## 证据入口

五项检查覆盖：测试全通过仍不能替代论证、必要性反例要求原版成功、改变能力配置使可删论证失效、新增消费者使不可达规则拒绝、守卫或 DTO 类型变化使规则拒绝。

- [完整报告](../evidence/module-source-labels/20261104-0593df8277a1/report.json)
- [八条标签、全部比较和反例](../evidence/module-source-labels/20261104-0593df8277a1/labels.json)
- [封存契约与来源](../evidence/module-source-labels/20261104-0593df8277a1/sealed_contracts.json)
- [删除副本与差异清单](../evidence/module-source-labels/20261104-0593df8277a1/mutation_seal.json)
- [检查日志](../evidence/module-source-labels/20261104-0593df8277a1/tests.log)、[文件哈希](../evidence/module-source-labels/20261104-0593df8277a1/manifest.json)

本轮只执行一次固定批次。八条边界记录均得到条件标签，不代表八个完整应用义务已经闭合；全应用标签仍为零个，自动删除授权仍为零次。
