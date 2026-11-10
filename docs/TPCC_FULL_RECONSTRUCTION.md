# 五类事务独立实现与差分验收

2026-11-03：预定的 Payment、OrderStatus、Delivery、StockLevel 已替换为独立实现，加上先前的 NewOrder，当前小应用的五类业务事务均由候选自身执行。候选运行时只加载自己的类、JSON 库和 PostgreSQL 驱动；没有 BenchBase JAR，也不加载参考适配器。

这是单仓库、按客户 ID、固定时钟和串行调度下的能力复现。不是完整 TPC-C 合规证明，也没有开始数据库性能优化。

## 实现与独立性

- `PaymentService`：仓库/地区累计收款、客户余额及累计付款、GC/BC 信用分支、BC 备注前缀和 500 字符截断、支付历史。
- `OrderStatusService`：按客户 ID 获取最新订单，返回订单及有序订单行。
- `DeliveryService`：逐地区选择最早待配送订单、更新承运人和配送时间、累加客户余额与配送次数；跳过空地区。
- `StockLevelService`：最近 20 个订单中满足库存阈值的不同商品数。
- `AppRuntime`：共享命令契约、事务边界及状态取证。两类查询由 PostgreSQL 的只读事务保护。候选接收固定时间，不依赖测试代理替换时间。
- `TpccApp` 仅作为参考入口；`CandidateApp` 为独立入口。如果候选进程能加载 BenchBase 的 `Procedure` 类，会主动失败，防止混用参考实现。

五个业务服务使用 JDK/JDBC。应用层另用既有 `json-20240303.jar` 与 `postgresql-42.7.4.jar`。实际编译和启动命令保留在运行记录中；候选 classpath 没有通配依赖目录或参考实现路径。输入校验和观察代码是共享基础设施，业务过程是分开的。

## 实际验收

最终运行 `b7157edda001`，八个新数据库分别从冻结快照恢复，执行四组参考/候选对照：

| 场景 | 每个实现的命令数 | 提交 | 业务中止 | 输入拒绝 |
|---|---:|---:|---:|---:|
| 原始业务基线 | 11 | 10 | 1 | 0 |
| 新输入与 NewOrder 边界 | 17 | 10 | 3 | 4 |
| 付款、查询、配送边界 | 27 | 23 | 0 | 4 |
| 部分/空配送队列 | 7 | 7 | 0 | 0 |
| 合计 | 62 | 50 | 4 | 8 |

全部业务响应一致。NewOrder、Payment、Delivery 的逐步写入观察，以及九表初始/最终完整行摘要、五项不变量均一致。基线仍与原种子回放的九表状态相等。实际 PostgreSQL 日志共有 26 次 `BEGIN READ ONLY`，对应两个实现合计 26 次 OrderStatus/StockLevel 查询。

新覆盖证据：

- 固定快照中地区 1 的客户 5 为 BC、客户 1 为 GC。BC 客户连续付款 12 次，包含 0.01、5000、17.29 和交替的付款地区 1/10。备注长度依次为 438、458、476、494，随后达到并保持 500；每笔均新增一条 history。
- GC 客户重复付款覆盖最低/最高金额和普通金额；超过两位小数、零金额等输入被拒绝。
- 新建订单后查询最新订单；阈值 10/20 的库存查询；连续配送与查询已配送订单。
- 配送分支使用**明确标记的数据变体**：仅保留地区 1 的 2101、2102 两个待配送订单。连续三次配送分别返回 1、1、0 单；随后在地区 10 建单，再配送返回 1 单，订单查询确认承运人为 7。变体 SQL 原样应用于参考/候选，保存配置及哈希，不声称它是标准装载状态。
- 首轮集成 `05bc7a80fd3a` 与最终扩展运行均保留。54 项 Python 本地检查通过，包含对付款备注、配送客户余额、最新订单、库存计数和逐步观察缺失的负例；Java 输入契约另通过 1 个有效输入和 11 个无效输入。

所有专用容器、数据卷、网络已清理。没有访问其他业务数据库或远程服务器。

[运行记录](../evidence/tpcc-full/20261103-b7157edda001/run/run.json) · [逐步状态示例](../evidence/tpcc-full/20261103-b7157edda001/run/transaction_edges_candidate/observations.json) · [本地检查](../evidence/tpcc-full/20261103-b7157edda001/local-tests.log) · [证据清单](../evidence/tpcc-full/20261103-b7157edda001/manifest.json)

## 已知语义与未覆盖范围

付款和配送沿用参考实现的浮点转换及累计方式；直接换成精确小数可能改变历史结果。这一轮没有调整金额契约。NewOrder 重复商品的批量更新行为也保持此前已记录的结果。若要改变这些规则，应先用业务需求决定预期，再建立新的验收基线。

当前未覆盖多仓库、按姓氏选客户、并发冲突/死锁/自动重试、任意 SQL 故障注入、持久服务或权限界面。状态相等证明的是这些受控用例，不是所有输入和调度的程序等价性。

REA 的角色仍为上一轮实际完成的 JAR 制品取证，见 [REA 报告](REA_TPCC.md)。本轮复用同一制品身份，没有重复执行无收益的归档检查，也没有声称工具自动恢复了 JVM 业务逻辑；实现依据固定源码和真实差分证据完成。

## 运行

```sh
cd /Users/wusai/Documents/code/reverseDB
python3 scripts/tpcc_app.py verify
python3 scripts/tpcc_app.py run --engine candidate
python3 scripts/tpcc_app.py run --engine candidate \
  --commands app/tpcc/scenarios/transaction_edges.json
python3 scripts/tpcc_app.py run --engine candidate --fixture delivery_edges \
  --commands app/tpcc/scenarios/delivery_edges.json
```

`--fixture delivery_edges` 显式选择配送分支数据变体；普通运行使用原快照。更完整的命令契约见[应用说明](../app/tpcc/README.md)。
