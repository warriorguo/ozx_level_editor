# OZX Level 数据参考（`GameData/levels/`）

- 面向读者：OZX Level Studio 的实现者
- 数据与代码快照：`ozx_base` `main@d867b644`，2026-09-11
- 配套文档：`ozx_level_data_editor_td.md`（工具的技术设计）、`level_referenced_data.md`（level 指向的 encounter / enemy / loot / item / tilemap 五个数据族）。本文只描述**磁盘上的数据长什么样、运行时怎么读它**，不重复工具架构。

编辑器要「能干活」，最低限度必须吃透三件事：字段语义（§3-§5）、plan 编译成 level 的过程（§6）、以及运行时已经在强制的校验（§7）。§8 是据此得出的实现清单。

---

## 1. 文件清单

目录：`ozx_base/Assets/StreamingAssets/GameData/levels/`，14 个 json（各配一个 `.meta`）。两种 `dataType` 混在同一目录，**不能按文件名区分**：

| 文件 | dataType | id | 规模 |
|---|---|---|---|
| `chapter_1.json` | LevelData | `chapter_1` | 1 floor / 12 rooms |
| `chapter_2.json` | LevelData | `chapter_2` | 2 floors / 18 rooms |
| `level_cave_demo.json` | LevelData | `level_cave_demo` | 1 floor / 3 rooms |
| `level_generated_tree.json` | LevelData | `level_generated_tree` | 2 floors / 12 rooms |
| `level_generated_cycle.json` | LevelData | `level_generated_cycle` | 2 floors / 18 rooms |
| `level_test.json` | LevelData | `level_test` | 4 floors / 21 rooms |
| `early_1floor.json` | LevelBasePlanData | `plan_early_1floor` | 1 floor plan |
| `early_2floor.json` | LevelBasePlanData | `plan_early_2floor` | 2 |
| `standard_3floor.json` | LevelBasePlanData | `plan_standard_3floor` | 3 |
| `factory_3floor.json` | LevelBasePlanData | `plan_factory_3floor` | 3 |
| `level_01.json` | LevelBasePlanData | `level_01` | 1 |
| `level_02.json` | LevelBasePlanData | `level_02` | 2 |
| `level_03.json` | LevelBasePlanData | `level_03` | 3 |
| `level_10.json` | LevelBasePlanData | `level_10` | 3 |

合计 84 个静态房间、18 个 floor plan。注意 `id` 与文件名并不总一致（`early_1floor.json` → `plan_early_1floor`），**不能用文件名推断 id**。

`level_generated_tree/cycle` 是生成器 dump 出来的产物，`level_test` 是测试数据；把它们当成风格参考时要留意它们并不遵守生产章节（`chapter_1/2`）的约定。

---

## 2. 通用序列化约定

所有 GameData 共用一个 wrapper：顶层必须有 `dataType` 与 `id`，`JsonDataRepository` 按 `dataType` 找 C# 类型、按 `id` 入库。

反序列化走 Unity 的 `JsonUtility`，它的语义决定了编辑器能不能如实还原作者意图：

| 行为 | 后果 | 编辑器对策 |
|---|---|---|
| **枚举序列化成整数** | `direction: 1` 就是 `Down`，磁盘上看不到名字 | UI 显示名字，写回整数；见 §4.4 |
| **省略字段 ≡ 显式 0 / "" / false** | `count: 0` 与「没写 count」在 typed 层不可分 | 判定「有没有 author」一律看 DOM，不看 typed 值 |
| **嵌套 `[Serializable]` 对象永不为 null** | `appearance` 即使没写也会被造出来 | 用判别字段（如 `movingBackground.assetKey` 非空）判断是否启用，不要 `!= null` |
| **未知字段被静默丢弃** | 运行时看不到，但它们真实存在于数据里 | 只编辑 DOM，未知字段原样保留 |

未知字段不是假设：`level_test.json` 的 `f0_room_0_0` 上有 `"pinballHammers": 1`，当前 `RoomNodeData` 里没有这个字段。任何「读 typed → 写 typed」的工具都会把它删掉。

`[NonSerialized]` 字段（`inlineEncounter`、`inlineLootPlan`、`guaranteedCargoTableIds`、`floorLootTableId`、`effectiveStageWeight`）是运行时合成结果，**磁盘上永远不存在，也永远不能写回**。

---

## 3. LevelData

```
LevelData
 ├ id / displayName / masterSeed
 └ floors[] : FloorData
      ├ index / startRoomId / bossRoomId / exitRoomId / themeId
      ├ topRoomIds[] / topMiddleRoomId
      ├ rooms[]      : RoomNodeData
      ├ caveLinks[]  : CaveLinkData
      └ stairLinks[] : CaveLinkData（同一个类，不同语义）
```

### 3.1 LevelData

| 字段 | 类型 | 说明 | 实际使用 |
|---|---|---|---|
| `id` | string | 主键 | 6/6 |
| `displayName` | string | 展示名 | 6/6 |
| `masterSeed` | int | **每次动态生成时写入的 per-run 种子**。0 = 每个房间回退到由 `roomId` 派生的稳定 seed。手写关卡保持 0 | 0/6 |
| `floors` | FloorData[] | 楼层，按 `index` 组织 | 6/6 |

### 3.2 FloorData

| 字段 | 类型 | 说明 | 实际使用（12 floors） |
|---|---|---|---|
| `index` | int | 楼层号，0 起 | 6（写 0 的楼层因 JsonUtility 无法与省略区分） |
| `startRoomId` | string | 入口房。必须是本层房间 | 12 |
| `bossRoomId` | string | boss 房 | 7 |
| `themeId` | string | 主题（如 `themes/factory`） | 11 |
| `rooms` | RoomNodeData[] | 本层房间 | 12 |
| `caveLinks` | CaveLinkData[] | 洞穴通道（同层） | 1 |
| `stairLinks` | CaveLinkData[] | 楼梯（跨层，规则见 §7.1） | 4 |
| `exitRoomId` | string | 专用出口房，只有一扇 Up 门通往下一层 start | 0（生成器写） |
| `topRoomIds` | string[] | 朝向上一层的顶边房间，供 `ConnectFloors` 选跨层连接源 | 0（生成器写） |
| `topMiddleRoomId` | string | `topRoomIds` 的首选中点（tree 的居中分支） | 0（生成器写） |

最后三个字段是生成器产物。编辑器可以显示，但不应引导手写。

### 3.3 RoomNodeData

按「实际被使用的程度」排序，84 个房间中非默认值出现次数：

| 字段 | 类型 | 默认 / 语义 | 使用 |
|---|---|---|---|
| `roomId` | string | 主键，楼层内唯一。也是 render seed 的来源 | 84 |
| `roomCategory` | string | `normal` / `basement` / `cave` / `test` | 84 |
| `doors` | DoorLinkData[] | 见 §4.4 | 83 |
| `encounterId` | string | 驱动本房刷怪的 `EncounterData` id | 52 |
| `stageType` | string | `start`/`teaching`/`building`/`default`/`pressure`/`peak`/`release`/`boss`/`exit`。**缺省 ≠ default**，53/84 未填 | 31 |
| `decorations` | DecorationEntryData[] | 纯视觉，可选，见 §4.6 | 31 |
| `staticPlacements` | StaticPlacementEntryData[] | **非 cave 房必须声明**（空数组也行），见 §4.5 | 83 声明 / 28 非空 |
| `appearance` | RoomAppearanceData | 本房相对楼层主题的差异，见 §4.7 | 11 |
| `templateId` | string | 钉死 tilemap 模板，跳过 `RoomTilemapQuery` 匹配 | 8 |
| `lootPlanId` | string | 清场掉落表 | 8 |
| `hasTeleportSpot` | bool | 传送点 | 5 |
| `requiredForExitItemIds` | string[] | 未拾取这些 item 则门不开（对照本房实际掉落匹配） | 2 |
| `isFinalRoom` | bool | 清场即通关（`SessionVictoryEvent`） | 2 |
| `roomCols` / `roomRows` | int | 复合房尺寸，0 = 1×1 | 1 |
| `roomShape` | string | `bridge` / `platform` / null | **0** |
| `bossId` | string | 楼层 boss 敌人 id | **0** |
| `seed` | int | 渲染种子，0 = 由 roomId 派生 | **0** |
| `steamTrain` | bool | 沿 rail 层跑蒸汽车；房间没有 rail 层却开启 = fail-fast | **0** |

两个反直觉点：

- `bossId` 本身**不驱动刷怪**。静态 boss 房仍然需要 `encounterId`；只填 `bossId` 的房间是空房。
- `isFinalRoom` 只能手写在 `LevelData` 或 `FloorPlanData.rooms` 上，生成器不会自己设。当前所有关卡都没有胜利条件（只有 2 个房间设了它）。

### 3.4 door / cave / stair 的地位差异

一个房间的连通性来自三处，语义完全不同，编辑器不能混成一种「连线」：

| 通道 | 载体 | 范围 | 是否需要孪生 |
|---|---|---|---|
| 门 | `RoomNodeData.doors` | 同层相邻 | 需要（方向相反的 twin） |
| 洞穴 | `FloorData.caveLinks` | 同层任意两房，中间有一段通道房 | 单条记录描述 A↔B |
| 楼梯 | `FloorData.stairLinks` | **跨层** | 需要（对侧楼层的反向记录，见 §7.1） |

---

## 4. 房间子结构

### 4.4 DoorLinkData

```json
{ "direction": 0, "toRoomId": "f0_room_0_2", "toDoorId": 1, "locked": false }
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `direction` | enum→int | 本房这扇门的朝向 |
| `toRoomId` | string | 对面房间 id |
| `toDoorId` | enum→int | **对面那扇门的朝向**（不是索引、不是 id） |
| `locked` | bool | 锁门 |
| `keyId` | string | 开锁所需 item id |

`DoorDirection` 的整数编码：**`0 = Up`、`1 = Down`、`2 = Left`、`3 = Right`**。

孪生规则：A 的 `direction = Up(0)` 指向 B，则 B 必须有一扇 `direction = Down(1)`、`toRoomId = A` 的门，且两侧的 `toDoorId` 各自填对面的朝向。上例来自 `chapter_1.json`：`f0_room_0_1` 用 `Up` 连 `f0_room_0_2`，`toDoorId = Down`。

实际用量：162 条门记录、3 条锁门。其中 53 条 `direction` 与 51 条 `toDoorId` 的值是**显式写出的 `0`**（= `Up`），另有 4 条门省略了 `toDoorId`。

**`0` 是一个合法方向，不是「没填」**——这是整份数据里最容易被工具写坏的字段。任何「值等于默认就不输出」的 writer 都会把 53 扇向上的门变成 4 扇省略门加 49 处语义漂移；判断一扇门有没有 author `direction`，只能看 DOM 里键在不在，不能看 typed 值是不是 0。

### 4.5 StaticPlacementEntryData

非 cave 房必须声明 `staticPlacements`（可以是 `[]`）；`null` 会被各 SpawnAdapter fail-fast。没有全局兜底：没 author 的东西不会生成。

两种模式二选一：**pinned**（`cells` 非空，`count` 被忽略）或 **random**（`count > 0`，按未占用的 Static 层格子采样）。

| 字段 | 说明 | 使用 |
|---|---|---|
| `kind` | `cargo`/`oiltank`/`toxicbarrel`/`pinballhammer`/`prop`/`skill`/`device`/`trigger`/`equipment`，未知 kind fail-fast。注意 `LevelData.cs` 里那段注释的清单**漏了 `toxicbarrel`**（`ToxicBarrelSpawnAdapter` 确实认它）——kind 的权威来源是各 adapter，不是契约注释 | 39 |
| `count` | random 模式数量 | 36 |
| `lootTableId` | 仅 `cargo`：指定该箱子的掉落表 | 27 |
| `skillId` | 仅 `skill`：必填，被动技能 id | 4 |
| `footprintCols/Rows` | 占地覆盖，0 = kind 默认（oiltank/skill 2×2，device 2×3，其余 1×1）。只有 oiltank/skill/device 支持非默认 | 3 |
| `mountedEnemyId` | 挂在该物体上的敌人（目前只有 `cargo` 认）。pinned 天然可追溯；count 模式下只有该 kind **唯一一条** count 条目时才允许 | 3 |
| `cells` | pinned 格子列表 `{x,y}` | 2 |
| `mountedCount` | 带敌人的实例数。0 = pinned 每个都带 / count 模式只带 1 个 | 2 |
| `itemId` | 仅 `equipment` 必填；也被任何设了 `requiredForExit` 的条目读取 | 1 |
| `spawnIf` | 仅 `equipment`；目前唯一合法值 `player_has_no_weapon`，拼错 fail-fast | 1 |
| `requiredForExit` | 该 item 没被拾取就不开门（按 `itemId` 匹配实际掉落） | 1 |
| `prefabKey` | `prop` 必填；`device` 可选（省略 = 从 `device_pool` 随机） | 0 |
| `direction` | `up/right/down/left`。`toxicbarrel` 用它决定滚动方向，空 = 每个实例随机；`device` **忽略**此字段 | 0 |
| `triggerId` | 仅 `trigger` 必填，对应 encounter 的 `touch` 条件；pinned-only | 0 |
| `buffDurationSeconds` | **Legacy，不再被读取**（技能平台已改为永久） | 1 |

### 4.6 DecorationEntryData

纯视觉，可选（null / `[]` 都行），不占格、不挡路。

四种内容来源互斥，必须恰好写一个：`prefabKey`（预制体）、`spriteKey`（单图，同材质会合批）、`fxKey`（循环特效，支持 `fx/X#Y@RRGGBB`）、`decalCategory`（按主题贴花类别撒点）。另有 `stencilBayNumber`：一个**字符串**形式的 0..99 整数，因为它同时是模式判别符（`"0"` 是合法编号，而 int 0 无法与省略区分）。

三种放置模式按字段推断：free（都不写，`pos` 是相对房间原点的世界偏移）/ pinned（`cells`）/ random（`count`）。`decalCategory` 只认 `count`——「撒多少」由关卡说，「长什么样」由主题说。

数值字段沿用「0 = 未 author」：`scale` 每轴 0 表示保持原值，`alpha <= 0` 表示回退到 `colorHex` 的 A 通道。因此**全透明或零缩放的装饰是无法表达的**。

实际用量集中在 `decalCategory + count`（123/124），其余都是零星使用。

### 4.7 RoomAppearanceData

该房相对楼层主题的差异，字段全部「空 = 用主题默认」：

- `background`：朝向 token（`Top`/`TopBottom`/`LeftRight`/`All`），仍然是从主题里挑。
- `backgroundImage`：直接钉一张 ResourcesDB sprite，越过主题列表；与 `background` 同时存在时它赢。
- `fixedBackground`：屏幕固定背景（如 `fixedbackground/galaxy`）。
- `movingBackground`：远景移动背景，**以 `assetKey` 非空为判别符**（对象本身永远非 null）。
- `tilePartConfig`：钉死地砖套（如 `FactoryTilePart1`）。
- `groundShadow`：钉死环境阴影 sprite。

### 4.8 CaveLinkData（caveLinks 与 stairLinks 共用）

| 字段 | caveLinks | stairLinks |
|---|---|---|
| `caveId` | 唯一 id | 唯一 id |
| `roomIdA` / `roomIdB` | 同层两个房间 | **`roomIdA` 必须在本层**，`roomIdB` 在 `targetFloor` |
| `linkType` | `cave`（有通道）/ `direct`（直连） | 同 |
| `encounterId` | 通道里的敌人 | 同 |
| `targetFloor` | 0（同层） | 目标楼层，**不能等于自身楼层** |
| `gridWidth/Height` | 通道网格尺寸，≥1 | 楼梯数据里通常省略 |
| `tentaclePlacement` | `{count, allowedTypes[]}`，占用通道里的 TentaclePlaces 标记 | — |

真实样例（`level_test.json`）：

```json
{ "caveId": "cave_test_01", "roomIdA": "f0_room_0_0", "roomIdB": "f0_room_2_0",
  "linkType": "cave", "targetFloor": 0, "gridWidth": 3, "gridHeight": 4,
  "tentaclePlacement": { "count": 5,
    "allowedTypes": ["tentacle_basic", "tentacle_thick", "tentacle_long", "tentacle_fast"] } }
```

---

## 5. LevelBasePlanData

```
LevelBasePlanData
 └ floors[] : FloorPlanData
      ├ roomCountMin/Max, floorWidth/Height Min/Max, generatorType, themeId
      ├ stageTypes[]            : StageTypePlanData
      ├ enemyPool[] + enemiesPerRoom
      ├ floorLootPlan           : FloorLootPlanData
      ├ caves[] / stairs[]      : CavePlanData / StairPlanData
      ├ rooms[]                 : RoomNodeData（generatorType="static" 时使用）
      ├ defaultStaticPlacements[]
      └ bossId / transitionKeyId / overflow* / useExitRoom / endsRunOnClear
```

### 5.1 FloorPlanData

18 个 floor plan 中的使用情况：

| 字段 | 说明 | 使用 |
|---|---|---|
| `roomCountMin/Max` | 房间数区间，编译时随机取 | 18 |
| `themeId` | 楼层主题 | 18 |
| `stageTypes` | stage 比例，见 §5.2 | 18 |
| `enemyPool` + `enemiesPerRoom` | 敌人名册。**设了 `enemyPool` 就必须给 `enemiesPerRoom > 0`**，否则 fail-fast | 12 |
| `floorLootPlan` | 楼层掉落方案，见 §5.3 | 12 |
| `transitionKeyId` | 过渡钥匙 item id；编译期自动绑到 peak/跨层流程并插锁门 | 10 |
| `generatorType` | `tree`/`cycle`/`mixed`/`static`；**缺省和未知值都静默回退 `tree`** | 9 |
| `defaultStaticPlacements` | 兜底静态摆放，只作用于「生成后 staticPlacements 仍为空」的房间 | 9 |
| `floorWidth/HeightMin/Max` | 显式楼层尺寸，0 = 由房间数推导。cycle：height 是环高（1 = 平排，≥3 = 完整环）；tree：只有 floor 0 的 height 有意义 | 4 |
| `caves` | `{linkType, encounterId, lootPlanId}` | 1 |
| `bossId` | 楼层 boss，合成为单敌人 inline encounter | 0（字段写了 null） |
| `stairs` | `{targetFloor, linkType, encounterId}` | 0 |
| `rooms` | 预定义房间；`generatorType="static"` 时必填，否则 fail-fast | 0 |
| `overflowStageType/EncounterId/LootPlanId` | 比例分配后剩余房间的兜底。默认 `pressure` / `loot_common` | 0 |
| `useExitRoom` | true = 合成专用 exit room（单向 Up 门） | 0 |
| `endsRunOnClear` | 把 `isFinalRoom` 盖到 `bossRoomId` 指的房间；楼层没有 boss 房则抛错 | 0 |

### 5.2 StageTypePlanData

`{ stageType, portion, encounterId, lootPlanId }`。`portion` 是相对比例（不必和为 1，实现按总和归一）。

### 5.3 FloorLootPlanData

| 子结构 | 字段 | 语义 |
|---|---|---|
| `probabilistic[]` | `itemId`, `weight`(0=1.0), `minCount`/`maxCount`(0=1), `floorCap`(0=无限) | 采样成每房的 `inlineLootPlan`，并合成楼层表 |
| `guaranteed[]` | `itemId`, `count`, 三选一绑定：`boundEnemyId` / `boundRoomId` / ~~`boundWaveLastEnemy`~~ | **必须且只能设一个绑定**；`boundWaveLastEnemy` 现在无条件抛错（named wave 已移除） |
| `categoryCaps[]` | `category`（taxonomy 节点）, `maxPerFloor` | 每层每类上限 |
| `stageWeights[]` | `stageType`, `weight` | 按 stage 缩放 `probabilistic`；缺省 1.0，0 = 该 stage 不掉，负数非法 |
| `stageMustDrop[]` | `stageType`, `drops[{itemId,count}]` | 该 stage 的**每个房间**各生成一个装着该 item 的 cargo |

真实样例见 `level_10.json` 的 floor 1：4 条 probabilistic（两把枪各 `floorCap: 1`）、`weapon` 类每层上限 2、stage 权重 start/boss 归零、release 必掉一个中血包。

---

## 6. plan 如何编译成 level

程序化关卡的作者输入是规则，房间是编译产物。编辑器要展示「输入 → 指定 seed 的结果」，就得复刻这条顺序（`LevelBasePlanAssigner.Apply`，逐楼层）：

```
AssignStageTypes → AssignEnemyPools → AssignLootPools → AssignBossId → AssignFinalRoom
→ AssignStaticPlacements → InsertCaveLinks → InjectGuaranteedDrops → InjectStageMustDrops
→ RegisterFloorCaps                                     （所有楼层完成后）→ InsertLockedDoors
```

几个必须知道的细节：

1. **stage 按 BFS 顺序分配**（`OrderRoomsByBFS`，从 start 房开始），比例用**最大余数法**（`PortionResolver`）换算成整数房间数，保证总和等于房间数。
2. **stage 赋值会覆盖房间的 `encounterId` 和 `lootPlanId`**——包括把它们覆盖成 `null`。在 plan 驱动的楼层里，`FloorPlanData.rooms` 上手写的 encounter 会被 stage 条目盖掉。
3. 比例分配后剩下的房间走 overflow，默认 `stageType = pressure`、`lootPlanId = loot_common`。
4. enemy pool 先合成 `inlineEncounter`；只要 floor 的 `probabilistic` 非空，随后合成的楼层表就会覆盖这些 action 的 `dropTableIdOverride`——**enemy pool entry 自带的 `dropTableId` 因此可能不生效**。
5. `guaranteed.boundEnemyId` 再覆盖第一个匹配的 inline action；自动过渡钥匙还可能覆盖 peak 房最后一个 action。
6. 编译产物落在 `[NonSerialized]` 字段上（§2），因此源 json 无法解释「这个 seed 下这个房间到底有什么」。这正是工具要提供 Effective 视图的原因。

---

## 7. 运行时已经在强制的校验

这些规则今天由运行时 fail-fast 或静默失败兜底；编辑器的价值就是把它们提前到编辑期。

### 7.1 楼梯孪生（`LevelValidator.ValidateStairLinks`，启动即校验）

三条硬规则，违反即抛异常：

1. `roomIdA` 必须是**本层**房间。
2. `targetFloor` 必须在 `[0, floors.Length)` 且**不等于本层**。
3. 目标楼层必须存在反向孪生：`roomIdA'=roomIdB`、`roomIdB'=roomIdA`、`targetFloor'=` 本层。

缺孪生 = 玩家能过去回不来。注意这是当前 `LevelValidator` 的**全部**内容——门、可达性、模板兼容都还没有人管。

### 7.2 其他 fail-fast 点（分散在各处）

- 非 cave 房 `staticPlacements == null` → `AuthoredStaticPlacementsLogic.RequireDeclared` 抛错。
- pinned `cells` 不在 Static 层 → 抛错（`equipment` 例外，按 ground 校验）。
- `mountedEnemyId` 用在非 `cargo` kind，或落在有多条 count 条目的 kind 上 → 抛错。
- `mountedCount` 超过该条目能放置的数量 → 抛错。
- `spawnIf` 写了未知 key、`prop` 缺 `prefabKey`、`skill` 缺 `skillId`、`trigger` 缺 `triggerId`、`equipment` 缺/错 `itemId` → 抛错。
- `steamTrain: true` 但房间模板没有 rail 层 → 抛错。
- `generatorType: "static"` 但 `rooms` 为空 → 抛错。
- `enemyPool` 非空但 `enemiesPerRoom <= 0` → 抛错。
- `guaranteed` 条目绑定数 ≠ 1 → 抛错；`boundWaveLastEnemy` 无条件抛错。
- `endsRunOnClear: true` 但楼层没有 boss 房 → 抛错。

### 7.3 静默失败（更危险，编辑器必须报错）

- **重复 `(dataType, id)`**：后加载者直接覆盖，无任何提示。
- **未知 `generatorType`**：静默变成 `tree`。
- **跨层门没有配 stairLinks**：静默死路。
- **新的 `openDoors` 组合没有对应模板**：生成器重映射时把门封掉，源数据看起来完全正常。
- **只填 `bossId` 的静态 boss 房**：不刷任何怪。
- **`requiredForExit` / `requiredForExitItemIds` 指向本房不会掉落的 item**：门锁不住（这是刻意的宽松，不是 bug，但值得提示）。
- **未知字段**（如 `pinballHammers`）：被 `JsonUtility` 丢弃，作者以为生效了。

---

## 8. 编辑器实现清单

**必须做到**

- 以 DOM 为唯一可写真源，未知字段与未编辑值原样保留。
- 门的 `direction` / `toDoorId` 按枚举整数写回，`Up = 0` 不能当成「空」省略掉。
- 建双向门时同时生成孪生；建跨层楼梯时同时生成对侧反向记录。
- `staticPlacements` 在新建非 cave 房时默认写 `[]`，不要留 null。
- 区分「未填」与「填了 0/空」：`stageType`、`roomShape`、`generatorType`、所有 `count`/`weight`/`portion` 都要三态。
- 引用字段（`encounterId`/`lootPlanId`/`templateId`/`enemyId`/`itemId`/`skillId`/`keyId`）只能从现有 id 中选。
- 程序化关卡必须能按固定 seed 编译并展示 §6 的覆盖链，否则设计师无法解释「为什么我改的 drop 没生效」。

**不要做**

- 不要把 `inlineEncounter` / `inlineLootPlan` / `floorLootTableId` / `guaranteedCargoTableIds` / `effectiveStageWeight` 写进 json。
- 不要把 `masterSeed`、`topRoomIds`、`topMiddleRoomId`、`exitRoomId` 作为手写字段推荐。
- 不要为 `boundWaveLastEnemy` 和 `buffDurationSeconds` 提供新建入口（前者抛错，后者不再读取）。
- 不要用文件名推断 id，也不要因为普通编辑就重命名文件。
- 不要用 typed round-trip 保存文件（会丢未知字段，并把省略的字段写成显式 0）。

---

## 附：源文件索引

| 内容 | 路径（`ozx_base`） |
|---|---|
| LevelData / FloorData / RoomNodeData / 门 / 洞穴 / 摆放 / 装饰 | `Assets/Scripts/Game.Contracts/Data/LevelData.cs` |
| RoomAppearanceData / Vec2Data | `Assets/Scripts/Game.Contracts/Data/RoomData.cs` |
| LevelBasePlanData / FloorPlanData / 掉落方案 | `Assets/Scripts/Game.Contracts/Data/LevelBasePlanData.cs` |
| DoorDirection 等枚举 | `Assets/Scripts/Game.Contracts/Enums/GameEnums.cs` |
| plan → level 编译 | `Assets/Scripts/Game.Level/LevelBasePlanAssigner.cs`、`PortionResolver.cs` |
| 生成器入口 | `Assets/Scripts/Game.Unity/Level/DynamicLevelBuilder.cs` |
| 楼梯校验 | `Assets/Scripts/Game.Level/LevelValidator.cs` |
| 静态摆放约束 | `Assets/Scripts/Game.Room/StaticPlacement/AuthoredStaticPlacementsLogic.cs` |
| 数据加载 / 重复 id 覆盖 | `Assets/Scripts/Game.Data/JsonDataRepository.cs` |
