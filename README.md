# 青岔河分栏观测日仓

站点和质控组各自报送，档案员冻结逐行登记。读数和质控可分路送达，值班员使用异地仓的当前日账交班，下一班按原账号取得当时的明细、汇总和两栏出处。

需要 Python 3.11 标准库。运行完整回归和样例。

```sh
python3 -B check_project.py
python3 -B -m cli demo
```

`check_project.py` 是项目物化时提供的核验入口。演示默认把数据库放在自动清理的临时目录。用 `python3 -B -m cli demo --directory demo-data` 可指定一个空目录保存演示库。

## 一天的交接

1. 站点登记 A 的 7 L 和 B 的 11 L，各有站点原始引用。
2. 质控组登记两行合格，各有独立的判定引用。初次冻结前两栏均须登记。
3. 档案员冻结首版，分栏出件员各取本栏投影并具名发行。
4. 异地仓收齐首版两栏后出版 18 L 原账，共两条实测。
5. 白班登记具名勘误。A 改为 13 L、待复核，B 只改读数为 17 L。档案员冻结其更正范围及父版。
6. 读数件先到。A 保留 7 L、合格，B 带原质控出处先推进，当前账为 24 L。
7. 源端关闭后，下一班重开异地仓，收取在途的质控件并出版 30 L。
8. 次班查询当前账并按首发账号复领 18 L 原账。原件可重投，独立编号的旧版件也可实收。

## Python 入口

- `source.pending.open_source(path)` 打开源库。`source.observations.record(db, site, day, row, column, value, origin, *, correction=None)` 登记并返回含 `entry_id` 的登记。栏别为 `meter` 或 `quality`，日期为 `YYYY-MM-DD`。行键限定于站日。读数用整数升，`None` 为明确缺测，布尔值非法。质控接受 `合格` 或 `待复核`。出处是本栏原系统引用。具名勘误须有同站日原行，勘误号在源库内归属同一站日。
- `freeze.days.freeze_day(db, site, day, entry_ids=None)` 冻结，返回含日版号及逐行范围的 `FrozenDay`。默认选择该站日全部待冻结登记，也可依登记号选择子集。每行每栏一次冻结最多采用一条登记。无效范围不分配版次或归档登记。冻结顺序由源库的日版号确定，行修订身份为站日、行键与日版号。`freeze.history` 可读取指定的日版、行版及改栏正文。
- `freeze.projection.project_frozen_column(db, version, column)` 产生仅有本栏正文和共有行版范围的投影。各出件端用 `issue.register.open_issuer(path, column)` 开独立的库，调用 `issue_column(issuer, version, column, item_id, projection=projection)` 发行文本栏件。原号绑定发行请求，同参重领可省略投影。出件号在交给同一仓的材料中应唯一。仓库按站日接收其登记源的冻结序列。
- `intake.store.open_warehouse(path)` 打开异地仓。`intake.register.admit(db, raw)` 实收 JSON 文本并返回整数实收号。相同业务内容的重投复用首个实收号和其原文。坏件或同号异件抛出 `ValueError`。`intake.materials.load_original(db, receipt_id)` 可查首收原文。
- `ledger.publish.publish(db, site, day, *, before_commit=None)` 扫描本地原件并返回 `PublicationResult`，含 `view` 和按行、按版的 `pending`。较新目标的依据或正文欠缺时，保留旧行或等待首发。一个行键可推进到已齐备的较新候选，晚到旧件不回退。无变化时复用当前账号。可选的提交前回调用于故障演练，在写入后与提交之间执行，回调自身不应开始或结束事务。回调或写入失败向调用者传播，出版事务回滚。
- `ledger.readback.read(db, *, site=None, day=None, account=None)` 按站日或按账号读取账。结果含稳定行键顺序、目标行版、每栏正文版次及出处、实测数和合计。空当前账的账号为 `None`，计数和合计均为零。未知历史账号抛出 `LookupError`。历史读取脱离源库和后续出版。

## 核账和持久边界

计量使用该账中实际已出版的行。每条整数读数贡献一条实测及其原整数升数，0 L 也算一条。缺测仍有明细，既不加计数也不加合计。待复核状态仍按同一口径计量。无需取整。样例输入在 `samples/`，测试用独立小例预期直接比较正常入口的输出。

源库在一个事务内归档选定登记、日版和行版。发行原件、仓库实收分别持久化。出版在一个仓库事务内账存行版、汇总、固定账号和当前引用。关闭源库后，异地仓依赖本地实收即可出版。重开不会重装样例或覆盖用户已有状态。调用者负责关闭打开的 SQLite 连接。
