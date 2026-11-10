# Saleor：守卫、对象更新与待写入列表的自动关联

2026-11-04。已完成第二个真实来源的受限依赖提取：**从 Saleor 原始函数提取守卫字段、对象字段写入、直接/条件入列，并关联调用方的字段投影和 ORM 调用。** 16 项边界检查通过，与四条已冻结 Saleor 契约一致。

输入为原始源码、DTO 定义、明确的函数/参数角色以及能力契约。没有执行应用、生成新标签、重新拟合或批准删除；四条已知记录只是源码一致性核对，不能视为新的预测准确率。

实现：[write_plan_bindings.py](../prototype/module_pruning/write_plan_bindings.py)；固定范围：[write_plan_protocol.json](../prototype/module_pruning/write_plan_protocol.json)；入口：[extract_write_plan_bindings.py](../scripts/extract_write_plan_bindings.py)。

## 实际提取链路

从冻结的 `saleor/checkout/utils.py` 与 `CheckoutLineData` 定义提取：

```text
DTO 布尔守卫及嵌套条件
    → line 的字段写入
    → to_update.append(line) / 不在列表时才 append
    → 调用方将同名局部列表传入 checkout_lines_bulk_update
    → 字面字段列表传入 CheckoutLine.objects.bulk_update
```

函数名和参数角色由绑定输入声明；写入字段、守卫、入列位置、调用参数映射和字段列表从 AST 获取，不按标签或案例名称填写。

| 写入位置 | 自动识别的字段/操作 | 后续入列关系 |
|---|---|---|
| 340 行 | quantity，替换赋值 | 343 行直接 append |
| 342 行 | quantity，增量 Add | 343 行直接 append |
| 346 行 | price_override，赋值 | 347 行直接 append |
| 350 行 | price_override_reason，赋值 | 351–352 行不在列表时 append |

每个写入保留完整嵌套守卫，例如价格写入关联 `line_data.custom_price_to_update` 和 `line not in to_delete`。数量更新还保留 quantity 正值条件以及 replace 分支。方法采用结构上的可能路径，不求解任意条件可行性，也不把静态写入位置数当成实际执行次数。

调用方关联到 249 行创建的 `to_update`，255 行传给 helper，267 行传给 bulk wrapper；字段列表为 quantity、price_override、price_override_reason、metadata。wrapper 在 142 行将两个输入形参转发到 `CheckoutLine.objects.bulk_update`。这个关系描述的是源程序参数传递，**不是数据库最终写入成功的证明**。

## 能力契约的作用

| 上下文 | 三个守卫区域中的可能写入位置 | 通向已识别投影调用的可能位置 |
|---|---:|---:|
| 价格与原因能力明确关闭 | 数量的两个位置 | 2 |
| 两项能力均允许启用/关闭 | 全部四个位置 | 4 |
| 调用方标志未知 | 全部四个位置 | 4 |

关闭价格能力不会丢掉数量更新。DTO 的默认值 false 仅是源码声明，不能替代未知请求的能力边界；没有明确上下文时保留 true/false 两种可能。

四条旧标签的核对均一致：禁用契约下两个价格区域不可达；启用契约下保留两个区域的可能依赖，分别对应已有删除反例。所有自动事实先封存，再按源码行号与 AST 哈希关联旧记录；提取器不读取标签、测试差异或概率。

## 新明确的难点：相等对象未必是同一个实例

原因更新的源码先修改 `line.price_override_reason`，再检查 `line not in to_update`。如果列表里已有相等但不同的对象实例，成员判断可能为 true，append 被跳过，而已修改的实例没有进入列表。

因此输出分别记录：

- 执行 append 时，加入的是当前 `line` 引用。
- 因已有成员而跳过 append 时，**相等性不能证明实例身份一致**，该关系保持 unknown。
- 无论哪一种，都不直接推断最终提交效果。

一项独立边界检查构造了两个主键相等、实例不同的对象，展示修改新实例后旧实例仍可独占列表。这不是确认 Saleor 当前路径存在该缺陷；是否会出现这种别名关系仍需调用上下文证据。原标签夹具在“已入列”场景中使用同一实例，其边界标签没有因此被改写或扩展。

## 未知效果与拒绝规则

`line.store_value_in_metadata` 当前仅识别为外部/对象调用，未展开其效果。metadata 虽在 bulk 字段列表中，却没有本轮直接属性写入记录；**这表示效果尚未建模，不表示该字段或模块无用**。

helper 中的参数重绑定、DTO 字段改写、角色别名/逃逸、反射、额外队列修改、提前退出和超出语法范围的控制结构会返回 unknown。无法识别后续入列关系时，通向 bulk 的关系是 null，不能填成 false。

调用方分析只接受一个明确 helper 调用与后续 wrapper 调用、同一个新建局部列表、字面字段投影和 wrapper 的直接转发。队列重新赋值、清空、逃逸、链式初始化产生别名、角色同名绑定、字段列表在 wrapper 内被修改等使调用方关联退为 unknown；局部写入事实可以继续保留。

实现仍依赖明确的普通 DTO 字段和参数角色、没有隐藏回调修改 DTO、没有外部函数替换等边界假设。没有证明整个调用方控制流，也没有验证事务、锁、权限、触发器或提交。调用方在 bulk_update 之前还可能删除行，不能从待写入列表推断这些行最终仍存在。

输出始终保持 `effect_summary_complete=false`、`database_effect_summary_complete=false`、`deletion_authorized=false`。四条关联记录的全应用标签和概率均为 null，评分保持 abstain，不映射成旧有限夹具的布尔评分输入。

## 验证与停止点

16 项检查覆盖：完整源关系、关闭能力与默认值区别、条件入列身份未知、相等但不同实例的反例、投影遗漏、入列证据缺失、helper/caller 队列修改、队列链式初始化别名、wrapper 投影修改、DTO/receiver 变化、调用角色别名、提前返回、DTO 属性钩子、标签注入、字段改名和未闭合外部效果。

初版 [c19d4897c7ec](../evidence/module-write-plan/20261104-c19d4897c7ec/report.json) 的 15 项检查通过后，代码复核补充了队列链式初始化的别名拒绝与一项检查，形成最终归档。两次静态提取都保留，没有增加数据样本或调整标签。

- [最终报告](../evidence/module-write-plan/20261104-7e5f16d9ac2b/report.json)
- [守卫、对象写入、入列及调用方事实](../evidence/module-write-plan/20261104-7e5f16d9ac2b/sealed_write_plan_facts.json)
- [四条既有契约的源码核对](../evidence/module-write-plan/20261104-7e5f16d9ac2b/label_consistency.json)
- [检查日志](../evidence/module-write-plan/20261104-7e5f16d9ac2b/tests.log)、[输入冻结](../evidence/module-write-plan/20261104-7e5f16d9ac2b/freeze.json)、[文件哈希](../evidence/module-write-plan/20261104-7e5f16d9ac2b/manifest.json)

至此，Paperless 的策略绑定和 Saleor 的对象/写入计划已有各自的自动证据链。本轮停止扩展。下一步应先做两个来源的证据汇总与收敛审查，统一能力契约、模块边界、观察对象和未知义务，判断哪些证据具备评分条件；不直接重启同一小集上的模型比较。
