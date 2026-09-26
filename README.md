# Carpool Fare Splitter · 拼车行程费用分摊 API

基于 FastAPI 的拼车费用分摊服务：按顺序路段读取整数分费用与每位乘客的上下车站点，
逐段均摊费用，输出逐人总额与逐路段明细；还可在提供每人实际付款记录后，自动生成
**转账笔数全局最少**的结清方案。金额全程使用整数分与精确分数计算，
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
| http://localhost:8000/admit | 候选乘客准入 + 重新分摊接口（POST） |
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

在 `/split` 的同一套 `segments`、`passengers`（既有乘客）基础上，给出统一座位上限
`capacity` 与**至多 12 名**候选乘客 `candidates`（站点规则与既有乘客相同，另带
`max_fare` 愿付上限，整数分、允许 0）。接口从候选中选出一组合法子集并重新分摊：

- **座位约束**：每个路段上「既有乘客 + 入选者」人数不超过 `capacity`；
- **票价约束**：按 `/split` 的完整规则对**合并后的完整名单**重新分摊后，
  每位入选者的应付总额不超过其 `max_fare`（既有乘客没有票价上限）。

择优顺序为字典序的三级目标：

1. **入选人数最多**；
2. 人数并列时，**入选者应付总额最小**；
3. 仍并列时，取入选 ID 按**规范顺序**（整数 ID 按数值在前、字符串 ID 按字典序在后，
   各自升序）排序后字典序最小的名单。

候选至多 12 名，因此子集至多 `2^12 = 4096` 个，采用精确枚举保证全局最优；
座位约束用位掩码快速预检，只有通过的子集才做完整重分摊。
**没有任何候选可入选时（包括候选列表为空），直接返回原行程的分摊结果**，
`admitted` 为空列表。

### 请求体

```json
{
  "segments": [100, 100],
  "passengers": [
    {"id": "alice", "board": 0, "alight": 2}
  ],
  "capacity": 2,
  "candidates": [
    {"id": "dave", "board": 0, "alight": 2, "max_fare": 100},
    {"id": "erin", "board": 0, "alight": 1, "max_fare": 60}
  ]
}
```

- `capacity`：每段统一座位上限，正整数；**既有乘客本身在任何路段都不得超员**
  （否则 422）。
- `candidates`：候选乘客数组，0–12 个；`id` 彼此唯一且不得与既有乘客重名
  （整数与字符串按字符串形式判重），`board`/`alight` 规则同既有乘客。
- `candidates[].max_fare`：该候选愿付的应付总额上限，整数分，`>= 0`。

### 响应（200）

```json
{
  "total_cost": 200,
  "admitted": ["erin"],
  "totals": {"alice": 150, "erin": 50},
  "segments": [
    {"index": 0, "cost": 100, "passengers": ["alice", "erin"], "shares": {"alice": 50, "erin": 50}},
    {"index": 1, "cost": 100, "passengers": ["alice"],          "shares": {"alice": 100}}
  ]
}
```

- `admitted`：入选候选 ID，按规范顺序排列；无人入选时为 `[]`。
- `totals`：**合并名单**（既有乘客 + 入选者）逐人应付额，总和恒等于 `total_cost`。
- `segments[]`：合并名单下的逐路段明细，格式同 `/split`。

上例中两人同乘会在第 0 段超员（3 人 > 容量 2）；dave 独乘全程应付 100、
erin 只乘第 0 段应付 50，故人数同为 1 时选择应付总额更小的 erin。

### 示例

```bash
curl -s http://localhost:8000/admit \
  -H 'Content-Type: application/json' \
  -d '{"segments":[100,100],"passengers":[{"id":"alice","board":0,"alight":2}],"capacity":2,"candidates":[{"id":"dave","board":0,"alight":2,"max_fare":100},{"id":"erin","board":0,"alight":1,"max_fare":60}]}'
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
- （仅 `/admit`）`capacity` 非正整数，或既有乘客在某路段已超员；
- （仅 `/admit`）候选超过 12 名、候选 ID 彼此重复或与既有乘客重名、
  候选站点非法/越界、`max_fare` 为负或非整数；
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
  splitter.py   # 核心算法：分摊（精确分数）、候选准入（子集枚举）、最少转账结清（子集 DP）
tests/          # 算法单元测试 + API 集成测试
Dockerfile      # 依赖与启动命令均固化在镜像内
compose.yaml    # 单服务编排，含健康检查
requirements.txt / requirements-dev.txt
```
