# Paperless B0：隔离运行入口

2026-11-02 已完成首个 B0 小型基线：镜像构建、隔离测试数据库迁移、110 个上游测试及 1 个 SQL trace fixture 均通过。结果与当前问题见 [BASELINE_RESULTS](BASELINE_RESULTS.md)；完整数据矩阵与性能基准尚未完成。

## 当前环境检查

- 三个固定源码仓库和 39 个静态证据区域再次核验通过。
- 宿主默认 Python 3.10.9，另有 `/opt/homebrew/bin/python3.12`（3.12.13）。运行器仅用标准库，兼容默认解释器；应用使用上游 Dockerfile 已固定 digest 的 Python 3.14 / uv 0.13.5 基础镜像。
- 初始安装是 Intel 版 Docker Desktop 3.5.2，CLI 20.10.7、Compose v2.0.0-beta.6，应用启动报 `kLSNoExecutableErr`。2026-11-02 按用户要求原位升级为官方 Apple Silicon 版 Docker Desktop 4.97.0（244584）。
- 当前 Engine/CLI 29.9.2、Compose v5.6.1；`desktop-linux` context 指向用户目录下的 Unix socket。Desktop 状态为 running，官方 hello-world 容器已成功执行并自动删除。运行器仍自行轮询健康状态，不依赖 Compose 的 `--wait`。
- 全部环境预检通过，详情见 [runtime-preflight.json](../evidence/runtime-preflight.json) 和 [升级记录](../evidence/docker-upgrade.json)。Paperless 镜像、测试与 trace 已实际运行并保存[证据](../evidence/paperless-b0/20261102-224105-1d6b9197b6f6/summary.json)。

## 运行顺序

需要一个可用的本地 Docker Linux daemon；当前已恢复。运行器显式使用所选 context，拒绝远程 Docker endpoint。若重启后 daemon 未就绪，先打开 Docker Desktop，再运行预检。

```sh
cd /Users/wusai/Documents/code/reverseDB
python3 scripts/paperless_baseline.py doctor --write
python3 scripts/paperless_baseline.py lock-images
python3 scripts/paperless_baseline.py build
python3 scripts/paperless_baseline.py test
```

逐条执行，前一条成功后再执行下一条。预检失败时退出码为 2，不创建容器；其他失败返回非零。`lock-images` 和 `build` 会下载完整上游依赖，包括 OCR/ML 依赖，首次执行可能耗时较长。

- `lock-images` 从 [images.json](../prototype/paperless/images.json) 拉取镜像，将真实 repository digest、Linux 架构、image ID 和实验文件哈希写入 `work/paperless/images.lock.json`。PostgreSQL 18 和 Valkey 9 的选择来自固定上游 Compose 配置；没有填造 digest。
- `build` 按上游 `uv.lock` 执行 `uv sync --frozen --no-default-groups --group testing --extra postgres`，保持完整应用依赖。将本地应用 image ID 写入 `work/paperless/build.lock.json`，不靠可变 tag 运行测试。Python/uv 版本及 Python/系统包清单保存在镜像内，运行时复制到结果目录。
- `test` 在每次新生成的 `reversedb-b0-<随机后缀>` Compose 项目中运行 PostgreSQL、Valkey 和测试容器。先执行两个上游测试文件，再执行 B0 smoke trace。pytest-django 创建并迁移本轮测试数据库；不会执行宿主机数据库迁移。
- 应用先把结果写入当轮 Docker results volume；运行器使用 `docker cp` 复制到 `work/paperless/runs/<时间>-<后缀>/`：`run.json`、展开后的 `config.log`、服务健康记录、完整执行日志、上游与 trace JUnit XML、包清单及 `b0-trace.json`。失败保留部分结果和退出码；复制失败或缺少 trace 不会判为成功。
- 成功或失败都会先尝试复制结果，然后精确删除**本轮一次性应用容器**，最后删除本轮项目的服务、网络及数据库/结果卷；不能只靠 `compose down` 回收一次性容器。如果清理失败，结果记录项目名并返回失败。强制结束进程/关闭机器可能绕过清理，需要根据该轮 `run.json` 检查残留。

已有 lock/build 文件会被校验和保留。若要修改实验输入，先将 `work/paperless/` 归档到另一个本地目录，再重新运行锁定与构建；不要同时启动多个锁定/构建进程。基础镜像和 Python 依赖已固定，apt 安装来自构建时仓库，因此不声称不同日期的重建字节一致；复现实验还需保留实际应用镜像及系统包清单。

## 隔离与采集边界

[compose.yaml](../prototype/paperless/compose.yaml) 不发布宿主端口、不使用宿主网络、不挂载宿主目录或已有数据库。数据库卷和结果卷均使用当次项目名，应用数据/媒体目录使用容器 tmpfs；结果通过 Docker API 复制回宿主。运行网络设为 internal；构建阶段需要下载依赖。

首次 smoke fixture 使用三个标签（父、子、零计数）和一个合成文档，检查原 helper 的完整 `(tag_id, document_count)` 映射，断言真实 SQL 存在 GROUP BY 和 CASE，并采集 v9/v10 标签 API 的分页响应。它使用 Django APIClient，**不是网络 HTTP benchmark**。两份上游测试补充权限和标签树覆盖；这仍不等于 PROTOTYPE 中完整的差分矩阵。

SQL wrapper 保存 SQL 模板、参数类型、模板字节数、执行耗时、调用栈位置、事务状态和阶段；不保存参数值或异常消息，不消费 `executemany` 迭代器。这里的执行耗时不含取回结果的时间，完整请求计时包含 trace 成本，不用于性能结论。参数总字节数、返回行数、EXPLAIN 与无 trace 的性能采样尚未实现。

fixture 在 pytest 事务和无并发写入的独立测试库中运行；每个版本采一次清理 Django cache 后的响应和一次重复响应。缓存标签只表明执行了 Django cache 清理，不能推断第三方树/权限缓存全部处于冷态。当前只区分 helper 构建、helper 求值和完整 API 请求，细分 API 内部五个阶段仍是后续工作。

## 本地工具检查

```sh
python3 -m unittest discover -s tests -v
python3 scripts/checkout_sources.py --verify-only
python3 scripts/inspect_regions.py
```

当前 17 个工具检查通过，覆盖锁文件拒绝条件、远程 Docker 拒绝、故障后的作用域清理、一次性容器回收顺序、服务崩溃检测、Compose 隔离配置、缺失/部分复制结果的失败判定，以及 SQL wrapper 的返回值/异常/迭代器语义。Compose 解析测试使用明确标记的假 digest，仅验证配置结构，不拉取或运行镜像。工具测试不计为 Paperless 测试通过。

下一验收点：扩大 B0 的规模、授权与树结构覆盖，采集 EXPLAIN 和无 trace 计时；再验证重复计数的 B1 常规优化与 B2 SQL 方案。
