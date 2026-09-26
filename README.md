# Carpool Fare Splitter · 拼车行程费用分摊 API

基于 FastAPI 的拼车费用分摊服务：按顺序路段读取整数分费用与每位乘客的上下车站点，
逐段均摊费用，输出逐人总额与逐路段明细；还可从最多 12 名候选乘客中**自动遴选**一组
上车（座位与预算双约束，先求入选人数最多、再求入选者应付总额最小），以及在提供每人
实际付款记录后，自动生成**转账笔数全局最少**的结清方案。金额全程使用整数分与精确分数计算，
总额严格守恒，且乘客与付款记录的输入顺序不影响结果。

## 快速启动

单条命令（构建镜像、安装依赖、启动服务全部自动完成）：

```bash
docker compose up --build
```

启动后访问：

| 地址 | 说明 |
| --- | --- |
| http://localhost:8000/split | 分摊接口（POST） |
| http://localhost:8000/admit | 候选乘客遴选 + 分摊接口（POST） |
| http://localhost:8000/settle | 分摊 + 结清转账接口（POST） |
| http://localhost:8000/docs | Swagger 交互式文档 |
| http://localhost:8000/healthz | 健康检查 |

后台运行可加 `-d`；停止用 `docker compose down`。

## 配置

无需任何必需配置（无数据库、无环境变量）。默认端口 **8000**；
如需修改宿主机端口，编辑 `compose.yaml` 中的端口映射即可，例如 `"8080:8000"`。

## API：`POST /split`

### 请求体

```json
{
  "segments": [100, 251, 80],
  "passengers": [
    {"id": "alice", "board": 0, "alight": 3},
    {"id": "bob",   "board": 1, "alight": 3},
    {"id": "carol", "board": 0, "alight": 1}
  ]
}
```

- `segments`：按行程顺序排列的各路段费用，整数，单位分，必须 ≥ 0，至少 1 段。
- `passengers[].id`：乘客 ID，字符串或整数，全程唯一。
- `board` / `alight`：上/下车站点编号。`N` 个路段对应站点 `0..N`；
  乘客在车区间为 `[board, alight)`，即乘坐第 `board` 至 `alight-1` 段，须满足 `0 ≤ board < alight ≤ N`。

### 响应（200）

```json
{
  "total_cost": 431,
  "totals": {"alice": 216, "bob": 165, "carol": 50},
  "segments": [
    {"index": 0, "cost": 100, "passengers": ["alice", "carol"], "shares": {"alice": 50, "carol": 50}},
    {"index": 1, "cost": 251, "passengers": ["alice", "bob"],   "shares": {"alice": 126, "bob": 125}},
    {"index": 2, "cost": 80,  "passengers": ["alice", "bob"],   "shares": {"alice": 40, "bob": 40}}
  ]
}
```

- `totals`：逐人应付总额（分），总和恒等于 `total_cost`。
- `segments[]`：逐路段明细，`passengers` 为该段在车乘客，`shares` 为该段分摊结果（每段 `shares` 之和恒等于该段 `cost`）。

### 示例

```bash
curl -s http://localhost:8000/split \
  -H 'Content-Type: application/json' \
  -d '{"segments":[100,251,80],"passengers":[{"id":"alice","board":0,"alight":3},{"id":"bob","board":1,"alight":3},{"id":"carol","board":0,"alight":1}]}'
```

## API：`POST /admit`

在既有行程（`segments` + 已在车的 `passengers`）基础上，从**最多 12 名候选乘客**中遴选一组加入。
每名候选乘客除 `id`、`board`、`alight` 外还给出 `max_fare`（整数分，愿意支付的上限）；
另有统一的每段座位上限 `capacity`。

一组候选入选当且仅当：

1. **座位约束**：每个路段上「既有乘客 + 入选候选」人数都不超过 `capacity`；
2. **预算约束**：将入选者并入完整名单后，按与 `/split` 完全相同的规则**重新分摊全程费用**，
   每名入选者的应付额不超过其 `max_fare`（既有乘客不受预算约束）。

遴选目标按以下顺序优化：

1. **入选人数最多**；
2. 人数相同时，**入选者应付总额最小**；
3. 仍相同时，取入选 ID 名单按规范顺序排序后字典序最小者
   （整数 ID 按数值升序、字符串 ID 按字典序升序，整数整体排在字符串前）。

### 请求体

```json
{
  "segments": [100, 251, 80],
  "passengers": [
    {"id": "alice", "board": 0, "alight": 3},
    {"id": "carol", "board": 0, "alight": 1}
  ],
  "capacity": 3,
  "candidates": [
    {"id": "bob", "board": 1, "alight": 3, "max_fare": 165},
    {"id": 7,     "board": 2, "alight": 3, "max_fare": 40}
  ]
}
```

- `capacity`：正整数，所有路段统一的座位人数上限；既有乘客人数在任何路段已超限即判 422。
- `candidates`：候选数组，**至多 12 名**（可为空数组）。候选 ID 不得彼此重复，
  也不得与既有乘客 ID 冲突；区间与站点规则同 `/split`。
- `max_fare`：非负整数分；约束针对**重新分摊后**的应付额，而非加入前的费用。
- 行程本身仍须满足 `/split` 的全部规则（如不得有既有乘客覆盖不到的空载收费路段）。

### 响应（200）

```json
{
  "admitted": [7, "bob"],
  "totals": {"7": 27, "alice": 202, "bob": 152, "carol": 50},
  "segments": [
    {"index": 0, "cost": 100, "passengers": ["alice", "carol"], "shares": {"alice": 50, "carol": 50}},
    {"index": 1, "cost": 251, "passengers": ["alice", "bob"],   "shares": {"alice": 126, "bob": 125}},
    {"index": 2, "cost": 80,  "passengers": [7, "alice", "bob"], "shares": {"7": 27, "alice": 26, "bob": 27}}
  ]
}
```

- `admitted`：入选候选 ID，按规范顺序排列（整数在前）；**无人可入选时为 `[]`**。
- `totals` / `segments`：入选后**完整名单**（既有 + 入选）的分摊结果，结构与 `/split` 完全一致。
- 无任何候选可以入选（座位或预算均不满足）时，`admitted` 为空，
  `totals`/`segments` 即**原行程的分摊结果**，与对同一行程调用 `/split` 一致。
- 无论 `passengers` 与 `candidates` 以什么顺序提交，响应完全一致。

### 示例

```bash
curl -s http://localhost:8000/admit \
  -H 'Content-Type: application/json' \
  -d '{"segments":[100,251,80],"passengers":[{"id":"alice","board":0,"alight":3},{"id":"carol","board":0,"alight":1}],"capacity":3,"candidates":[{"id":"bob","board":1,"alight":3,"max_fare":165},{"id":7,"board":2,"alight":3,"max_fare":40}]}'
```

## API：`POST /settle`

在 `/split` 的同一套 `segments`、`passengers` 基础上增加 `payments`：
每位乘客**恰好一条**已支付记录（整数分，允许 0，不允许负数）。
接口先算出每人应付额，再以 `余额 = 已付 − 应付` 生成欠款人到收款人的转账方案。

### 请求体

```json
{
  "segments": [100, 251, 80],
  "passengers": [
    {"id": "alice", "board": 0, "alight": 3},
    {"id": "bob",   "board": 1, "alight": 3},
    {"id": "carol", "board": 0, "alight": 1}
  ],
  "payments": [
    {"id": "alice", "amount": 331},
    {"id": "bob",   "amount": 100},
    {"id": "carol", "amount": 0}
  ]
}
```

- `payments[].id`：乘客 ID，必须与 `passengers` 一一对应：**不缺、不重、无多余**。
- `payments[].amount`：该乘客已付金额，整数分，`>= 0`。
- `payments` 金额之和必须**恰好等于**行程总额（`sum(segments)`）。

### 响应（200）

```json
{
  "owed": {"alice": 216, "bob": 165, "carol": 50},
  "paid": {"alice": 331, "bob": 100, "carol": 0},
  "balance": {"alice": 115, "bob": -65, "carol": -50},
  "transfers": [
    {"from": "bob", "to": "alice", "amount": 65},
    {"from": "carol", "to": "alice", "amount": 50}
  ]
}
```

- `owed`：每人应付额（与 `/split` 的 `totals` 完全一致）。
- `paid`：每人已付额（按请求 `payments` 汇总）。
- `balance`：`paid - owed`，正数为应收、负数为应付，总和恒为 0。
- `transfers`：结清全部余额的转账列表；`from` 为欠款人、`to` 为收款人。
  - 非零余额参与者**不超过 12 人**时，求解**转账笔数全局最少**的方案
    （零和子集划分 + 桥接割动态规划，精确枚举）；
  - 最少方案不唯一时，将 `(from, to, amount)` 序列按乘客 ID 规范顺序
    （整数 ID 按数值、字符串 ID 按字典序，整数排在字符串前）排序后取字典序最小者；
  - 因此无论 `passengers` 与 `payments` 以什么顺序提交，响应完全一致；
  - 超过 12 个非零余额时改用确定性贪心结算（至多 `n-1` 笔）。

### 示例

```bash
curl -s http://localhost:8000/settle \
  -H 'Content-Type: application/json' \
  -d '{"segments":[100,251,80],"passengers":[{"id":"alice","board":0,"alight":3},{"id":"bob","board":1,"alight":3},{"id":"carol","board":0,"alight":1}],"payments":[{"id":"alice","amount":331},{"id":"bob","amount":100},{"id":"carol","amount":0}]}'
```

## 分摊规则

1. 按路段顺序处理，每段找出全部在车乘客均摊：`base = cost // n`，余数 `r = cost % n`。
2. 每位乘客维护**累计理论份额**（精确分数）与**已分金额**；欠额 = 理论份额 − 已分金额。
3. 无法整除时，余数 `r` 个 1 分优先补给**欠额最大**的乘客；欠额并列时按乘客 ID 升序兜底
   （整数 ID 按数值、字符串 ID 按字典序），除此之外 ID 不影响分摊。
4. 因此：总额严格守恒（`sum(totals) == sum(segments)`），且乘客在请求中的排列顺序不影响结果。

## 错误（422）

以下情况返回 `422 Unprocessable Entity`，响应 `detail` 中含具体原因：

- 空载收费路段：某段 `cost > 0` 但无任何乘客在车（费用为 0 的空载段允许）；
- 非法区间：`board >= alight`、站点越界（`alight > 路段数`）或站点为负；
- 乘客 ID 重复、费用为负、路段列表为空、字段类型错误或含未知字段；
- （仅 `/admit`）候选超过 12 名、候选 ID 与既有乘客或其他候选重复；
- （仅 `/admit`）既有乘客在某路段的人数已超过 `capacity`；
- （仅 `/admit`）`capacity` 非正整数，或 `max_fare` 为负/非整数；
- （仅 `/settle`）`payments` 总和不等于行程总额；
- （仅 `/settle`）`payments` 缺失某位乘客、存在未知 ID 或同一 ID 重复；
- （仅 `/settle`）付款金额为负或非整数（如小数）。

## 本地开发与测试

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests/          # 全部测试（算法 + API）
python -m unittest tests.test_splitter   # 仅核心算法（纯标准库，无需安装依赖）
uvicorn app.main:app --reload    # 本地起服务，默认 http://127.0.0.1:8000
```

## 项目结构

```
app/
  main.py       # FastAPI 应用与路由（POST /split、POST /admit、POST /settle、GET /healthz）
  models.py     # 请求/响应模型与 422 校验规则
  splitter.py   # 核心算法：分摊（精确分数）+ 候选遴选（子集枚举）+ 最少转账结清（子集 DP）
tests/          # 算法单元测试 + API 集成测试
Dockerfile      # 依赖与启动命令均固化在镜像内
compose.yaml    # 单服务编排，含健康检查
requirements.txt / requirements-dev.txt
```
