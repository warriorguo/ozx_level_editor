# Level 引用数据参考（encounter / enemy / loot / item / tilemap）

- 面向读者：OZX Level Studio 的实现者
- 数据与代码快照：`ozx_base` `main@d867b644`，2026-09-11
- 增量复核：§1.6 / §2.4 与相关条目核对于 `main@f0a78923`（2026-09-29），对应 OZX-1032 / OZX-1041 / OZX-1044 / OZX-1047 / OZX-1063；其余仍是 `d867b644` 快照的实测数字。
- 姊妹篇：`level_data_reference.md`（`GameData/levels/` 本身）。本文覆盖 level 数据**指向外部**的五个数据族，是引用选择器、跳转、校验和 Effective 视图的依据。

本文所有分布数字都是在上述快照上实测的，不是估计。

---

## 0. 引用图

编辑器的引用图至少要覆盖下面这些边。**每条边都带类型**：`encounterId` 指向 `EncounterData`，不是「某个 id」——一个只有 `id -> 引用者` 的字典会把不同 dataType 混在一起。

### 从 level 出发

| 源字段 | 目标 | 必需性 |
|---|---|---|
| `RoomNodeData.encounterId` | EncounterData | 可选（但没有它房间不刷怪） |
| ~~`RoomNodeData.lootPlanId`~~ | —— | **OZX-1381 删除**（房间清场通道没了，写了被静默丢弃 → `LEVEL_FIELD_REMOVED`） |
| `RoomNodeData.guaranteedDrops[].itemId` / `.tableId`（OZX-1380） | ItemData / LootTableData（必须 `pickOne`） | 房间欠的奖品，见 §1.6 |
| `RoomNodeData.bossId` | EnemyData | 可选（**不驱动刷怪**） |
| `RoomNodeData.templateId` | Tilemap id = 相对 TilemapData 的路径去扩展名，如 `normal/all_start_1_01`（OZX-1571，`TilemapDocument.IdOf`，核对于 `ozx_base@85fa6bf1`；不保留裸文件名） | 可选（不填则走匹配） |
| `RoomNodeData.requiredForExitItemIds[]` | ItemData | 可选 |
| `RoomNodeData.appearance.*` | ResourcesDB key（非 GameData） | 可选 |
| `staticPlacements[].lootTableId` | LootTableData | `cargo` 用 |
| `staticPlacements[].mountedEnemyId` | EnemyData | `cargo` 用 |
| `staticPlacements[].itemId` | ItemData | `equipment` 必需 |
| `staticPlacements[].skillId` | SkillData | `skill` 必需 |
| `staticPlacements[].prefabKey` / `decorations[].prefabKey/spriteKey/fxKey` | ResourcesDB key | 视 kind |
| `staticPlacements[].triggerId` | 与 encounter `touch` 条件的 `triggerId` 闭合 | `trigger` 必需 |
| `doors[].keyId` | ItemData | 锁门用 |
| `caveLinks[].encounterId` / `stairLinks[].encounterId` | EncounterData | 可选 |
| `caveLinks[].tentaclePlacement.allowedTypes[]` | EnemyData | 可选 |
| `FloorData.themeId` | RoomThemeConfig（ScriptableObject，非 GameData） | 需要 |
| `FloorPlanData.enemyPool[].enemyId` | EnemyData | plan 必需 |
| `FloorPlanData.enemyPool[].dropTableId` | LootTableData | 可选（可能被楼层表覆盖） |
| `FloorPlanData.bossId` | EnemyData | 可选 |
| `FloorPlanData.transitionKeyId` | ItemData | 可选 |
| `FloorPlanData.stageTypes[].encounterId` | EncounterData | 可选（`lootPlanId` 已随清场通道删除，OZX-1381） |
| `floorLootPlan.probabilistic[].itemId` / `guaranteed[].itemId` / `stageMustDrop[].drops[].itemId` | ItemData | 必需 |
| `floorLootPlan.probabilistic[].tableId` / `guaranteed[].tableId` / `stageMustDrop[].drops[].tableId`（OZX-1109） | LootTableData（`pickOne`） | 与 itemId 二选一 |
| `floorLootPlan.guaranteed[].boundEnemyId` | EnemyData | 三选一 |
| ~~`floorLootPlan.guaranteed[].boundRoomId`~~ | —— | **OZX-1380 删除**，绑定只剩 `boundEnemyId` / `boundStage` 二选一 |
| `floorLootPlan.categoryCaps[].category` | ItemTaxonomy 节点 | 必需 |
| `floorLootPlan.stageWeights[].category` | ItemTaxonomy 节点 | 可选；留空 = 该 stage 的兜底倍率 |
| `caves[]/stairs[].encounterId` | EncounterData | 可选（`caves[].lootPlanId` 已删，OZX-1381） |

### 数据族之间

| 源 | 目标 |
|---|---|
| `EncounterAction.enemyId` / `condition.enemyId`（`killed`） | EnemyData |
| `EncounterAction.viaSpawner` | EnemyData（**必须是房间里活着的那种敌人**，见 §1.4） |
| `EncounterAction.railTrainId` / `EnemyPoolEntryData.railTrainId`（OZX-1309 / OZX-1315） | RailTrainData（`GameData/railenemy/`，`cars` 车头在前） |
| `EncounterAction.railOilTankId`（OZX-1317） | OilTankData（`GameData/oiltank/`，`railConfig.enabled` 为真的那种，出货 `rail_oil_tank`） |
| `EncounterAction.dropTableId` | LootTableData |
| ~~`EncounterAction.guaranteedDrop`~~ | **OZX-1380 删除**，奖品挪到房间的 `guaranteedDrops`（`ENCOUNTER_PIN_REMOVED`） |
| `RoomNodeData.guaranteedCargo[].itemId` / `.tableId` | ItemData / LootTableData（`[NonSerialized]`） |
| `LootEntryData.tableId` | LootTableData（必须 `pickOne`） |
| `EncounterCondition.triggerId`（`touch`） | 房间里 `kind="trigger"` 的 placement |
| `EnemyData.dropTableId` | LootTableData |
| `EnemyData.deathSpawnEnemyId` / `hatchEnemyId` / `hatchPool[]` / `spawnConfig.enemyIds[]` | EnemyData |
| `EnemyData.projectileId` / `beamId` / `skills[]` | ProjectileData / BeamData / SkillData |
| `LootEntryData.itemId` | ItemData |
| `ItemTaxonomyData.nodes[].items[]` | ItemData |

**当前快照的引用完整性是干净的**：encounter→enemy、encounter→loot、enemy→loot、loot→item、viaSpawner→enemy 全部可解析，无悬空。这就是 Phase 0 要守住的基线。

---

## 1. EncounterData（87 份，`GameData/encounters/`）

### 1.1 结构

```json
{ "dataType": "EncounterData", "id": "spawn_ch1_teach1_01", "maxAlive": 3,
  "steps": [ { "id": "w1", "condition": {...}, "action": {...} } ] }
```

| 字段 | 说明 |
|---|---|
| `maxAlive` | 同时存活上限，0 = 不限。**87/87 都设了** |
| `steps[]` | 顺序脚本，由 playhead 逐条执行（`SequentialEncounterRuntime`） |

一个 step = 「等条件 → 执行动作 → 前进」。`condition` 缺席 = 进入即执行；`action` 缺席 = 纯等待。`time` / `killed` 的计时和计数都是**相对于 playhead 进入该 step 的时刻**，不是编码全局时间。没有循环。

### 1.2 EncounterCondition（叶子，无布尔树）

| `kind` | 附带字段 | 实测用量 |
|---|---|---|
| （省略） | — | 178 |
| `time` | `seconds` | 25 |
| `cleared` | — | 4 |
| `touch` | `triggerId`（必填） | 0 |
| `killed` | `enemyId`（必填）、`count`（默认 1） | 0 |
| `custom` | `key`（必填） | 0 |

OZX-947 之前这里是 `op` + `children` 的布尔树。`JsonUtility` 无法表达自引用数组（会强行展开十层幻影节点），所以条件被拍平成叶子。**不要重新引入嵌套条件。**

### 1.3 EncounterAction

| 字段 | 说明 | 实测用量 |
|---|---|---|
| `verb` | `launch`，或 `sound`（OZX-1173：只读 `soundId`，其余 launch 字段都不适用，`EncounterValidator` 校验完 `soundId` 非空就 return）；缺席 = 纯等待 | 205 launch / 2 缺席 |
| `enemyId` | 必填 | 205 |
| `min` / `max` | 数量区间，闭区间随机 | 最常见是 min==max |
| `eliteCount` | 前 N 只以 elite 出生 | 11 step |
| `dropTableId` | 这批敌人摇的表（替换 `EnemyData.dropTableId`） | 10 step |
| ~~`guaranteedDrop`~~ | OZX-1380 删除，见 §1.6 | —— |
| `viaSpawner` | 走生产者通道，见 §1.4 | 20 step（6 个 spawner 类型） |
| `at` | 生成点类别覆盖 | **0** |

### 1.4 两个必须在 UI 里讲清楚的语义

**`at` 与生成点类别。** 不填 `at` 时类别由敌人推导：`EnemyData.role` 能解析成 `chaser/air/zoner/dps/edge` 就用它；否则按 `category` 是 spawner/turret → `zoner`，再否则 `movementType=="ground"` → `chaser`，其余 → `air`。类别名直接对应 tilemap 的生成层（见 §5.5）。房间里该类别一个点都没有时，运行时只是 warning 并**跳过这次 launch**——安静地少刷一批怪，这正是编辑器该提前报的错。

**`viaSpawner` 不是「生成器类型」，是敌人 id。** 它要求房间里**有活着的该类型敌人**充当生产通道，请求排队直到有实例空闲。当前使用的 6 个都是敌人：`evil_ground_factory`(6)、`big_mouth`(5)、`husk`(4)、`kolossoxenophis`(3)、`evil_watcher`(1)、`broodwing`(1)。注意 **没有任何敌人配置了 `spawnConfig.enemyIds`**，所以不能用那个字段来筛选合法的 spawner——能不能生产是运行时能力，不是数据声明。编辑器能静态证明的只有「该 encounter 在此之前 launch 过这种敌人」或「房间的 placement 挂了这种敌人」；证明不了就给 warning。

程序化楼层是例外：`FloorPlanData.enemyPool` 的条目现在可以自带 `minionId` / `minionsPerSpawner`（OZX-1032），`PoolSampler` 为每台抽到的 spawner 合成一条 `viaSpawner` step，id 形如 `jammer_via_evil_ground_factory`。这种 step 的 `viaSpawner` 一定可达——同一个房间刚刚放下了那台 spawner——所以**不要**对它报 warning。校验规则见 `level_data_reference.md` §5.4。

### 1.5 校验（`EncounterValidator` 已有 + 需要补的）

已有（结构性，抛异常）：verb 既非 `launch` 也非 `sound`、`sound` 缺 `soundId`（缺了只在 `SoundServiceAdapter` 打一行日志，看起来触发了其实没声音）、`launch` 缺 `enemyId`、`min < 0`、`max < min`、`eliteCount < 0`、`time.seconds < 0`、`touch` 缺 `triggerId`、`killed` 缺 `enemyId`、`custom` 缺 `key`、未知 `kind`。

**轨道 launch**（OZX-1309 列车 / OZX-1317 油罐车）：`enemyId` / `railTrainId` / `railOilTankId` 三选一（`ENCOUNTER_LAUNCH_TARGET_AMBIGUOUS`），`min..max` 数的是**列车数 / 辆数**。不读刷怪层字段，写了就按名拒（`EncounterValidator.RailLaunchProblem` → `ENCOUNTER_RAIL_LAUNCH_FIELD_UNREAD`）：`at`、`eliteCount`、`boss`、`variantIds`、cluster / `sharedRoot` / `fanArcDegrees`；油罐车另拒 `viaSpawner` 与 `dropTableId`。**注意两条后来放开的**：OZX-1315 起列车的每节车会掷 launch 的 `dropTableId`（pool 楼层会给每条 launch 盖楼层表）；OZX-1341 起列车可以走 rail factory 的 `viaSpawner`。`enemyId` 指向 `railConfig.enabled` 的敌人，执行器抛（`ENCOUNTER_RAIL_ENEMY_AS_BODY`）——轨道敌人只能作为列车的一节刷。`boss` 不能走 `viaSpawner`（OZX-1328，`ENCOUNTER_BOSS_ON_SPAWNER_CHANNEL`）。

pool 里的列车条目（OZX-1315，`LevelPlanValidator.ValidateRailTrainEntries`）：楼层（合并后）必须 `railCart: true`（`LEVEL_RAIL_TRAIN_OFF_RAIL`）；不能同时写 `enemyId`（`LEVEL_RAIL_TRAIN_ENTRY_AMBIGUOUS`）；不能写 variant / cluster / sharedRoot / minion 字段（`LEVEL_RAIL_TRAIN_FIELD_UNREAD`）。出货：chapter_5 的两个 basement area（票里写的 chapter_4 已过时）。

**没有**做的（编辑器必须补）：`enemyId` 是否存在、`dropTableId` 是否存在、`guaranteedDrop.itemId` 是不是 item（而不是 loot table）、`guaranteedDrop.tableId` 指的表是否存在、`viaSpawner` 是否可达、`eliteCount > 0` 的敌人是否有 elite 配置、`at` 类别在目标模板里有没有点、`cleared` 之前是否真的有会产生活体的 step。

### 1.6 掉落表与「欠的奖品」是两个通道（OZX-1041 → OZX-1380，核对于 `ozx_base@5cd9b22e`）

`EncounterAction.dropTableIdOverride` 曾经同时表达两件事——「这批敌人摇哪张表」和「这里钉了什么奖品」——两个写入方互相覆盖（OZX-1040：一个钥匙房掉四把钥匙）。OZX-1041 拆成 `dropTableId` 和 launch 上的 `guaranteedDrop`；**OZX-1380 又把奖品从 launch 挪到了房间**：

| 字段 | 回答的问题 | 语义 |
|---|---|---|
| `EncounterAction.dropTableId` | 这批敌人摇哪张表 | 替换 `EnemyData.dropTableId`；解析链 `action.dropTableId → EnemyData.dropTableId → item_exp_small` |
| `RoomNodeData.guaranteedDrops[]` | 这间房欠什么 | 由房间**开局波里最后被击杀的那只**（`InitialWaveLastKill`）**额外**支付。开局波 = 第一个带 condition 的 step 之前的所有 step 里的**直放** launch（不含 `viaSpawner`、不含轨道）；这是玩家唯一走不开的一批 |

形状（`GuaranteedDrop`）：

```jsonc
"guaranteedDrops": [
  { "itemId": "key_gold", "count": 1 },                              // 一个具名道具
  { "tableId": "loot_equipment_f0", "count": 1, "chance": 0.07 }     // 或从一张 pickOne 表里抽
]
```

编辑器的规则（`EncounterValidator.ValidateRoomPins`，RoomReady 时抛）：

- **「有没有钉东西」不是对象非空。** JsonUtility 会实例化省略的字段，**钉住成立当且仅当 `itemId` 或 `tableId` 非空**（`ROOM_PIN_EMPTY`）。
- `itemId` 与 `tableId` **互斥**（`ROOM_PIN_AMBIGUOUS`）；`tableId` 必须 `pickOne` 且非空（`ROOM_PIN_TABLE_NOT_PICKONE` / `_EMPTY`）；`count >= 1`；`chance ∈ (0, 1]`，**0 = 没写 = 每次都掉**。
- **必须有承载者**：房间要跑一个遭遇，且开局波里有一条 `min >= 1` 的直放 launch（`EncounterWaves.GuaranteesOpeningBody`），否则没人付——**没有回退通道**，而欠的通常就是这层的钥匙（`ROOM_PIN_NO_CARRIER`）。release 这类无敌人房因此不能欠东西，它的保底走 `stageMustDrop`（落箱子）。
- launch 上还写着 `guaranteedDrop` → 被静默丢弃（`ENCOUNTER_PIN_REMOVED`）。旧名 `dropTableIdOverride` 同理（`ENCOUNTER_DROP_FIELD_RENAMED`）。

出货：ch1 `f0_room_1_4`、ch2 `f0_room_3_1`、`level_test` / `factory2_test` 的 `f0_room_0_0` 用它放 `key_gold`。生成楼层由 assigner 追加（过渡钥匙、`floorLootPlan.guaranteed`）。`RoomNodeData.guaranteedCargo`（`stageMustDrop` 落的箱子）是另一回事，仍在。会话 dump 里旧的 launch pin 照常读取显示。


### 2.1 分类字段（picker 的列）

| 字段 | 取值与实测分布 |
|---|---|
| `category` | `bug` 35 / `robot` 13 / `plant` 9 / `turret` 6 / `spawner` 5 |
| `role` | `zoner` 30 / `chaser` 18 / `dps` 11 / `air` 9（68/68 都填了）——同时是生成点类别 |
| `movementType` | `ground` 54 / `fly` 14（缺省 = fly） |
| `attackType` | 19 种取值，`melee` 只是其中之一；`kamikaze` 12 / `burst` 11 / `tentacle` 8 / `melee` 6 / `none` 6 / … |
| `type` | 68/68 全是 `normal`。**Legacy，无区分度**，不要当主分类 |
| `spawnPlacement` | 空 58 / `initial` 8 / `wave` 2。不匹配只 warning，不阻止生成 |

### 2.2 关卡相关的能力字段

| 字段 | 语义 | 实测 |
|---|---|---|
| `dropTableId` | 默认死亡掉落表 | 68/68 都有 |
| `elite` | elite 调参。`EncounterAction.eliteCount > 0` 时**必需**，否则 `EnemyService.Spawn` 抛错 | 20/68 有 |
| `footprintCols/Rows` | 占地格数，>1 只对 stationary（spawner/turret/plant）生效，其余忽略 | 3/68 >1×1 |
| `size` | 视觉尺寸 | — |
| `emergeWay` / `emergeDuration` / `emergeOnSense` | 出场方式；`emergeOnSense` 需要 `emergeDuration > 0` | — |
| `excludeFromRoomClear` | 不计入清场条件 | — |
| `passive` | 永不索敌 | — |
| `deathSpawnEnemyId` / `hatchEnemyId` | 死亡/孵化衍生敌人（引用边） | 2 / 1 |
| `hatchPool` / `hatchCountMin/Max` / `hatchSpreadRadius` | 孵化池与一窝的大小，见 §2.4 | 1 |
| `spawnConfig.enemyIds[]` | 该敌人能轮转生产的类型 | **0 份数据使用** |
| `projectileId` / `beamId` | 远程弹道；`beamId` 非空时 `projectileId` 被忽略 | — |
| `spawnMinWallDistance` / `wallRepelDistance` | OZX-1263，世界单位（1 格 = 1），0 = 关。前者：直放（`LaunchPlacement`）只收离房间矩形每条边 ≥ 该值的点，jitter 夹回内矩形——房间里没有这样的内矩形时 `RoomWallClearance.ClampInside` **抛**；后者：活着时离墙更近就主动飞开。负值报错（`ENEMY_WALL_DISTANCE_NEGATIVE`）；≥ 5 时标准 16×10 房一个合格点都没有，直放的身体全被丢（`ENEMY_WALL_CLEARANCE_UNPLACEABLE`，Warning，大复合房仍可能放得下） | 1（broodwing 3 / 2） |

`role` 是这份数据里最重要的一个字段：它同时决定敌人放在哪层生成点、以及 encounter 不写 `at` 时的默认落点。改 `role` 等于改所有引用该敌人的 encounter 的落点。

**变体家族**（OZX-1427 / OZX-1428 / OZX-1429，核对于 `ozx_base@5cd9b22e`）：`EnemyData.family` + `tier`（1 = 最弱）把同一种生物的几个变体归成一族。`EnemyFamily.ValidateAll` 开机时对整个敌人库检查：两个字段要么都写要么都不写、`tier ≥ 1`（`ENEMY_FAMILY_HALF_DECLARED`）；一族的 tier 恰好是 1..n、每档一个，成员不止一个时每个成员都要自己写（`ENEMY_FAMILY_INCONSISTENT`）。没写 family 的敌人自成一族、tier 1。

- **新敌人按族判定**（OZX-1428）：某个变体出现过，整族都不算新；pool 仍按变体写。这影响「一局见全池子」的计数与采样器，由生成器体现（OLE-1）。
- **area 的 tier 上限**（OZX-1429，`ValidateVariantTiers`）：第 k 个能到达的 area（入口 k = 1，沿 area 连接最短步数 + 1，连接双向走，`ArrivalOrder`）的 pool 里，每具能放下的身体（`enemyId` 与每个 `variantIds`）`tier ≤ k`。精英提拔不受限，轨道列车不查 → `LEVEL_VARIANT_TIER_TOO_EARLY`。

### 2.3 编辑器要给的信息

enemy picker 至少显示 `category / role / movementType / attackType / footprint / 有无 elite`，并在选中后显示「哪些 encounter、enemyPool、placement 在用它」。elite 复选框要在敌人没有 elite 配置时禁用并说明原因。

### 2.4 孵化是一个池，而且不占任何预算（OZX-1063，核对于 `ozx_base@f0a78923`）

`hatchEnemyId` 现在是池的一端：`hatchPool` 是通用形式，`hatchEnemyId` 等价于单元素池。**两者同时非空直接抛**（`HatchBrood.Validate`），不设优先级、不做回退——理由在源码里写明了，是 OZX-1041 `dropTableIdOverride` 那次教训（一个字段两个含义轮流覆写）。

| 字段 | 语义 |
|---|---|
| `hatchPool` | 破壳放出来的种类池。**每只占用者各自抽一次**（等权、独立、允许重复） |
| `hatchCountMin` / `hatchCountMax` | 一颗卵开出几只，每颗卵掷一次（含端点）。**0 / 省略 = 恰好 1 只**（旧行为）；max < min 时按 min 取，不抛 |
| `hatchSpreadRadius` | 第 2 只起相对卵的散布环半径。第 1 只永远落在卵自己的坐标上 |

**孵化物完全不经过 `PoolSampler`**，所以它既不占 `FloorPlanData.enemiesPerRoom`，也不计入 encounter 的 `maxAlive`——两个预算都数不到它。一间抽到 N 颗蛋的房间实际比 `enemiesPerRoom` 多出 `N × [min, max]` 个敌人，而且那些敌人的 HP 也不在任何预算里。实测 chapter_3 floor 1（3000 seed × 38998 间）：每房植株 4.74 → 6.49，全房敌人 HP 8846 → 9456。**任何回答「这间房有多大一场仗」的视图漏掉孵化通道，都会朝同一个方向系统性偏低。**

编辑期该挡住的四条（引擎是运行时抛，而且是从 `HatchState.Enter` 抛——已经孵了三秒、门还锁着的时候）：

1. 两个 channel 同时非空。
2. 两个都空但这只敌人被当成卵（`IsEgg` 就是「任一非空」，所以这条在编辑器里表现为「不是卵」，无需报）。
3. `hatchPool` 里有空字符串（空位照样占一次抽取份额，一窝会静默变少）；或条目不是已存在的 enemy id（**这条引擎查不了**，它手里只有一只敌人的数据，没有 id 全集）。
4. `hatchCountMax > 1` 而 `hatchSpreadRadius <= 0`——孵化物是 rooted 植株，叠在一点上永远弹不开。

池子**等权**是有意的：三株 blight 植物是同一生物的三个 variant，带权重会是三个没人选过的数字。将来需要时再升级成带权条目数组。
---

## 3. LootTableData（25 份，`GameData/loot_tables/`）

### 3.1 结构与两种权重语义

```json
{ "dataType": "LootTableData", "id": "loot_common",
  "entries": [ { "itemId": "item_exp_small", "weight": 60, "minCount": 1, "maxCount": 3, "conditions": "" } ] }
```

| `pickOne` | 语义 |
|---|---|
| `false`（默认，24/25） | **每个 entry 独立按 0-100 的百分比判定**。可能同时掉多个，也可能一个都不掉 |
| `true`（1/25） | 按权重**相对**抽取，恰好掉一个 |

这是最容易被误读的地方：一张「两个 50」的 `pickOne=false` 表，有 25% 概率两个都掉、25% 什么都不掉。UI 必须按 `pickOne` 切换显示方式（百分比 vs 归一化相对权重），并且**不能假设权重和为 100**——实测有 5 张表的权重和 > 100，权重取值范围 15..100。

`conditions` 字段在 25 张表里**全部为空字符串**，属于没有消费者的预留字段。

### 3.2 当前数据的一个坑

`loot_ch1_shotgun`、`loot_ch1_supply`、`loot_ch1_laser` 三张表 **`entries` 是空数组**，而 `chapter_1.json` 的 cargo 大量引用它们（`count: 6` 的箱子成排）。空表 roll 不出东西，最终落到 `DropTableResolver` 的 `item_exp_small` 兜底。这不是崩溃，但几乎肯定不是作者本意——编辑器应把「被引用的空表」报成 Warning。

### 3.3 掉落链（编辑器的 Effective Drop 视图要复刻的顺序）

1. 敌人死亡：`instance/action override` → `EnemyData.dropTableId` → `item_exp_small` 兜底，三者取第一个命中的**一张**表；elite 额外直接给 `item_exp_large`（相加，不替换）。
   （`EventBindings.cs:103-138`。编辑器已按这一条实现：每个 launch wave 的敌人行
   下面挂一行 `DROPS`，标出最终生效的表、它是哪一级命中的，以及表里的物品；
   带技能的那一行高亮，因为这是能靠打怪拿到技能的通道，而它不在 cargo 上，Loot
   栏看不到。第 3、4 条的楼层合成与 cap 仍未实现。）
2. ~~房间清场~~：OZX-1381 删除（`lootPlanId` / `inlineLootPlan`），全被 cap 掉时也不再补发 exp+heal。房间自己的那一行现在是 `guaranteedDrops`（开局波最后一杀额外支付）。
3. 楼层通道：floor `probabilistic` 合成的楼层表会覆盖 enemy pool entry 自带的 drop override，并作为 `floorLootTableId` 给每间房的箱子；`guaranteed` 与过渡钥匙追加到选中房间的 `guaranteedDrops`。
4. `floorCap` / `categoryCap` 按实际事件顺序消费；全部候选被 cap 掉时仍回退 `item_exp_small`。

带共享 cap 的跨通道期望值**没有可靠闭式解**，只能用固定 seed 的确定性模拟给分布，不要显示伪精确的百分比。

---

## 4. ItemData（47 份，`GameData/items/`）与 taxonomy

### 4.1 ItemData

| 字段 | 说明 | 实测 |
|---|---|---|
| `type` | `skill` 23 / `weapon` 16 / `exp` 3 / `heal` 3 / `key` 2。**Legacy**：loot pool 路由已改用 taxonomy，但仍有旧调用 | 47 |
| `rarity` | `common` 19 / `uncommon` 11 / `rare` 9 / 未填 8（消耗品与钥匙） | — |
| `inventoryItem` | 是否进背包 | 16 |
| `stackable` | **0 份数据使用** | 0 |
| `refId` | **运行时从不读取**（全仓库只有声明与一处测试）。看着像「指向具体实现」，实际不是——见 4.3 | — |
| `displayName` / `nameTemplate` | 后者在生成时套名字库，空则回退前者 | — |
| `value` / `spriteKey` / `effects[]` | — | — |

过渡钥匙就是普通 item：`key_silver`、`key_gold`。`FloorPlanData.transitionKeyId` 和 `doors[].keyId` 都指向它们。

### 4.3 技能掉落靠 item 的 `id`，不是 `refId`

`ItemService.ApplySkill` 把 `item.id` 直接交给 `SkillService.AddPassive`
（`ItemService.cs:111-120`），`refId` 全程没被读过。所以一份 `type: "skill"`
的 ItemData 要能捡，**它自己的 id 必须同时是一个 SkillData 的 id**；`refId`
指对了也没用。

- `sprint_engine` 就是反例：`refId: "sprint"` 指向存在的技能，但没有
  `SkillData` 叫 `sprint_engine`，`AddPassive` 会抛
  （`SkillService.cs:84-86`）。它在 `loot_skills` 里，权重 50。
- `AddPassive` 不检查 `type`，所以 `fireball`、`sprint` 这两份 active 技能
  被当 passive 装上，不报错。
- `inventoryItem: true` 的 item 在 switch 之前就进背包了，走不到 `ApplySkill`。

编辑器把前两条分别报成 `LOOT_SKILL_ITEM_UNRESOLVED`（Error）和
`LOOT_SKILL_ITEM_NOT_PASSIVE`（Warning）。

另一条技能入手途径是 SkillPlatform（`staticPlacements` 的 `kind: "skill"`，
直接指 `skillId` → SkillData），和掉落这条完全独立，见
`level_data_reference.md` §7.2。

### 4.2 ItemTaxonomyData（`GameData/item_taxonomy.json`，单份）

扁平节点列表 `{name, parent, items[]}`，靠 `parent` 串成树（和 encounter condition 一样，因为 `JsonUtility` 撑不住自引用结构）：

```
equipment
 ├ weapon
 │   ├ ranged ─ shotgun(8) / pistol(1) / missile(1)
 │   ├ lightning(1)
 │   └ beam(5)
 └ skill ─ passive(9) / active(4) / hammer(10)
consumable
 ├ exp(3) / heal(3) / key(2)
```

`floorLootPlan.categoryCaps[].category` 和 `stageWeights[].category`（OZX-1044）填的都是这里的节点名（如 `weapon`、`equipment`）。编辑器的 category 选择器必须从这棵树取值，不能自由输入。

两处读法不同，别混：`categoryCaps` 是**上限**，`stageWeights` 是**倍率**，而且后者按子树匹配、**最具体的优先**——`core_cinder_reactor` 依次试 `core`、`equipment`，都没有才落到该 stage 的无品类条目、最后才是 1.0（`level_data_reference.md` §5.5）。一个节点即使自己不直接挂道具也是合法节点，所以「这个名字存不存在」要查节点表，不能用「它底下有没有道具」来判断——上游为此专门加了 `HasCategory`。

---

## 5. TilemapData（23 份，`StreamingAssets/TilemapData/`）

**归属提醒**：这些文件由 room template 后端生成、经 sync 下发。MVP 只读；任何改动都要走 ORT 流程（见 TD §12.1）。

### 5.1 文件分布

| 目录 | 份数 | 说明 |
|---|---|---|
| `normal/` | 18 | 主力池 |
| `basement/` | 4 | 只有 pressure |
| `test/` | 1 | 声明 `roomCategory: "normal"`，所以**它在 normal 池里**——目录名不是类别 |
| `cave/` | 0 | 空目录；洞穴不走这个池 |

### 5.2 文件结构

```json
{ "ground": [[...]], "softEdge": [...], "bridge": [...], "pipeline": [...], "rail": [...],
  "railLines": [...], "static": [...], "chaser": [...], "zoner": [...], "dps": [...],
  "mobAir": [...], "mainPath": [...],
  "doors": {"top":0,"right":1,"bottom":0,"left":1}, "doorOverrides": {"top":1,"bottom":1},
  "stageType": "pressure", "roomShape": "all", "roomCategory": "basement", "openDoors": 10,
  "meta": {"name":"...","version":1,"width":20,"height":12} }
```

层的语义：`ground`（>0 为可走）、`softEdge`（未被墙覆盖的边缘，也是 edge 生成点的来源）、`bridge`、`static`（静态摆放可用格）、`chaser`/`mobAir`/`zoner`/`dps`（四类敌人生成点）、`mainPath`、`rail`（蒸汽车轨道）、`pipeline`。

### 5.3 索引方向（最容易写错的一件事）

运行时按 **`grid[x][y]`** 读：`RoomTilemapData.Size()` 返回 `(Ground.Count, Ground[0].Count)`，`Visit` 外层循环 x、内层 y，`RoomGroundRenderer` 直接把 `size.X` 当宽度。**外层数组是列（世界 X），内层是行（世界 Y）。**

而**每一份文件的 `meta.width/height` 都是反过来写的**：23/23 满足 `meta.width == 内层长度`。例如 `basement/all_pressure_10_01.json` 的数组是 12×20、`meta` 写 `width:20, height:12`，运行时把它当成 X=12、Y=20 的房间。

结论：**以代码为准，不要信 `meta`**。任何按 `meta.width` 去切外层数组的工具都会把非正方形房间整个转置——而且因为两个维度都存在，它不会报错，只会安静地给出错误的「这个格子合法」。

### 5.4 门的两套编码（互不相同，别混用）

| 概念 | 编码 |
|---|---|
| `DoorLinkData.direction` / `toDoorId`（level 侧枚举） | `Up=0, Down=1, Left=2, Right=3` |
| `openDoors`（tilemap 侧位标志） | `Top=1, Right=2, Bottom=4, Left=8` |

实测一致：`doors {top:0,right:1,bottom:0,left:1}` 对应 `openDoors: 10` = 2+8。房间需要的 mask 由它实际拥有的门方向算出，再拿去匹配模板。

### 5.5 匹配算法（`RoomTilemapQuery.Query`）

三阶段：

1. **过滤**：`roomCategory`（查询为 `basement` 时**额外**要求文件在 `basement/` 目录下）、`roomShape`、`stageType`。三者的通配规则都是「查询为空 = 不过滤；模板值为空 = 通配」，`roomShape` 额外认 `all`，`stageType` 额外认 `all` / `none`。候选为空直接抛异常。
2. **精确 openDoors**：相等的候选里随机挑一个。
3. **超集回退**：`(候选.openDoors & 需要的 mask) == mask` 的候选里随机挑。仍然没有 → 抛异常。

注意超集回退意味着**一个四门模板可以服务任意门组合**，多余的门口会被封上。

### 5.6 覆盖矩阵（编辑器可以直接用它做门/stage 的可选性判断）

当前 23 份模板对 15 种门 mask 的覆盖：

| category | stageType | 精确匹配的 mask | 只能超集回退 | **无法匹配（抛异常）** |
|---|---|---|---|---|
| normal | `start` | 1 | — | **2-15 全部** |
| normal | `teaching` | 5, 14, 15 | 其余 | 无 |
| normal | `building` | 5, 10, 15 | 其余 | 无 |
| normal | `pressure` | 5, 6, 15 | 其余 | 无 |
| normal | `peak` | 5, 15 | 其余 | 无 |
| normal | `release` | 5, 9, 15 | 其余 | 无 |
| normal | `default` / `boss` / `exit` | — | — | **没有任何模板** |
| basement | `pressure` | 2, 8, 10 | — | **1,3,4,5,6,7,9,11-15** |
| basement | 其他所有 stage | — | — | **没有任何模板** |

可直接落成规则：

- **start 房只能有一扇上门**（mask 1）。给它加任何别的门 = 运行时抛异常。
- **basement 房只能有左/右门**（2 / 8 / 10）。任何上下门都没有模板。
- **`stageType` 写 `default`、`boss`、`exit` 的房间没有模板可用**——虽然这些值在别处是合法词汇。相比之下 `stageType` **留空**反而最宽松：查询为空即不过滤，所有模板都是候选。
- 其余 normal stage 因为存在 mask 15（四门）模板，任何 mask 都至少能超集回退，不会失败。

这张表随模板池变化，**必须由工具从当前 TilemapData 现算，不能硬编码**。

### 5.7 镜像变体进入抽取池（OZX-1467；核对于 `ozx_base@b3d4d526` 的**工作区改动**——撰写时上游尚未提交）

`RoomTilemapQuery.LoadPool` 现在把每张**没标 `noMirror`** 的模板展开成 4 条：原样 / `FlipX` / `FlipY` / `Rotate180`（`Game.Room/Tilemap/RoomTilemapQuery.cs:41`）。没有 90° 旋转，因为要保持网格尺寸。

- **门位重映射**（`RoomTilemapData.cs:252` `MirrorDoors`）：`FlipX` 交换 Left↔Right，`FlipY` 交换 Top↔Bottom，`Rotate180` 两个都换；高于 4 位的 bit 原样保留。门在边中点，翻过去正好落在对边的门位上。
- **每张底板固定 4 条**，对称模板也不去重，保证各底板被抽中的概率不变。
- **命名**：变体的 `SourceFile` 是 `<原 id>@<朝向>`（OZX-1571 起原 id 带目录，如 `normal/x@FlipX`）。**pinned `templateId` 只匹配原样**（`RoomLoadManager` 按 `SourceFile == templateId` 找，没人会写 `@FlipX`）。
- **去重按 family**（OZX-1103 的邻居去重、`ChapterTilemapAssigner` 的 priorUse）：A 的邻居不会抽到 A 的任何朝向。
- **opt-out**：`RoomTilemapData.noMirror`（bool，缺省 false，`RoomTilemapData.cs:59`）——给「不对称就是重点」的布局用。

所以 §5.6 的覆盖矩阵要在**展开后的池子**上算：一张只有 Left 门（mask 8）的 basement 模板，现在也服务 Right（mask 2）。编辑器侧：`ozxlevel/tilemaps.py` 的 `TilemapIndex.facts()` 返回展开后的池（`expand` / `mirror_doors`），`candidates()` 是 `RoomTilemapQuery.Candidates` 的 Python 版；`Dataset.pinned_templates` 直接按文件名读文件，不经过这个池，所以 pin 仍然只认原样。

---

## 6. 编辑器实现清单

**选择器（picker）**

| 数据族 | 必须显示的列 | 必须过滤掉的 |
|---|---|---|
| Encounter | id、step 数、maxAlive、涉及的敌人 | — |
| Enemy | category、role、movementType、attackType、footprint、有无 elite | 选 elite 时排除无 elite 配置的 |
| Loot | id、pickOne、entry 数、条目预览 | 标记空表 |
| Item | id、type、rarity、taxonomy 路径 | `equipment` placement 只列真实 item |
| Tilemap | folder、stageType、roomShape、openDoors、尺寸 | 按当前房间的 category/stage/mask 预筛 |

**校验（除 §1.5 / §3.2 外）**

- `eliteCount > 0` → 目标敌人必须有 `elite`。
- `at` / 推导出的类别 → 目标模板必须有该生成层的格子。
- `viaSpawner` → 静态证明不了就 warning，不要报错（运行时能力）。
- `categoryCaps.category` → 必须是 taxonomy 节点。
- `stageWeights.category` → 必须是 taxonomy 节点；同一个 `(stage, category)` 不能有两条。
- 房间的门 mask → 用 §5.6 的现算矩阵判断能否匹配到模板，匹配失败时列出参与匹配的 folder / stage / shape / mask 和最接近的模板，而不是只说「找不到模板」。
- 被引用的空 loot 表 → warning。

**不要做**

- 不要用 `spawnConfig.enemyIds` 推断合法的 `viaSpawner`（零数据使用）。
- 不要把 `EnemyData.type`、`ItemData.type`、`LootEntryData.conditions`、`ItemData.stackable` 当作有效输入推荐（legacy 或零使用）。
- 不要按 `meta.width/height` 解释 tilemap 网格。
- 不要重新引入嵌套 encounter condition。
- 不要假设 loot 权重和为 100。

---

## 附：源文件索引

| 内容 | 路径（`ozx_base`） |
|---|---|
| EncounterData / Step / Condition / Action | `Assets/Scripts/Game.Contracts/Data/EncounterData.cs` |
| 顺序执行语义 | `Assets/Scripts/Game.Encounter/SequentialEncounterRuntime.cs` |
| 结构校验 | `Assets/Scripts/Game.Encounter/EncounterValidator.cs` |
| launch 执行、`at` / `viaSpawner` 落点 | `Assets/Scripts/Game.Unity/Character/Enemy/EncounterActionExecutor.cs` |
| role → 生成点类别 | `Assets/Scripts/Game.Character/EnemyEntity.cs`（`GetSpawnPointCategory`） |
| EnemyData 全字段 | `Assets/Scripts/Game.Contracts/Data/EnemyData.cs` |
| LootTableData / ItemData | `Assets/Scripts/Game.Contracts/Data/LootData.cs` |
| 掉落解析、`item_exp_small` 兜底 | `Assets/Scripts/Game.Loot/DropTableResolver.cs` |
| 楼层 cap | `Assets/Scripts/Game.Loot/FloorBudgetService.cs` |
| 敌人死亡掉落链 | `Assets/Scripts/Game.Integration/EventBindings.cs` |
| tilemap 结构与索引 | `Assets/Scripts/Game.Room/Tilemap/RoomTilemapData.cs` |
| 模板匹配 | `Assets/Scripts/Game.Room/Tilemap/RoomTilemapQuery.cs` |
| 生成点提取 | `Assets/Scripts/Game.Room/SpawnPositionExtractor.cs` |
| 枚举（DoorDirection / OpenDoors） | `Assets/Scripts/Game.Contracts/Enums/GameEnums.cs` |
