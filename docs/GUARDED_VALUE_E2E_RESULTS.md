# 条件化值关系：已有值、替代生产者与错误值验证

2026-11-06。五个版本各运行同一组70条请求，共350条全部完成。正常融合、已有值及替代生产者三个候选与原版的完整返回、逐请求观察和九表最终状态一致；错误值候选的返回仍完全一致，但两个场景组的customer数据不同。

本轮验证的是特征的适用边界，不是增加安全规则或重新拟合概率。默认应用与生成器未改动，所有候选仅用于隔离实验。

## 固定设计

复用此前五组协议：baseline 11、holdout 17、transaction_edges 27、delivery_edges 7、time_boundaries 8。固定JSON请求顺序、时钟、四个串行执行的终端连接和原始数据库转储；每个版本、每组场景独立恢复完整数据，基础转储九表共599,051行。已有场景名holdout不意味着本轮是独立盲测，这些输入此前已用于开发诊断。

候选及特征在数据库执行前冻结。每个候选单独编译Java类目录、安装配套SQL，并检查实际加载的适配器、辅助类及安装的函数体。NewOrder融合保持不变，只改Payment的值生产与消费关系：

| 版本 | Java值来源与SQL消费 | 预先提取的条件关系 | 实测完整观察 |
| --- | --- | --- | --- |
| original | 原实现 | 参照，不评分 | 参照 |
| full | BC分支计算新c_data，SQL仅BC分支使用 | 已提取 | 一致 |
| old_value | 先读取已有c_data；BC仍用原方法计算新值；SQL两分支均写入 | unknown：多个选定字段赋值 | 一致 |
| alternative_producer | BC用原方法，GC用另一分支读取已有c_data；SQL两分支均写入 | unknown：条件赋值不唯一 | 一致 |
| aligned_empty | 保留BC赋值与消费条件，但赋值内容改为空串 | 与full的四项数值特征相同 | 不一致，仅完整状态检查检出 |

两种合法对照中，GC读取并回写原值，BC保留原Java金额、字符串前缀及截断语义。它们不是性能优化；额外读取仅用于验证“选定的新值生产者没有执行”并不意味着消费必然错误。原始数据未裁剪；空串版本是有意构造的错误改写负控。

## 实测结果

- 每版56条提交、5条业务中止、9条入口拒绝；无进程失败或未执行后缀。
- 每版已提交Payment包含GC 8条、BC 13条。错误值版在不含BC的baseline、holdout以及无Payment的delivery_edges中没有差异，在另外两组产生差异。
- full、old_value、alternative_producer在全部五组的完整响应、逐请求观察、初始及最终九表行数／完整行哈希均一致。
- aligned_empty的70条完整响应仍与原版一致；transaction_edges中12条观察不同，time_boundaries中1条观察不同；两组最终仅customer表哈希不同。所有版本仍满足原有五项业务不变量，说明这些不变量也不足以覆盖文本数据语义。
- 具体见证：transaction_edges的edge-01，原版写入的c_data长438字符，以`5 1 1 1 1 0.01 | `开头，错误候选写入空串。Payment结果不返回这个字段，因此接口返回比较无法发现该错误。
- 提交／回滚轨迹、实际辅助类加载、函数体及预定JDBC调用数检查通过；9项时间追踪器检查通过。已有12项条件关系静态检查也在启动前通过，提取器未修改。
- 保留SERIALIZABLE、WAL、fsync、同步提交和全页写入；没有性能测量或数据库内核删减。915个归档文件的SHA-256逐一核验，临时容器和卷已清理并额外只读检查为空。

## 对方法的含义

第一，unknown是表示尚未覆盖该结构。本轮两个unknown候选都通过固定协议，不能把unknown当作“有错”、零风险或可删。现有提取器只描述一个选定赋值，尚不能表示已有定义与多个互斥生产者。

第二，条件对齐不等于值正确。令φ为本轮四项条件关系特征，得到：

`φ(full) = φ(aligned_empty) = (0, 0, 0.5, 0.5)`，但批次差异标签分别为0和1。

任何只接收φ的确定性评分函数都会给这两版相同分数；同输入的概率模型也无法从这组输入中区分二者。继续调这四项特征的权重不能补足缺失的信息，需要能表示值来源或变换差异的额外证据。这不代表所有现有特征都会碰撞，也不是通用模型已校准的证据。

从反编译后的依赖分析看，条件关系可作为学习输入，但它既不概括全部可达定义，也不保证定义的值满足业务语义。只观察接口返回或SQL非空约束，会遗漏本次持久化数据损坏；保留完整数据比较仍是必要的验证边界。

本轮到此结束，不扩展解析器、不在同一批次调参、不自动删除模块。四个候选标签仅表示本协议是否发现差异；单项目、已知机制、相关变体不构成独立泛化校准数据。正常完成和固定串行请求下的实测一致，也不证明并发、任意触发器、故障或全输入等价。

## 复现与证据

- [候选构造](../prototype/module_pruning/guarded_value_variants.py)、[测试入口](../scripts/test_guarded_value_e2e.py)：`python3 -B scripts/test_guarded_value_e2e.py`，依赖本地冻结JAR、完整转储、JDK和锁定Docker镜像。
- [执行前特征](../evidence/guarded-value-e2e/20261106-2b184bc5dc73/preexecution-features.json)、[冻结协议](../evidence/guarded-value-e2e/20261106-2b184bc5dc73/protocol.json)。
- [执行报告](../evidence/guarded-value-e2e/20261106-2b184bc5dc73/run.json)、[候选标签](../evidence/guarded-value-e2e/20261106-2b184bc5dc73/candidate-labels.json)、[哈希清单](../evidence/guarded-value-e2e/20261106-2b184bc5dc73/manifest.json)。
- [错误值版差异](../evidence/guarded-value-e2e/20261106-2b184bc5dc73/transaction_edges_aligned_empty/comparison.json)、[配对候选清单](../prototype/module_pruning/guarded_value_e2e_cohort.json)。
