# 冻结模型的扩大范围挑战：能力删减判定尚不能泛化

2026-11-04。本轮按预定边界完成 16 个新小程序的有限穷举与 3 个真实反编译模块的源码审查。**新挑战未支持升级模型：可提取子集的诊断 Brier=0.267368，差于固定 0.5 预测的 0.25。** 没有调整特征、模型或阈值，没有执行模块删除或性能实验。

这是一轮新源码挑战，不是独立收集、盲测的外部基准：小程序由同一研究者在已知开发结果的情况下编写，专门检查边界；三个真实模块均来自 BenchBase TPC-C。不能把它们当成 19 个独立软件系统，或据此估计真实世界错误率。真实侧完成的是源码能力审查，尚未完成新的数据库删减差分验证。

## 冻结、预测和标签

- [固定协议](../prototype/module_pruning/external_validation_protocol.json)、[新程序定义](../prototype/module_pruning/external_corpus.py)、[执行脚本](../scripts/validate_module_external.py)。
- 使用上一轮归档的完整 `effect_context` 模型、原效果提取器和原预测器；运行前对原归档 manifest 校验，并冻结全部输入及 16 份独立可运行源码。
- 每个程序输入 `x ∈ {-2,-1,0,1,2}`、`flag ∈ {false,true}`。先执行固定探针 `(0,true)`，提取特征，封存全部预测；再穷举每个程序的 10 个状态获得标签。总计 **160 对穷举比较 + 16 对探针比较，即 352 次有限程序执行**。
- 观察包含完整返回值或异常类型/消息。状态均为新建局部状态；有数据库状态含义的字段只是明确返回的有限代理，不是真实数据库执行。标签只适用于声明的有限域。
- 预测器收到真实挑战域 `new-finite-challenge/v1`，全部返回 `abstain`。另直接读取冻结模型的特征单元分数，作**域外诊断**，不伪装训练域，也不宣称已校准。
- 预设诊断分类规则为 `q < 0.5` 倾向可删，`q ≥ 0.5` 倾向保留；这是分类评估阈值，**不是删除准入阈值**。没有把 unknown 当作可删。

## 固定结果

| 项目 | 结果 |
|---|---:|
| 新程序 | 16 |
| 冻结提取器接受 / 拒绝 | 12 / 4 |
| 全部有限标签：必要 / 可删 | 11 / 5 |
| 可提取子集：必要 / 可删 | 8 / 4 |
| 域外诊断 Brier / log loss | 0.267368 / 0.729594 |
| 同子集固定 0.5 基线 Brier / log loss | 0.250000 / 0.693147 |
| 诊断倾向可删 | 8 |
| 危险建议 | 5/8 个必要模块；占可删建议的 5/8 |
| 不必要保留 | 1/4 个有限域可删模块 |
| 未见特征单元回退 | 0/12 |
| 正式域拒答 / 授权删除 | 16/16 / 0 |
| 评测器检查 | 4 项通过 |

全部 12 个可提取案例都落入已有特征单元，但其中一些语义不同。这说明“特征组合见过”不能替代“该程序效果可被当前抽象表达”。正式域限制避免了执行错误删减；全部拒答也意味着尚无部署覆盖率，不能把零实际误删当成模型准确性的证据。

| 新程序 | 有限域必要性标签 | 域外 q | 诊断结果 |
|---|---:|---:|---|
| overwritten_receipt | 0 | 0.600000 | 误保留：结果被后续覆盖 |
| checksum_chain | 1 | 0.600000 | 保留 |
| conditional_ledger | 1 | 0.600000 | 保留 |
| opposite_guard | 1 | 0.444444 | 危险建议 |
| redundant_guard | 0 | 0.444444 | 可删，仅有限契约 |
| preexisting_snapshot | 0 | 0.333333 | 可删，仅有限契约 |
| two_consumers | 1 | 0.666667 | 保留 |
| integer_identity | 0 | 0.444444 | 可删，仅整数契约 |
| reset_after_assignment | 1 | 0.444444 | 危险建议 |
| early_return | 1 | 0.363636 | 危险建议 |
| control_dependency | 1 | 0.333333 | 危险建议 |
| implicit_division_error | 1 | 0.333333 | 危险建议 |
| loop_accumulator | 1 | — | 不支持 For |
| method_side_effect | 1 | — | 不支持表达式语句/方法效果 |
| finally_cleanup | 0 | — | 不支持嵌套 Try |
| postmutation_copy | 1 | — | 拒绝模块后的复制时序 |

## 反例揭示的研究难点

1. **动作相同不代表守卫覆盖。** `opposite_guard` 的两个位置都抛 `DENIED`，但分别在 `x<0` 和 `x>0` 时执行；删去前者会放行负值。`redundant_guard` 的守卫相同，有限域内可删。二者的八项特征完全相同。
2. **局部变量会通过控制流影响能力。** `control_dependency` 的模块只更新 `reject`，返回值不直接读取它，但后续守卫读取它并决定拒绝。当前观察传播漏掉这条控制依赖。`early_return` 还显示模块里的提前返回不能当成“无写、无抛错、无效果”。
3. **效果必须考虑隐式异常和到达定义。** `implicit_division_error` 在 `x=-2` 时因除零失败，删去未使用的临时值计算后成功；源码没有显式 `raise`。`reset_after_assignment` 中 `stamp=0; stamp=x; [stamp=0]` 的最后赋值并非恒等，提取器却沿用早期常量，标成中性更新。

此外，`overwritten_receipt` 表明无时序的名字依赖也会误保留：后续赋值覆盖模块结果，当前特征仍认为模块结果可观察。

令 `φ(P,M,C)` 为当前八项特征，`Y(P,M,C)` 为该有限契约下是否必要。此次出现三个混合标签单元：

| 完全相同的特征键 | 标签为 0 的案例 | 标签为 1 的案例 |
|---|---|---|
| `01101000` | overwritten_receipt | checksum_chain |
| `11010001` | redundant_guard | opposite_guard |
| `11100000` | preexisting_snapshot | control_dependency、implicit_division_error |

因此存在 `φ(P₁,M₁,C)=φ(P₂,M₂,C)` 且 `Y₁≠Y₂`。**仅以这八项特征为输入的确定性分类器，无论如何调参数，都无法在这 12 个案例上全部正确**；每个混合单元至少错一个，经验错误数下界为 3/12。这个结论限定于当前抽象和本组案例，不是对所有程序或所有概率算法的不可能性证明。概率可用于安排审查优先级，但这些特征无法单独构成删除证明。

## 真实模块：能力依赖审查，未新增执行标签

[审查输入](../prototype/module_pruning/external_real_review.json)规定保留事务能力与提交后的业务状态，在有效行、正常成功提交且没有补偿写入/触发器恢复被删动作的范围内推理。原反编译源码与适配器按历史 manifest 校验、再次冻结；实际锚点和 SHA256 见归档 `real_review.json`。

| 模块 | 不依赖有限覆盖的保留理由 | 状态 |
|---|---|---|
| Payment.insertHistory | 丢弃调用返回值仍会新增 history 行；删除会改变提交后的历史关系 | 按声明契约保留 |
| NewOrder.updateDistrict | 推进 D_NEXT_O_ID，影响当前提交状态与未来订单分配能力 | 按声明契约保留 |
| Delivery.updateBalanceAndDelivery | 更新余额、递增配送次数；即便订单总额为零，次数仍改变 | 按声明契约保留 |

三个真实模块都保持域拒答；源码论证没有转换为训练标签，`executable_label=null`。它们没有提供真实可删负例，也没有证明完整 JDBC、异常、事务或触发器效果摘要。当前仍缺少跨应用、独立裁定的真实正负标签集。

## 停止条件与下一项讨论

本轮固定挑战到此结束，保留失败案例，不调参、改阈值、继续扩样或测试速度。旧 `risk_model.json` 和旧提取器均未修改。

下一步建议只验证一个通用方向：**把模块效果及其到达观察点的条件、先后顺序作为关系表达，形成最小的效果依赖摘要。** 它需要表示数据依赖、控制依赖、显式/隐式异常；未覆盖的效果应为 unknown。随后讨论这种摘要能否区分上述同特征异标签案例，再决定概率模型如何使用它。这样研究对象仍是模块能力依赖与删减条件，而非具体测试数据或数据库工程调优。本轮尚未实现该摘要。

## 复核入口

- [完整归档](../evidence/module-external/20261104-0a3d0ae02836/report.json)：`status=completed` 表示实验完成，不表示模型验证通过。
- [预测封存](../evidence/module-external/20261104-0a3d0ae02836/sealed_predictions.json)、[全部比较与反例](../evidence/module-external/20261104-0a3d0ae02836/results.json)。
- [真实源码审查](../evidence/module-external/20261104-0a3d0ae02836/real_review.json)、[输入冻结](../evidence/module-external/20261104-0a3d0ae02836/freeze.json)、[文件哈希](../evidence/module-external/20261104-0a3d0ae02836/manifest.json)。

脚本 `python3 -B scripts/validate_module_external.py` 可产生另一份独立归档；本轮只运行一次，未以重跑增加样本数。
