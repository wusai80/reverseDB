# 小型订单事务应用

这是运行在 PostgreSQL 上的命令行应用，建单、付款、订单查询、配送和库存检查五类事务均已有独立候选实现。输入使用明确的业务参数，执行顺序由 JSON 数组规定，时间由每条命令的 `clock_ms` 固定。四个终端串行执行，适合能力复现与差分验证。

当前支持单仓库、10 个地区、按客户 ID 操作。数据来自已冻结的 BenchBase 快照，业务行为以固定版本为参考。这不是完整 TPC-C 产品、认证负载或带权限的多人 Web 系统。

## 运行

在当前已准备依赖的工作区，需要本机 Docker Desktop 与 Java/Javac 23+。脚本使用现有 PostgreSQL 镜像、数据快照和分发包，不下载新依赖。每次从快照创建专用数据库，完成后清理容器、网络和数据卷，结果保存在输出路径。

```sh
cd /Users/wusai/Documents/code/reverseDB

# 运行参考实现，使用自带的新输入场景
python3 scripts/tpcc_app.py run

# 运行五类事务全部独立实现的候选版本
python3 scripts/tpcc_app.py run --engine candidate

# 自己编写命令文件；数组顺序就是执行顺序
python3 scripts/tpcc_app.py run --commands /absolute/path/commands.json --engine candidate

# 完整验收：四组场景、八个隔离数据库，分别运行参考与候选
python3 scripts/tpcc_app.py verify
```

输出目录内 `responses.json` 是业务结果，`jdbc-trace.json` 是诊断记录，`final-state.json` 是九张表的完整行摘要；`observations.json` 保存逐步写入状态和只读事务标记。整个运行的 `run.json` 保存结果、配置哈希、命令和清理状态。数据库是一次性实验实例；结果文件持续保留。

## 命令格式

文件根对象为 `{"version":1,"commands":[...]}`。每条命令包含唯一 `id`、`type`、`terminal`（1–4）、`warehouse`（当前固定 1）、`clock_ms`（非负毫秒时间戳）。每种类型另需以下字段：

| type | 业务字段 | 结果 |
|---|---|---|
| NewOrder | district、customer、lines；每行 item 和 quantity | 新订单及订单行 |
| Payment | district、customer_district、customer、amount | 客户余额及累计付款 |
| OrderStatus | district、customer | 最新订单及订单行 |
| Delivery | carrier | 各地区完成配送的订单 |
| StockLevel | district、threshold | 最近 20 单涉及的低库存商品数 |

`district` / `customer_district` 为 1–10；`customer` 为 1–3000。NewOrder 需要 5–15 行，item 为正整数，quantity 为 1–10；不存在的商品在事务中触发回滚。Payment 为 0.01–5000.00，至多两位小数；carrier 为 1–10，threshold 为 10–20。未知字段及错误类型被拒绝。

```json
{
  "version": 1,
  "commands": [{
    "id": "order-001",
    "type": "NewOrder",
    "terminal": 1,
    "warehouse": 1,
    "district": 1,
    "customer": 42,
    "clock_ms": 1793664000000,
    "lines": [
      {"item": 31, "quantity": 1},
      {"item": 32, "quantity": 2},
      {"item": 33, "quantity": 3},
      {"item": 34, "quantity": 4},
      {"item": 35, "quantity": 5}
    ]
  }]
}
```

业务结果有三种状态：`committed`（提交）、`aborted / INVALID_ITEM`（商品不存在，回滚）、`rejected / INVALID_INPUT`（输入校验未通过）。数据库故障或未知程序错误使本次运行失败，保留诊断证据；当前没有自动重试或跨运行幂等承诺。

## 实现与重构范围

- `src/com/oltpbenchmark/api/TpccApp.java`：只作为参考适配器，调用固定 BenchBase 过程。
- `src/reversedb/tpcc/CandidateApp.java`：独立候选入口，调用 NewOrder、Payment、OrderStatus、Delivery、StockLevel 五个服务。业务服务仅使用 JDK/JDBC；应用层依赖 JSON 库与 PostgreSQL 驱动。
- `AppRuntime` / `JdbcTrace` / `Observation`：共享命令校验、四连接串行调度、事务生命周期、业务投影及取证。两类只读查询由 PostgreSQL 强制只读；候选直接使用命令时间。
- 候选编译及实际执行不加载 BenchBase JAR。验收脚本仍复用既有分发包中的两项依赖、镜像和快照，并单独编译参考入口供对照。
- 金额计算和库存批处理保留当前参考实现的行为。重复商品行、库存回补和付款浮点规则均记录在报告中；后续优化或改变规则需另行约定并测试。


`scenarios/baseline.json` 来自已冻结的 JDBC 记录，来源与转换记录随文件保存：客户选择解析为 ID、付款浮点数还原为两位小数、无效商品哨兵改为不存在的正整数。数据库最终状态必须与原先种子执行的九表摘要一致。`scenarios/holdout.json` 使用未录制过的业务输入，覆盖新品组合、地区/客户、15 行订单、重复商品、回补、三个中止位置和四类输入拒绝。

新增 `transaction_edges.json` 覆盖 GC/BC 信用、重复及跨地区付款、500 字符备注截断、金额边界、最新订单与只读库存检查。`delivery_edges.json` 需显式搭配 `--fixture delivery_edges`，以单独的数据变体验证跳过空地区、全空队列和新单配送。参考/候选各执行 62 条命令，详见[五类事务验收](../../docs/TPCC_FULL_RECONSTRUCTION.md)。

## 监控与反编译

`python3 scripts/tpcc_app.py verify --observe` 会额外采集命令时间线、数据库事务标签、JDBC 耗时、JFR 和类加载日志，并与未监控基线比较业务结果。诊断时间不是性能基准。已有字节码反编译与绑定结果见[报告](../../docs/TPCC_OBSERVATION_DECOMPILATION.md)；反编译使用独立固定版本 Vineflower，未使用源码代替输出。

## REA 的实际作用

反编译输出也已实际重建运行：先执行 `python3 scripts/rebuild_tpcc.py`，再执行 `python3 scripts/tpcc_app.py verify --rebuilt <输出的bundle路径>`。此模式使用未修改反编译 Java 生成的业务 class，与冻结原版分别比对，并核验类加载来源；共享框架保留。[结果和边界](../../docs/TPCC_DECOMPILATION_ROUNDTRIP.md)。

REA 6.8.0 已分析参考 JAR，保留文件/类成员身份及 SHA256。此次工具没有恢复 Java 方法、SQL 或事务逻辑；本机 JEB 深度分析桥接不可用。因此当前五类事务是基于固定源码与真实执行证据进行的人工重构，REA 提供参考制品取证。没有安装额外引擎，也没有为适配工具添加 Web API。

详见[重构取证与验收报告](../../docs/REA_TPCC.md)。参考适配依据 Apache-2.0 BenchBase 源码，许可证保存在 [BENCHBASE-LICENSE](BENCHBASE-LICENSE)。
