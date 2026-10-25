# OZX Level 数据参考（`GameData/levels/`）

- 面向读者：OZX Level Studio 的实现者
- 数据与代码快照：`ozx_base` `main@d867b644`，2026-09-11
- 增量复核：§5.1 / §5.2 / §5.3 / §5.4 / §5.5 / §5.6 与相关条目核对于 `main@f0a78923`（2026-09-29），对应 OZX-1032 / OZX-1041 / OZX-1044 / OZX-1047 / OZX-1057 / OZX-1063 / OZX-1069 / OZX-1089；其余仍是 `d867b644` 快照的实测数字。
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

`[NonSerialized]` 字段（`inlineEncounter`、`inlineLootPlan`、`guaranteedCargo`、`floorLootTableId`、`effectiveStageWeight`）是运行时合成结果，**磁盘上永远不存在，也永远不能写回**。（`guaranteedCargo` 在 OZX-1041 之前叫 `guaranteedCargoTableIds`，是一串表 id；现在是 `{itemId|tableId, count, chance}` 的列表——箱子照旧摇房间的常规表，pin 是额外支付的。）

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
| `stairLinks` | CaveLinkData[] | 楼梯——OZX-1116 起只用于往房间里钻的 basement / cave 垂直支线（规则见 §7.1）；**area 之间不再用它** | 4 |
| `exitRoomId` | string | 专用出口房，只有一扇 Up 门通往下一层 start | 0（生成器写） |
| `topRoomIds` | string[] | 朝向上一个 area 的顶边房间，供 `ConnectFloors` 选接缝门的出口房 | 0（生成器写） |
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
| `stageType` | string | `start`/`teaching`/`building`/`default`/`pressure`/`peak`/`release`/`boss`/`exit`/`story`。**缺省 ≠ default**，53/84 未填。`story`（OZX-1171）：不打架、只推进剧情的房，只在教学章手摆；和 `release` 一样没有 `encounterId` 是合法的，演出靠 `trigger` 摆放 + 遭遇的 `sound` 动作 | 31 |
| `decorations` | DecorationEntryData[] | 纯视觉，可选，见 §4.6 | 31 |
| `staticPlacements` | StaticPlacementEntryData[] | **非 cave 房必须声明**（空数组也行），见 §4.5 | 83 声明 / 28 非空 |
| `appearance` | RoomAppearanceData | 本房相对楼层主题的差异，见 §4.7 | 11 |
| `templateId` | string | 钉死 tilemap 模板，跳过 `RoomTilemapQuery` 匹配 | 8 |
| ~~`lootPlanId`~~ | string | **OZX-1381 删除**（房间清场掉落通道没了；写了被静默丢弃 → `LEVEL_FIELD_REMOVED`）。房间自己的奖品写 `guaranteedDrops`，技能等走 cargo 的 `lootTableId` | 0 |
| `guaranteedDrops` | GuaranteedDrop[] | OZX-1380：房间欠的奖品（钥匙、保底装备），由**开局波最后被击杀的那只**额外支付；要求房间有遭遇且开局波必有一具身体，见 `level_referenced_data.md` §1.6 | 4 |
| `hasTeleportSpot` | bool | 传送点 | 5 |
| `requiredForExitItemIds` | string[] | 未拾取这些 item 则门不开（对照本房实际掉落匹配） | 2 |
| `exitBlockedTip` | string | `requiredForExitItemIds` 还在地上、出口拒绝放行时 HUD 横幅显示的一行（OZX-1168，规则同 `lockedTip`，见 §4.4） | 2 |
| `isFinalRoom` | bool | 清场即通关（`SessionVictoryEvent`） | 2 |
| `roomCols` / `roomRows` | int | 复合房尺寸，0 = 1×1 | 1 |
| `roomShape` | string | `bridge` / `platform` / null | **0** |
| `bossId` | string | 楼层 boss 敌人 id | **0** |
| `seed` | int | 渲染种子，0 = 由 roomId 派生 | **0** |
| `railCart` | bool | 这间房自带一台可乘坐的轨道矿车（OZX-1159 前叫 `steamTrain`）；房间没有 rail 层却开启 = fail-fast。旧名 JsonUtility 静默丢弃 → `LEVEL_ROOM_FIELD_RENAMED` | 1 |
| `requireFullGround` | bool | 这间房只抽 ground 层没有空格的 tilemap（OZX-1265）。**硬过滤**，门掩码下一张都没有就抛。复合房（`roomCols/Rows > 1`）设了它——或所在 area 设了 `FloorData.requireFullGround`（OZX-1340）——`RoomLoadManager` 直接抛（复合拼接没有这层过滤）→ `ROOM_FULL_GROUND_COMPOSITE`；钉了 `templateId` 时它不起作用 → `ROOM_FULL_GROUND_PINNED`（Warning） | 1 |
| `requireRail` | bool | 这间房必须抽到带 rail 图层的 tilemap，好让矿车骑过去（OZX-1164）。程序化关卡由 `AssignRail` 逐房标；手写可自填。和 `railCart` 是两件事：一间房放车，每间房铺轨 | 2 |

**钉了 `templateId` 的房间跳过 rail 过滤**（`RoomTilemapSelector` / `RoomLoadManager` 按文件名直接取，不走 `RoomTilemapQuery`）。所以钉的图没有 rail 时：`railCart` → `RailCartSpawnAdapter` 抛（`LEVEL_ROOM_RAIL_MISSING`，Error）；`requireRail` → 矿车骑到这间就停（同码，Warning）。按文件名查的是**整个池子**，同名图在多个目录里（`all_start_5_01` 在 normal/ 无 rail、test/ 有 rail）时结果取决于加载顺序，编辑器只在所有同名副本结论一致时才下判断。

**钉了 `templateId` 也跳过门的全部检查**（OZX-1171，对齐 `Game.Tests/Level/PinnedTemplateDoorTests`）。`RoomTilemapQuery` 会拿模板的 `openDoors` 匹配房间的门掩码，钉图直接绕过、下游也不再问，所以下面四条在关卡 JSON 里完全看不出来，编辑器要拦（实现在 `ozxlevel/tilemaps.py`）：

| 码 | 规则 |
|---|---|
| `LEVEL_PIN_TEMPLATE_MISSING` | `templateId` 找不到任何 tilemap → 房间加载抛。出货里只有 `level_cave_demo`（ORT-45 之前的旧名，上游测试点名放行） |
| `LEVEL_PIN_DOOR_SIDE_CLOSED` | 模板 `openDoors` 必须**包含**房间每一扇门的那一侧（相等或超集） |
| `LEVEL_PIN_DOORWAY_NO_FLOOR` | 门立在所朝那条边的**中点格**（10×16：Top=(5,15)、Bottom=(5,0)、Left=(0,8)、Right=(9,8)），那一格必须有 Ground 或 Bridge，否则从这扇门进来的人落在地图外 |
| `LEVEL_PIN_DOORS_UNCONNECTED` | 门与门之间要能走通：可走 = 有地面且**既不是 Static 也不是 Pipeline**。Pipeline 是实心可破坏的墙（独立物理层），寻路网格不建模它——ch2 第一版剧情房就是这样被封死的。`exitsRun` 闸门也算，要走得到 |
| `LEVEL_PIN_PLACEMENT_IN_DOORWAY` | 钉格摆放（`cells`）跳过 `DoorClearance`，实体压在门格上就把门封了；`trigger` / `equipment` 是踩过去的，不算 |

洞穴（没有 tilemap）和复合房（门落在拼接后的网格上）不在范围内。同名模板有多份副本时，只报**每份都有**的问题。上游还有一条「`holdsGates` 的 touch 步骤不能被绕过」，需要遭遇的步骤语义，这里暂未做。

两个反直觉点：

- `bossId` 本身**不驱动刷怪**。静态 boss 房仍然需要 `encounterId`；只填 `bossId` 的房间是空房。
- `isFinalRoom` 只能手写在 `LevelData` 或 `FloorPlanData.rooms` 上，生成器不会自己设。当前所有关卡都没有胜利条件（只有 2 个房间设了它）。

### 3.4 door / cave / stair 的地位差异

一个房间的连通性来自三处，语义完全不同，编辑器不能混成一种「连线」：

| 通道 | 载体 | 范围 | 是否需要孪生 |
|---|---|---|---|
| 门 | `RoomNodeData.doors` | 同 area 相邻，**或跨 area 的接缝**（OZX-1116） | 需要（方向相反的 twin） |
| 洞穴 | `FloorData.caveLinks` | 同层任意两房，中间有一段通道房 | 单条记录描述 A↔B |
| 楼梯 | `FloorData.stairLinks` | 垂直支线（basement / cave） | 需要（对侧楼层的反向记录，见 §7.1） |

**area 不是楼层**（OZX-1116，核对于 `ozx_base@87f9ef02`）。`FloorData` 是部署单元（theme / enemyPool / stageTypes / floorLootPlan 成套的那一份），area 之间**共面平铺、普通门衔接、无缝过渡**。由此三条：

1. **`toRoomId` 在整关范围解析**——`FloorGraph.Build(LevelData)` 把所有 area 的房间放进一张扁平索引，所以 roomId 也必须**整关唯一**（撞 id 直接抛；`LEVEL_ROOM_ID_DUPLICATE` 会说出另一个 area）。
2. **跨 area 的接缝是一对孪生普通门**，和同 area 的门同一条孪生规则（`LEVEL_DOOR_TWIN_MISSING`）。旧的「跨层门必须配 stairLinks」反过来了，`LEVEL_CROSS_FLOOR_DOOR_NO_STAIR` 已删除。`toDoorId` 写了就应等于对面那扇门的方向，否则落点偏（`DoorService` 按方向找到达门，找不到只是没有入口偏置）→ `LEVEL_DOOR_ARRIVAL_MISMATCH`（Warning）。ch2 的接缝：`f0_room_1_5` Up ↔ `f1_room_1_0` Down。
3. **只有 area 0 可以有 `stageType: "start"`**——后续 area 的入口房是走进来的；而且 `start` 在 tilemap 库里只有 mask 1 的模板，回程门让它变成 mask 5，`RoomTilemapQuery` 直接抛 → `LEVEL_START_STAGE_NOT_FIRST_AREA`（Error）。改成 teaching / building。

接缝门的方向只决定小地图把下一个 area 画在哪一侧；同 area 门的几何自洽规则不约束它。

**程序化关卡也一样**（OZX-1117）：`LevelBuilder.ConnectFloors` 不再写 stairLinks，而是在出口房（`SelectCrossFloorSource`）和下一个 area 的 `startRoomId` 之间写一对 Up/Down 孪生门；生成结果里一条 stairLinks 都没有。每个接缝恰好一扇门，「本 area 从哪出去」= 指向本 area 之外、且不挂在本 area `startRoomId` 上的那扇门（`ExitStairRoomId`）。钥匙锁的是 area **内部**通往门房的门，**接缝门永不上锁**——钥匙用掉就没了，锁住唯一出口会把走回来的玩家关在里面；手写关卡锁了接缝报 `LEVEL_SEAM_DOOR_LOCKED`（Warning）。会话 dump 的可达性两种形状都走（旧 dump 仍有 stairLinks）。

**`exitsRun` 门**（`DoorLinkData.exitsRun`，第一、二章已用）：不通向任何房间，走出去即通关。不是图上的边（`LeadsToRoom` 为 false），不查目标、孪生、可达。规则：不能再写 `toRoomId`（上游 fail-fast → `LEVEL_DOOR_EXIT_HAS_TARGET`）；不该上锁（闸门本就要清房才开 → `LEVEL_DOOR_EXIT_LOCKED`）；与 `isFinalRoom` 二选一（后者清房即通关，闸门成装饰 → `LEVEL_DOOR_EXIT_WITH_FINAL_ROOM`）。

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
| `lockedTip` | string | 这扇门因缺 `keyId` 拒绝玩家时 HUD 横幅显示的一行（OZX-1168），见下 |
| `exitsRun` | bool | 走出去即通关，不通向任何房间（见 §3.4） |

`DoorDirection` 的整数编码：**`0 = Up`、`1 = Down`、`2 = Left`、`3 = Right`**。

孪生规则：A 的 `direction = Up(0)` 指向 B，则 B 必须有一扇 `direction = Down(1)`、`toRoomId = A` 的门，且两侧的 `toDoorId` 各自填对面的朝向。上例来自 `chapter_1.json`：`f0_room_0_1` 用 `Up` 连 `f0_room_0_2`，`toDoorId = Down`。

实际用量：162 条门记录、3 条锁门。其中 53 条 `direction` 与 51 条 `toDoorId` 的值是**显式写出的 `0`**（= `Up`），另有 4 条门省略了 `toDoorId`。

**`0` 是一个合法方向，不是「没填」**——这是整份数据里最容易被工具写坏的字段。任何「值等于默认就不输出」的 writer 都会把 53 扇向上的门变成 4 扇省略门加 49 处语义漂移；判断一扇门有没有 author `direction`，只能看 DOM 里键在不在，不能看 typed 值是不是 0。

**HUD tip（`lockedTip` / `exitBlockedTip`，OZX-1168，核对于 `ozx_base@5cd9b22e` 的 `RoomTips`）**：省略 = 不显示（程序化章节全是省略）。规则：

- **≤ 25 字符**（`RoomTips.MaxTipLength`，860px 横幅里一行 56pt 粗体）。超长**抛**，不截断——半句话告诉玩家去找哪把钥匙比不说更糟 → `LEVEL_TIP_TOO_LONG`（Error）。编辑器输入框直接限长。
- **写了但空白**会被上游出货数据守卫拒（`RoomTipShippedDataTests.NoAuthoredTip_IsBlank`）；不要提示就**不写这个键** → `LEVEL_TIP_BLANK`（Error，fix 是删键）。编辑器清空即删键，绝不写空串。
- 只用**拉丁字符**：`Play-Bold` / `Aldrich-Regular` 没有 CJK 字形 → `LEVEL_TIP_GLYPH_MISSING`（Warning，不抛，只是画成方块）。
- 写在永远不会拒人的地方等于死字：`lockedTip` 所在的门没 `locked`+`keyId`、`exitBlockedTip` 所在的房没有 `requiredForExitItemIds` → `LEVEL_TIP_NEVER_SHOWN`（Warning）。

写入走 patcher 的 `put`（键在就 `set`，不在就 `add` 进对象、沿用该对象的内联/多行风格），清空走 `remove`，一加一删逐字节还原。

### 4.5 StaticPlacementEntryData

非 cave 房必须声明 `staticPlacements`（可以是 `[]`）；`null` 会被各 SpawnAdapter fail-fast。没有全局兜底：没 author 的东西不会生成。

两种模式二选一：**pinned**（`cells` 非空，`count` 被忽略）或 **random**（`count > 0`，按未占用的 Static 层格子采样）。

| 字段 | 说明 | 使用 |
|---|---|---|
| `kind` | `cargo`/`oiltank`/`toxicbarrel`/`pinballhammer`/`prop`/`skill`/`device`/`trigger`/`equipment`，未知 kind fail-fast。注意 `LevelData.cs` 里那段注释的清单**漏了 `toxicbarrel`**（`ToxicBarrelSpawnAdapter` 确实认它）——kind 的权威来源是各 adapter，不是契约注释 | 39 |
| `count` | random 模式数量 | 36 |
| `lootTableId` | 仅 `cargo`：指定该箱子的掉落表 | 27 |
| `skillId` | 仅 `skill`：必填，**主动**技能 id（OZX-1400 起平台是可反复充能的主动技能站，之前发被动） | 4 |
| `charges` | 仅 `skill`：必填 int > 0，触碰平台获得 / 充满的使用次数（OZX-1400）；`buffDurationSeconds` 已无意义 | 4 |
| `footprintCols/Rows` | 占地覆盖，0 = kind 默认（oiltank/skill 2×2，device 2×3，其余 1×1）。只有 oiltank/skill/device 支持非默认 | 3 |
| `mountedEnemyId` | 挂在该物体上的敌人（目前只有 `cargo` 认）。OZX-1348 起一间房可以有**多条** count 模式 cargo 条目，各自挂 `mountedEnemyId` / `mountedCount`，放置按条目轮转（authored 顺序），格子不够时尾部少放 | 3 |
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

**`decalCategory` 的取值是封闭集合**（`DecalSurface.StampCategory` 的 case，未知即抛 → `ROOM_DECAL_CATEGORY_UNKNOWN`）：`ground_crack`、`ground_utility_by_edge`、`edge_crack`、`floor_static_2x2`、`ground_blood`，以及 BattleField 的 `ground_crater` / `ground_scrub` / `ground_gravel` / `ground_rubble`（OZX-1268，自由撒）和 `ground_stones_by_edge`（OZX-1274，按离墙距离撒）。`abandoned_cable` 在 factory 配置里有，但**不可编写**（电缆挂在设备周围，没有 count）。

**写得出 ≠ 画得出**：类目查的是房间所在楼层 `themeId` 的 `stainConfig`（`HasCategory` / `TryGetCategory`），没有就**什么都不画**，不会拿别的图顶替。所以 BattleField 那五个写在 factory 楼层上是死字 → `ROOM_DECAL_CATEGORY_NOT_IN_THEME`（Warning）。编辑器从主题资源的 `stainConfig` 读这份清单（`ThemeIndex.decal_categories`），不是硬编码「BattleField 专属」。出货里 `level_test` / `factory2_test` 的 `f0_room_1_0`（上游 README 称之为 BattleField 样例房）就落在 factory / factory2 楼层上，那 103 张贴花实际一张都不画。

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
 ├ floorDefaults : FloorPlanData（同形，见 §5.0）
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

顶层的 `waivePoolCoverage`（OZX-1366，bool，只给测试 plan 用）：为 true 时 plan 校验跳过「一局见全池子」的覆盖规则（每层要有引入房、引入容量够），编辑器的 `LEVEL_STAGE_INTRO_MISSING` 同样跳过。同票的另一半是生成算法：没有出口的楼层（终点 area 且无 boss）不再要求主干 ≥ 2 间，start + 1 间合法——那由 genlevel 体现（OLE-1）。

顶层还有一个 `skillDropLevelCap`（OZX-1069，int，与 `id` / `floorDefaults` / `floors` 同级）：**某技能到这个等级，本章就不再掉落该技能**；0 / 省略 = 默认 5。

它**不是等级钳制**。`UpgradeSkill` 没改，等级天花板仍是 `SkillData.maxLevel`；cap 只决定还能不能被**发出来**，封四条通道（普通掉落 / 箱子 / 遭遇 pin / 技能平台），**按技能封**，同一张表里其它技能不受影响。字段说明不要写成「技能等级上限」。

放在顶层是因为技能等级跨整局携带，一个在楼梯上变化的 cap 会让同一个技能在 f1 封住、f2 解封。

两条编辑期该管的：

1. **负数** —— `LevelPlanValidator` 抛。**没有「不设上限」的写法**：想让它让路就写个高过所有 `maxLevel` 的数。0 是「没写」，读成默认 5。
2. **cap 高于本章能掉的技能的 `maxLevel` 时等于没写** —— 这条最容易困惑，值得提示。判据：本章 `floors[].floorLootPlan.probabilistic` 里 `ItemData.type == "skill"` 的条目，逐个解析成它装的那份 `SkillData`（`ApplySkill` 交给 `AddPassive` 的是 **item 自己的 id**，`refId` 只是创作期的回退），取 `maxLevel`。

### 5.00 areaLinks（OZX-1185 / OZX-1186，核对于 `ozx_base@5cd9b22e` 的 `AreaLinkPlan.Build`）

plan 顶层（不在 floor 上）可选的 `areaLinks: [{ fromArea, toArea, kind, keyId?, stairRoomsMin?, stairRoomsMax? }]`，声明 area 怎么连。**不写 = 老行为**：i → i+1 的接缝门链，钥匙来自各楼层的 `transitionKeyId`。

- `kind: "door"`：共面接缝（OZX-1117 的那对孪生门），可带 `keyId`（钥匙掉在 fromArea 里）；`kind: "stair"`：孪生 stair link，进出 basement，可在 fromArea 的 `stairRoomsMin..Max` 间不同房各开一道（都省略 = 1）。
- basement **没有新字段**：effective `themeId` 为 `themes/basement` 即是（生成房 `roomCategory: basement`，只抽 `TilemapData/basement/`）。
- 终点 area = 没有连接离开的那个，不再是最后一个下标（ch5 的 boss 在下标 1）。

`Build` 抛的每一条编辑器都报（读**合并后**的楼层，因为 basement 判定要看 effective `themeId`）：

| 码 | 规则 |
|---|---|
| `LEVEL_AREA_LINK_OUT_OF_RANGE` / `_SELF` / `_INTO_START` / `_DUPLICATE` / `_KIND_UNKNOWN` | 下标越界、自连、连**进** area 0（回程总会自动造，从 0 往外写）、同一对 area 连两次、kind 不是 door/stair |
| `LEVEL_AREA_LINK_FIELD_MISPLACED` | door 写了 stairRooms；stair 写了 keyId（楼梯运行时没有锁，只按清房开） |
| `LEVEL_AREA_LINK_STAIR_ROOMS_INVALID` | `stairRoomsMin ≥ 1` 且 `max ≥ min`，或都省略 |
| `LEVEL_AREA_LINK_STAIR_LEVEL` | 楼梯两端 `elevation` 相同（没有方向）；或 basement 一端不比地面一端低（两头都会显示反了）。OZX-1338 起这条看 elevation，不再看下标 |
| `LEVEL_AREA_LINK_STATIC_FLOOR` / `_FLOOR_KEY` | 有 areaLinks 时不许有 `static` 楼层，也不许楼层写 `transitionKeyId`（钥匙只写在门连接上） |
| `LEVEL_AREA_LINK_FAN_OUT` | 每个 area 最多 1 条出门、1 条进门、1 条出楼梯；只靠楼梯离开的 area，那道楼梯只能 1 间房 |
| `LEVEL_AREA_LINK_LANDING_NEEDS_DOOR` | 多间落点的楼梯，目标 area 必须有门连接离开 |
| `LEVEL_AREA_UNREACHABLE` | 每个 area 都要能从 area 0 沿连接（双向）走到 |
| `DATA_REFERENCE_MISSING` | door 的 `keyId` 不是 ItemData |

**关卡侧的 stairLinks**（`LevelValidator.ValidateStairLinks`，加载即抛，编辑器之前完全没查）：`roomIdA` 在本层（`LEVEL_STAIR_ROOM_NOT_ON_FLOOR`）、`targetFloor` 是另一层（`LEVEL_STAIR_TARGET_INVALID`）、对面层有孪生（`LEVEL_STAIR_TWIN_MISSING`）、两层 elevation 不同（`LEVEL_STAIR_NO_DIRECTION`）、**一间房只有一个楼梯端**（OZX-1188，`PlaceStairs` 会跳过第二道，那条就成了单程；`themeId` 含 `cave` 的楼层豁免 → `LEVEL_STAIR_ROOM_TWO_ENDS`）。

### 5.0 floorDefaults（OZX-1011，核对于 `ozx_base@5cd9b22e`）

`LevelBasePlanData.floorDefaults` 是**与 `floors[]` 元素同形**的章节级默认楼层：楼层没写的字段从这里取，写了就覆盖。chapter_3–7 都用了它（chapter_3 的 `themeId` / `enemiesPerRoom` / `appearance` / `stageWeights` / `categoryCaps` 等只写在这里），**所以直接读 `floors[f]` 看到的是「只剩差异」的楼层**——这是 OLE-9 之前 chapter_3 在编辑器里「打不开」的原因。

合并规则只有一份实现：`Game.Level/FloorPlanDefaults.cs`（`Merge` / `Validate`），编辑器这边是 `ozxlevel/plandefaults.py`，逐条对齐：

| 层级 | 规则 |
|---|---|
| 楼层的每个字段 | 各自独立继承 |
| `floorLootPlan` | **进去一层**，各列表独立继承；`probabilisticPresets` 跟着 `probabilistic` 走（上游先展开 preset 再合并，所以楼层写了任一个就两个都用楼层的） |
| `treeShape` / `appearance` / `elitePlan` | **整块**，按各自判别字段：`trunkLengthMax > 0` / `tilePartConfigs` 非空 / `perStage` 非空 |
| 任何数组 | 覆盖，**永不追加** |
| `railCart` / `requireFullGround` | bool 没有「没写」：楼层只能打开，不能关掉章里打开的 |
| `endsRunOnClear` / `elevation` / `rooms` | **永不继承**；写在默认里 `Validate` 抛 → `LEVEL_FLOOR_DEFAULT_REFUSED` |

「写没写」**从值读，不从键读**：0 / 空串 / 空数组 / null 都是「没写」（JsonUtility 把省略读成这些）。代价：楼层不能把继承来的值改回 0 或空。

编辑器的约定：

- 校验跑在**合并后**的楼层上；一条 finding 如果落在继承来的字段上，指针改指 `/floorDefaults/...`，三层同一个错只报一次，消息里列出继承它的楼层。
- plan 视图的每层带 `inherited`（继承来的字段名），指针一律指向**真正存值的节点**；`floorDefaults.fanOut` 给出每个默认值被几层继承——改它会影响几层。
- 楼层卡片上继承来的值置灰并标 `default`；章节默认值单独一张卡。继承来的 tile groups **不能**在楼层卡上点改（那会改到所有继承的楼层），要在默认值卡上改。
- 「回到继承」= 删掉楼层上的键，不是写 0；保存时不要写出等于缺省值的键。

### 5.1 FloorPlanData

18 个 floor plan 中的使用情况：

| 字段 | 说明 | 使用 |
|---|---|---|
| `roomCountMin/Max` | 房间数区间，编译时随机取 | 18 |
| `themeId` | 楼层主题 | 18 |
| `stageTypes` | stage 比例 + 每 stage 敌人预算，见 §5.2 | 18 |
| `enemyPool` + `enemiesPerRoom` | 敌人名册。**设了 `enemyPool` 就必须给 `enemiesPerRoom > 0`**，否则 fail-fast。条目可另带 spawner 通道，见 §5.4 | 12 |
| `floorLootPlan` | 楼层掉落方案，见 §5.3 | 12 |
| `transitionKeyId` | 过渡钥匙 item id；编译期自动绑到 peak/跨层流程并插锁门 | 10 |
| `generatorType` | `tree`/`cycle`/`mixed`/`static`；**缺省和未知值都静默回退 `tree`** | 9 |
| `stageDecorations` | `[{ stageType, decorations: [{ decalCategory, count }] }]`（OZX-1291）：生成期按房间最终的 `stageType`，把该条的 `decorations` 拷到 `decorations` 为空的非 cave 生成房上（start / boss 也算）。缺 `stageType` 或同一 stage 写两次 `AssignStageDecorations` 抛（`LEVEL_STAGE_DECORATION_UNSCOPED` / `_DUPLICATE`）；每条贴花与手摆的同规则，按楼层 effective `themeId` 判能不能画（§4.6）。数组整体替换 | ch3 / ch4（floorDefaults） |
| `spawnerMinionsPerRoom` | int（OZX-1219）：spawner 产线的独立额度，按房共享。0 = OZX-1064 的规则（产线占 `enemiesPerRoom`、受 minion 条目 `maxPerRoom` 约束）；> 0 = 产线只花这份额度，`enemiesPerRoom` 只数直接放置、minion 的 `maxPerRoom` 只管直接放置，每座 spawner 取 `minionsPerSpawner`、被剩余额度截断、一只都给不出就不放这座。可从 floorDefaults 继承。`LEVEL_SPAWNER_LINE_ALWAYS_TRUNCATED` 据此改判：有额度时比的是额度，不是 minion 的 cap | 8（ch5–7） |
| `elevation` | int（OZX-1338）：area 的高度——地面 0、basement −1、楼上 +1。楼梯朝向**只**看两端高度（目标更高 = UpStairs 1×3，更低 = DownStairs 1×1），与 area 下标无关；两端同高抛。**永不继承**（写进 floorDefaults 抛）。LevelData 的 `FloorData.elevation` 同义，`ValidateStairLinks` 用它 | ch4–ch6 |
| `requireFullGround` | bool（OZX-1340）：本 area 每间房只抽 ground 无空格的模板，精确门型没有时回退到满地图的超集（最坏四门 TBRL）。所以**每个会发的 stage 都要有一张满地图四门模板**（`ValidateFullGroundTilemaps` → `LEVEL_FULL_GROUND_TILEMAP_MISSING`）。bool：楼层能打开不能关掉 | ch6 的两个 battlefield area |
| `railCart` | bool（OZX-1164）：本层跑在轨道上——每间房标 `requireRail`、起始房标 `railCart`。bool 无「没写」：楼层能在章节默认之上打开，不能关掉 | 2（ch4/ch5 的 basement） |
| `defaultStaticPlacements` | 兜底静态摆放，只作用于「生成后 staticPlacements 仍为空」的房间。`chance` / `countMin` 见 §5.6 | 9 |
| `floorWidth/HeightMin/Max` | 显式楼层尺寸，0 = 由房间数推导。cycle：height 是环高（1 = 平排，≥3 = 完整环）；tree：只有 floor 0 的 height 有意义 | 4 |
| `caves` | `{linkType, encounterId}`（`lootPlanId` 已删，OZX-1381） | 1 |
| `bossId` | 楼层 boss，合成为单敌人 inline encounter | 0（字段写了 null） |
| `stairs` | `{targetFloor, linkType, encounterId}` | 0 |
| `rooms` | 预定义房间；`generatorType="static"` 时必填，否则 fail-fast | 0 |
| `overflowStageType/EncounterId/LootPlanId` | 比例分配后剩余房间的兜底。默认 `pressure` / `loot_common` | 0 |
| `useExitRoom` | true = 合成专用 exit room（单向 Up 门） | 0 |
| `endsRunOnClear` | 本层清完即通关。只有**终点 area** 可以写（无 areaLinks 时是最后一层；有时是没有连接离开的那个，`LevelBasePlan.IsFinalArea`），否则生成期抛（`LEVEL_ENDS_RUN_NOT_FINAL_AREA`）。有 `bossId` → `isFinalRoom` 盖到 boss 房；**没有 `bossId`**（OZX-1442）→ 盖到按 BFS 序的**最后一间 peak 房**（离入口最远，不用 rng）。这时 `ValidateFinalPeak` 要求：列了 peak、peak 在 `roomCountMin` 下按最大余数法至少分到 1 间（`PortionResolver`，编辑器按单精度逐步复刻）、peak 的 `enemyBudgetCut < 1`（空房一进门就通关）、peak 的 `secondWave` 只能 `cleared`（`advance` 可能还在等，而第一次平静就是胜利）→ `LEVEL_FINAL_PEAK_INVALID`。终点房不承接 `guaranteedDrops`、不放传送点。不能从 floorDefaults 继承 | ch3–ch7 各一层 |

**每个会发的 stage 都要有 tilemap**（`LevelPlanValidator.ValidateStageTilemaps`）：area 的库 = 主题的 category（`themes/X` → `X`，没有 tilemap 认领就退回 `normal`；没写 `roomCategory` 的 tilemap 是通配），`stageType` 为 `all` / `none` / 空的模板服务任何 stage。plan 列出的每个 stage、overflow stage（默认 `pressure`）、area 0 的 `start`、有 `bossId` 的 area 的 `boss`，库里一张都没有 → `RoomTilemapQuery` 在玩家进门时抛 → `LEVEL_STAGE_TILEMAP_MISSING`。编辑器维护一份按文件 stat 戳失效的 tilemap 事实索引（`ozxlevel/tilemaps.py` 的 `TilemapIndex`）。

### 5.2 StageTypePlanData

`{ stageType, portion, encounterId, enemyBudgetCut?, secondWave? }`（`lootPlanId` / `overflowLootPlanId` 随清场通道删除，OZX-1381 → `LEVEL_FIELD_REMOVED`）。`portion` 是相对比例（不必和为 1，实现按总和归一）。

**`enemyBudgetCut`（OZX-1089，float [0,1]，省略 = 0）是「还回去多少」，不是「乘几」。** 这个方向是刻意的：`JsonUtility` 把省略的 float 读成 0，所以一个「0 = ×1」的系数会把作者写的 `0`（「这里不要敌人」）读成「拿满预算」——正是这个字段要修的那个 bug 换一层。写成 cut：省略 = 0 = 什么都不改，而危险的那个值（1 = 完全不刷敌人）必须手打。

| 值 | 含义 |
|---|---|
| 0 / 省略 | 不还，拿满 |
| (0, 1) | 还回这个比例；**结果不足 1 向上取到 1**——「很少」不许静默变成「没有」 |
| 1 | 这一 stage 不刷敌人；生成器**根本不生成 `inlineEncounter`**（不是生成一个空 `steps` 的，那会被 `EncounterValidator` 拒绝，而被拒的 encounter 永不刷怪、永不清场，门就永远锁着） |
| 区间外 | `StageEnemyBudget.Validate` 抛 |

`budget = round_half_away(enemiesPerRoom × (1 − cut))`，再 `max(1, ...)`；注意是**远离零取整**，Python 的 `round()` 是银行家舍入，0.5 会落到另一个整数上。plan 没列的 stage（尤其 overflow stage）取 cut = 0。

**编辑器 UI 按「省略 = 不改」呈现，不要给它一个可见的 1.0 默认值。**

另有一条校验：cut 满的 stage 同时写了 `encounterId` → 拒绝。有 `enemyPool` 的楼层上，作者写的 `encounterId` 一直是死文本（生成器会用采样出的 `inlineEncounter` 覆盖它）；满 cut 让生成器跳过这间房，于是那段死文本**复活**，用一个手写 encounter 去填「说好不要敌人」的那一 stage。

**`secondWave`**（OZX-1354，核对于 `ozx_base@5cd9b22e`）：`{ chance, share, triggers: [{ kind, weight, distance? }] }`，开局波之上**加**一波。每房按 `chance` 掷一次；第二波人数 = `ceil(share × 该 stage 每房预算)`；`kind` 是 `cleared`（清场后跟进）或 `advance`（离进门落点 ≥ `distance` 世界单位时伏击；OZX-1353 把早先的 `depth` 换成了 `distance`）。写了任何字段即「已声明」（JsonUtility 会实例化省略的块）。`ValidateSecondWaves` 的规则编辑器全查：`chance` / `share` ∈ (0, 1]，triggers 非空、`weight > 0`、`advance` 要 `distance > 0`、`cleared` 不能写 distance、kind 只有这两种、残留的 `depth` 报错（`LEVEL_SECOND_WAVE_INVALID`）；楼层没有 enemyPool（`_NO_POOL`）；stage 预算全砍（`_NO_FIGHT`）；**容量**：只有能晚到的类型（`emergeDuration > 0` 且 `spawnPlacement != "initial"`，变体组要每个成员都能）才能填，最多取该 stage 的种类上限（teaching 2 / building 3 / pressure 4 / peak 5）个、按 `maxPerRoom` 从大到小（0 = 不限），装不下所需人数上游**抛**而不是放一波缩水的（`LEVEL_SECOND_WAVE_CAPACITY`）。spawner 产线不计入。

### 5.3 FloorLootPlanData

| 子结构 | 字段 | 语义 |
|---|---|---|
| `probabilistic[]` | `itemId` **或** `tableId`, `weight`(0=1.0), `minCount`/`maxCount`(0=1), `floorCap`(0=无限) | 采样成每房的 `inlineLootPlan`，并合成楼层表 |
| `probabilisticPresets[]` | `loot_presets.json` 的组名 | 加载期前置到 `probabilistic` 前面（OZX-1012，OLE 尚未展开——见 OLE-10） |
| `guaranteed[]` | `itemId` **或** `tableId`, `count`, `chance`(0=必出), **二**选一绑定：`boundEnemyId` / `boundStage`（~~`boundRoomId`~~ OZX-1380 删、~~`boundWaveLastEnemy`~~ OZX-650 删 → `LOOT_GUARANTEED_BINDING_DEAD`） | **必须且只能设一个绑定**；落点 = 选中房间开局波最后被击杀的那只（追加到该房 `guaranteedDrops`） |
| `categoryCaps[]` | `category`（taxonomy 节点）, `maxPerFloor` | 每层每类上限 |
| `stageWeights[]` | `stageType`, `category?`, `weight` | 按 stage 缩放 `probabilistic`；缺省 1.0，0 = 该 stage 不掉，负数非法。`category` 见 §5.5 |
| `stageMustDrop[]` | `stageType`, `drops[{itemId` **或** `tableId, count, chance}]` | 该 stage 的**每个房间**各生成一个 cargo；`chance`(0/省略=必出) **逐房**在生成期掷（OZX-1109） |

真实样例见 `level_10.json` 的 floor 1：4 条 probabilistic（两把枪各 `floorCap: 1`）、`weapon` 类每层上限 2、stage 权重 start/boss 归零、release 必掉一个中血包。

**`itemId` / `tableId` 二选一**（OZX-1043 / OZX-1047）：`tableId` 指的表必须 `pickOne: true` 且非空（`LootTableDraw.Require`）。`probabilistic` 两个都不写只是一条永远掉不出东西的行；`guaranteed` 两个都不写或都写会抛（`hasItem == hasTable` 即拒）。

**这一块的引用边几乎全是静默失败，编辑器要全查**：

| 边 | 漏了会怎样 |
|---|---|
| `probabilistic[].itemId` | `RegisterScaledTable` 把条目**原样**搬进合成表的 `LootEntryData`，从不查 id。表照常工作，只是永远少掉一样东西，零报错 |
| `guaranteed[].itemId` / `.tableId` / `.boundEnemyId` / `.boundStage` | 生成期抛（`LevelBasePlanAssigner`）——抛得对，但抛在跑生成器的时候 |
| `categoryCaps[].category` | 不是 taxonomy 节点 → 匹配不到任何道具，这条 cap 读起来像调过、实际什么都没做。**注意 `categoryCaps` 至今没有 fail-fast**，而同样的问题 OZX-1044 给 `stageWeights.category` 加了一条 |
| `stageMustDrop[].drops[].itemId` | 同 probabilistic |
| `stageMustDrop[].drops[].tableId` | 同 guaranteed：二选一、`pickOne` 且非空，生成期抛（OZX-1109） |

**无敌人的 stage 的保底只能走 `stageMustDrop`**（OZX-1109）。`enemyBudgetCut: 1` 的 stage 一个 launch 都没有，而 guaranteed 挂在 launch 上：`guaranteed.boundStage` 指向它会被 `ResolveStageRoom` 抛（`LevelBasePlanAssigner.cs`，「a stage with no fight pays its guarantees through stageMustDrop」）——编辑器报 `LOOT_GUARANTEED_STAGE_NO_ENEMIES`（Error）。`probabilistic` **没有**死：stage 缩放后的表作为 `floorLootTableId` 盖在**每一间**房上（`LevelBasePlanAssigner` 的 OZX-473/477 段），`CargoSpawnAdapter.cs:121` 用它摇房里的箱子——死的只是尸体那一半，所以 release 的 `stageWeights` 是在调箱子，不报。`stageMustDrop` 落的是箱子不是尸体，所以它是 guaranteed 的替代。

`stageMustDrop[].drops[].chance` 的 0 = 必出是刻意的：`MustDropItemData` 是 `[Serializable]` 子配置，`JsonUtility` 把省略的 float 读成 0，「省略 = 必出」只能这么表达；「永不掉」无法表达也是刻意的——删条目即可。区间外 `LOOT_MUST_DROP_CHANCE_INVALID`，空 `stageType` 上游抛，报 `LOOT_MUST_DROP_UNSCOPED`。

`boundRoomId` 已被 OZX-1380 删除：房间由生成器选（能承载的房间 = 开局波必有身体的那些）。

`category` 存不存在要查**节点表**，不能用「它底下有没有道具」判断：中间节点自己不直接挂道具也是合法节点（上游为此专门有 `HasCategory`）。

### 5.4 EnemyPoolEntryData（OZX-1032 spawner 通道 + OZX-1063 变体组与成簇）

`{ enemyId, weight(0=1.0), maxPerRoom(0=无限), dropTableId?, minionId?, minionsPerSpawner?, variantIds?, clusterSizeMin?, clusterSizeMax?, clusterRadius?, sharedRoot?, fanArcDegrees? }`。

#### 5.4.0 变体组与成簇（OZX-1063，核对于 `ozx_base@f0a78923`）

| 字段 | 语义 |
|---|---|
| `variantIds` | `enemyId` 的其它形态。整条条目**按一种类型**采样 / 计数 / 限量，每具身体的具体 id 在放置时**各自等权抽一次**（独立、允许重复） |
| `clusterSizeMin` / `clusterSizeMax` | 成簇大小。0/1 = 不成簇 |
| `clusterRadius` | 簇内环半径；`clusterSizeMax > 1` 时必填（`sharedRoot` 除外） |
| `sharedRoot` / `fanArcDegrees` | OZX-1191：同根一丛（触手）。全部成员**落在锚点上**，沿「锚点 → 房间中心」轴在 `fanArcDegrees` 的扇形里均分朝向。规则即 `EncounterValidator.SharedRootProblem`（pool 条目与 launch 同一套）：`fanArcDegrees ∈ (0, 360]`、`clusterSizeMax > 1`、不能再写 `clusterRadius`（环和同根是反义）、不能走 `viaSpawner`；没 `sharedRoot` 写了 arc 也报 → `CLUSTER_SHARED_ROOT_INVALID`。出货：`spawn_tentacles`、`tentacle_short/medium/long` |

同样四个字段也在 `EncounterData` 的 `steps[].action` 上——采样器把条目的值抄到它合成的 step 上。

`enemyId` **仍是代表**：刷怪层、footprint、`placementJitter` 全读它，`variantIds` 里不能重复列它（列了会抛）。

**折叠不是重构，它改了整层的采样构成。** `PoolSampler` 数的是**条目**，coverage tier（OZX-1010）先给每个条目发一只——三条折成一条，植株那份保证从 3 只降到 1 只，腾出的名额落到别的类型上。chapter_3 floor 1 条目数 9 → 7，12 个名额里 coverage 吃掉 7 个（原来 9 个）。实测全房敌人 HP +7.4%，`ground_turret` 每房 1.42 → 1.61，植株每房均值 3.58 → 3.38 但 **p95 从 6 株变 8 株**（合并后的 `maxPerRoom` 是三者之和，把原来分散在三个上限里的余量集中到一个条目上）。stage 的 distinct 记账里一个变体组也只占 1 个名额，`ValidatePoolCoverage` 从此承诺的是**这个组**而不是每个变体。

成簇是放置层的事：`EncounterActionExecutor` 把一条 launch 的身体分成若干簇，每簇一个锚点，其余成员落在 `[radius/2, radius]` 的环上，**溢出另起一簇**（5 具 = 3+2，不是一道 5 具的墙）。**簇内刻意豁免最小间距**，簇与簇之间照旧遵守——所以 `clusterRadius` 不是装饰，它是唯一还在把身体撑开的东西。

校验（`LevelPlanValidator` / `EncounterValidator`，编辑器前移）：

- 变体：空字符串 / 重复代表 / 不存在的 enemy id / **role 与代表不同**（整组只占一层刷怪格，取自代表；role 这条只在 pool 上，action 上没有可取代表的池子）。
- 成簇：`clusterSizeMax > 1` 而 `clusterRadius <= 0`；写了半径没写簇大小；`min > max`；**spawner anchor 同时要成簇**（minion 走 channel，落在 spawner 自己的发射锚点上，从不读这些字段）。
- `viaSpawner` 的 launch 不许带 `variantIds` 或成簇字段。

⚠️ 契约注释说 `LevelPlanValidator` 同时强制「变体各自带 elite 块」，实际代码里只有 role 一条；elite 那半没实现（核对于 `f0a78923`）。

#### 5.4.1 spawner 通道（OZX-1032 / OZX-1064，核对于 `ozx_base@f0a78923`）


**`category: "spawner"` 的敌人自己什么都不产。** `EnemyEmitController` 只排干「encounter 通过 `EncounterAction.viaSpawner` 塞进去的请求」，`produceIntervalSeconds` 只是排干的节奏。手写 encounter 一直是把 spawner 和它的 minion step 成对写的；程序化楼层过去没法说出后半句，所以丢进 `enemyPool` 的 spawner 出来就是一具血厚如墙的哑火躯体。这两个字段就是那后半句：

| 字段 | 语义 |
|---|---|
| `minionId` | 这台 spawner 产什么。非空 = 本条目是 spawner 锚点。**必须是同层 `enemyPool` 的另一个成员** |
| `minionsPerSpawner` | 一台 spawner 喂几只；乘以本房抽到的该 spawner 只数 |

**两者必须同时非空或同时缺省**，旧 plan 不写这两个字段，行为不变。

⚠️ **OZX-1064 起这条线是「请求」不是「承诺」**：它被计入 `enemiesPerRoom`（那个数字现在是这间房的**全部**身体数，两条通道合计），并且受 **minion 自己的 `maxPerRoom`** 限制——minion 是同一种敌人走第二道门进来的。谁先见底谁就截断这条线：房间少产几只，spawner 照样出现；只有连一只都放不下才不放这个 spawner。所以 `minionsPerSpawner` 写得比 minion 的 `maxPerRoom` 大，就是一条**每次都被截断**的线（chapter_3 floor 2 就是 6 vs 4）。实测每房敌人数 f0 13.59 → 11.06、f2 15.49 → 11.30，`jammer` 单房最多 8/10 → 4。合成遭遇的 `maxAlive` 不变，仍只数直接放置的身体。

采样时 `PoolSampler` 为每台抽到的 spawner 合成一条 `viaSpawner` step：

```jsonc
{ "id": "jammer_via_evil_ground_factory",
  "action": { "verb": "launch", "enemyId": "jammer", "min": 6, "max": 6,
              "viaSpawner": "evil_ground_factory" } }
```

预览要跟上的三条语义：

- 这些 minion **不计入** `enemiesPerRoom`，也**不计入** encounter 的 `maxAlive`（后者只数直接放置的）。有工厂的房间实际身体数是 `enemiesPerRoom + minionsPerSpawner × n`。
- **spawner 条目不参与 elite round-robin**（它产的 minion 参与——`viaSpawner` 通道端到端带着 `eliteCount`），所以预览里工厂的 `eliteCount` 恒为 0。提拔 spawner 会缩放它的 root，把 SpawnPoints 的发射锚点和血条一起拖走。
- 合成出来那条 step 的 `dropTableId` 来自 **minion 自己的** pool 条目，不是 spawner 的——死的是 minion。
- pin 不能骑在 `viaSpawner` 的 launch 上（§1.6）——通道请求可以被合法丢弃。

`LevelPlanValidator.ValidateSpawnerPairs` 的四条，每条挡的都是静默或半路抛的失败：

1. 只写了一半 → 按普通敌人采样，工厂哑火站着（**无报错**）。
2. `minionId` 不是同层 `enemyPool` 成员 → 绕过池成员身份的三重保证（一次 run 见得到、role 在本层每张 tilemap 上都有生成格、带 elite 块），半路 `EnemyService.Spawn` 抛错。
3. 不能产自己 → 无界增长，`maxAlive` 门的是整条 step，管不到通道里面。
4. 不能产另一台 spawner → 链式生产没有人口上界。

### 5.5 stageWeights 的 category 维度（OZX-1044，核对于 `ozx_base@c87c6236`）

`stageWeights[]` 从 `{ stageType, weight }` 变成 `{ stageType, category?, weight }`。`category` 是 `item_taxonomy.json` 的节点名，**按子树匹配**（写 `equipment` 覆盖 `weapon` / `core` / `drive`）；留空 = 该 stage 的兜底倍率，也就是这个字段出现之前每一条的含义，向后兼容。

**这才是这个字段存在的理由**：光有 stageWeights 只能说「peak 掉得多」，说不出「peak 掉更多**装备**」。stage × 品类这个叉积是四个 stage 旋钮里唯一没人能表达的——`categoryCaps` 有品类没 stage 而且是上限，`room_drop_caps` 两者都有但只管 `equipment` 且是降级不是导向，`stageMustDrop` 一次只钉一个具名道具。

```jsonc
{ "stageType": "peak", "weight": 1.4 },                          // 兜底：什么都 ×1.4
{ "stageType": "peak", "category": "equipment", "weight": 1.8 }  // 装备另外 ×1.8
```

**对预览的影响**：`WireFloorLootToSpawnEntries` 在 `stageWeights` 非空时**本来就为每个 stage 合成一张表** `loot_floor_f{n}_{stage}`（OZX-477 起），变的是 `RegisterScaledTable` 从「整表乘同一个标量」改成「逐条目按 taxonomy 品类查倍率」。**所以同一张合成表内，不同条目的 weight 不再等比**——凡是按「整表等比缩放」做过反推的地方，现在都对不上。

解析顺序（`StageWeightResolver.Resolve`，逐个道具）：

1. 该 stage 下、**道具自己所属品类**的条目；
2. 逐级向上，每个祖先品类各试一次（`core_cinder_reactor` → `core` → `equipment`）；
3. 该 stage 的**无品类**条目；
4. 都没有 → 1.0。

最具体的优先。这条顺序是会静默出错的地方：挑错条目照样产出一个数字，表看起来正常，只有掉落手感不对。

**三条 fail-fast**（`StageWeightResolver.Build`，编辑器前移为 Error）：

- 负权重；
- `category` 不是 taxonomy 节点——拼错的名字匹配不到任何道具，这个 stage 会无声地退回兜底权重；
- 同一个 `(stage, category)` 写了两条——输家永远不会生效，谁赢取决于书写顺序。

**注意 `effectiveStageWeight`**：品类一旦参与，一个 stage 就没有单一数字了。HUD 报的是该 stage 的**无品类兜底值**（`StageWeightResolver.StageFallback`），也就是「没被特别圈定的东西的倍率」——不要把它当成整个 stage 的倍率显示。

**「没写 category 的 plan 永远不查 taxonomy」是刻意的闸门**：这样一份早于本字段的 plan 不会因为 taxonomy 没收录某个道具而突然开始抛错。

### 5.6 StaticPlacementEntryData 的 `chance` 与 `countMin`（OZX-1057，核对于 `ozx_base@f0a78923`）

`count` 一直是**上限**：random fill 摆得下几个摆几个。这两个是长在它两端的字段。

| 字段 | 语义 |
|---|---|
| `countMin` | 这条 entry **最少**放几个。0 / 1 / 省略 = 无下限（此字段之前的行为）；`>= 2` 时放不到就**一个都不放**，格子让给后面的 adapter |
| `chance` | 这条 entry **整条**出不出现，(0, 1]，0 / 省略 = 必出 |

**`chance` 整条掷一次，不是每个物件掷一次。** `count: 3, chance: 0.2` 是「这间房要么是个有三个 pod 的培养舱，要么是间普通房」，不是「每间房都有 0.6 个 pod」——后者会让 set-piece 读起来像杂物，正是这个字段要避免的分布。判定在 `AuthoredStaticPlacementsLogic.EntryAppears`，用**房间的 seeded `IRandom`**，同房同 seed 可复现。

**这两个字段今天只写在 plan 的 `defaultStaticPlacements` 上**，因为程序化楼层没有手写房间可以挂。chapter_3 三层的 pod 都是 `{count: 3, countMin: 2, chance: 0.2, placement: "sidewall"}`。

校验：

- `chance > 1` → 抛（`ValidateChances`）。
- `chance` 写在不读它的 kind 上 → 抛。`ChanceHonouringKinds` 现在只有 `prop`。
- `countMin > count` → **引擎不管**，但等于永远不放。
- ⚠️ **`countMin` 比字段注释说的还要窄**：注释说「只对 random fill 有意义」，实际只有 `placement: "sidewall"` 那条分支把它传给 `SideWallPlacementLogic.ChooseAnchors`；旁边那条普通 random fill 读了就丢。pinned `cells` 同样不读。

---

**更新（核对于 `ozx_base@5cd9b22e`）**：读 `chance` 的 kind 现在是 `prop` 和 `groundlaser`（OZX-1377，`ChanceHonouringKinds`）；`countMin` 除了 prop 的 `sidewall`，还被两个 kind 当作**数量区间**的下限读：`toxicbarrel`（OZX-1226，`RollRangedCount`，`[countMin, count]` 均匀、每条掷一次）和 `groundlaser`。

**`groundlaser`**（OZX-1371，选点 OZX-1373）：固定 12 格（列 {2, 7} × 下组行 {2, 3, 6} / 上组行 {9, 12, 13}，10×16 房），左右成对，优先玩家进门一侧（上门 → 上组，下门 → 下组，侧门或非门进入 → 下组），两台间距须 > 2 格。只读 `countMin` / `count`（台数，缺省 2 / 4，**必须落在 2–4** 且区间里有偶数——票里写的 2–6 已过时）和 `chance`；写 `cells` / footprint / `placement` 都抛（`GroundLaserPlacementLogic.ResolvePairRange` → `ROOM_GROUNDLASER_INVALID`）。ch3–ch6 每层默认 `chance: 0.3`。

## 6. plan 如何编译成 level

**编辑器里看样本（OLE-1）**：plan 视图的「Generate a sample」调用 ozx_base 自己的 headless 入口（`DotnetTests/Game.Balance` 的 `genlevel --plan <id> --seed <n> --gamedata <dir>`，OZX-991），把返回的 `LevelData` 走普通关卡视图画出来（只读，带可达性叠加）——不在 Python 里重写生成器，所以传送点、间距、stage 发牌、采样都和游戏一致。**一次只是一个样本**：样本条上写着 SAMPLE 和 seed，「Compare 5 seeds」并排给出各 seed 的房间数 / 每层 / 传送点，看的是分布不是某一次。GameData 是实时读的；C# 改了要重建 DLL，编辑器在 DLL 比 `Assets/Scripts` 里（Game.Unity / Tests 之外）最新的 `.cs` 旧时明确提示 stale 和重建命令。生成失败时原样显示 genlevel 的报错——plan 生成不出来是 plan 的问题。


程序化关卡的作者输入是规则，房间是编译产物。编辑器要展示「输入 → 指定 seed 的结果」，就得复刻这条顺序（`LevelBasePlanAssigner.Apply`，逐楼层）：

```
AssignStageTypes → AssignEnemyPools → AssignLootPools → AssignBossId → AssignFinalRoom
→ AssignStaticPlacements → InsertCaveLinks → InjectGuaranteedDrops → InjectStageMustDrops
→ RegisterFloorCaps                                     （所有楼层完成后）→ InsertLockedDoors
```

几个必须知道的细节：

1. **stage 按 BFS 顺序分配**（`OrderRoomsByBFS`，从 start 房开始），比例用**最大余数法**（`PortionResolver`）换算成整数房间数，保证总和等于房间数。
2. **stage 赋值会覆盖房间的 `encounterId`**——包括把它覆盖成 `null`。在 plan 驱动的楼层里，`FloorPlanData.rooms` 上手写的 encounter 会被 stage 条目盖掉。
3. 比例分配后剩下的房间走 overflow，默认 `stageType = pressure`。
4. enemy pool 先合成 `inlineEncounter`；只要 floor 的 `probabilistic` 非空，随后合成的楼层表就会覆盖这些 action 的 `dropTableId`——**enemy pool entry 自带的 `dropTableId` 因此可能不生效**。
5. `guaranteed` 条目与过渡钥匙追加到选中房间的 `guaranteedDrops`（OZX-1380），由开局波最后被击杀的那只支付，**不碰任何 `dropTableId`**——所以第 4 步和第 5 步之间没有写入顺序依赖。无开局身体的房间（含 release）不能被选中，生成时抛。
6. 编译产物落在 `[NonSerialized]` 字段上（§2），因此源 json 无法解释「这个 seed 下这个房间到底有什么」。这正是工具要提供 Effective 视图的原因。

---

## 7. 运行时已经在强制的校验

这些规则今天由运行时 fail-fast 或静默失败兜底；编辑器的价值就是把它们提前到编辑期。

### 7.1 楼梯孪生（`LevelValidator.ValidateStairLinks`，启动即校验）

硬规则，违反即抛异常（OZX-1188 / OZX-1338 后又加了两条，全表见 §5.00 末尾）：

1. `roomIdA` 必须是**本层**房间。
2. `targetFloor` 必须在 `[0, floors.Length)` 且**不等于本层**。
3. 目标楼层必须存在反向孪生：`roomIdA'=roomIdB`、`roomIdB'=roomIdA`、`targetFloor'=` 本层。

缺孪生 = 玩家能过去回不来。

### 7.2 其他 fail-fast 点（分散在各处）

- 非 cave 房 `staticPlacements == null` → `AuthoredStaticPlacementsLogic.RequireDeclared` 抛错。
- pinned `cells` 不在 Static 层 → 抛错（`equipment` 例外，按 ground 校验）。
- `mountedEnemyId` 用在非 `cargo` kind → 抛错（OZX-1348 前「多条 count 条目」也抛，已放开）。
- `cargo` 的每只箱子摇**所属条目**的 `lootTableId`，缺省回落房间 `floorLootTableId`，都没有就抛（`CargoSpawnAdapter`，`ROOM_CARGO_LOOT_SOURCE_MISSING`）。手写关卡没有 `floorLootTableId`（生成期 `[NonSerialized]` 合成），所以手写 cargo 必须自带表；plan 楼层只有写了 `probabilistic` 才有。cargo 只支持 1×1（`ROOM_CARGO_FOOTPRINT_INVALID`）。技能不要走房间 `lootPlanId`（OZX-1381 已删），用 cargo `lootTableId` 指向技能表。
- `mountedCount` 超过该条目能放置的数量 → 抛错。
- `spawnIf` 写了未知 key、`prop` 缺 `prefabKey`、`skill` 缺 `skillId`、`trigger` 缺 `triggerId`、`equipment` 缺/错 `itemId` → 抛错。
- `skill` 的 `skillId` 指向的 SkillData `type != "active"`（OZX-1400，`ROOM_PLACEMENT_SKILL_NOT_ACTIVE`）、`charges <= 0`（`ROOM_PLACEMENT_SKILL_NO_CHARGES`），或其 `iconKeys` 为空 / 不在 ResourcesDB → 抛错
  （`SkillPlatformSpawnAdapter`；平台正中要画 level-1 图标，`ResolveIcon` 拿不到 key 就抛）。
  `iconKeys` 为空是**合法的 SkillData**，只是不能摆在平台上（`dash`、`evade`、`teleport` 这几个主动技能现在就是这样）。
  技能库只列出能摆的：主动、有可加载图标。出货的平台在 `level_test` / `factory2_test` / `battlefield_test` 的起始房（`laser`、`sprint`）。
- `railCart: true` 但房间模板没有 rail 层 → 抛错（`RailCartSpawnAdapter`）。
- `generatorType: "static"` 但 `rooms` 为空 → 抛错。
- `enemyPool` 非空但 `enemiesPerRoom <= 0` → 抛错。
- spawner 通道四条（`LevelPlanValidator.ValidateSpawnerPairs`，见 §5.4）→ 抛错。
- `guaranteed` 条目绑定数 ≠ 1 → 抛错；`boundWaveLastEnemy` 无条件抛错。
- `endsRunOnClear: true` 但楼层没有 boss 房 → 抛错。

### 7.3 静默失败（更危险，编辑器必须报错）

- **重复 `(dataType, id)`**：后加载者直接覆盖，无任何提示。
- **未知 `generatorType`**：静默变成 `tree`。
- **spawner 条目只写了一半**：`minionId` 或 `minionsPerSpawner` 缺一个时 `EnemyPoolEntry.IsSpawner` 判定为假，这条按普通敌人采样——工厂原地站着不产任何东西，没有任何报错（OZX-1032，见 §5.4）。
- **`countMin` 写在不是 `placement: "sidewall"` 的 entry 上**：读了就丢，摆放照旧（OZX-1057，见 §5.6）。
- **`skillDropLevelCap` 高于本章能掉的所有 `maxLevel`**：数字写着、看着像调过，实际永不触发（OZX-1069，见 §5.1）。
- **孵化不占任何预算**：`enemiesPerRoom` 和 `maxAlive` 都数不到孵化物，一间有蛋的房间比两个数字都大（OZX-1063，见 `level_referenced_data.md` §2.4）。
- **`defaultStaticPlacements` 里的 `oiltank` 不会下发给 start room**：规则在 `StaticPlacementPolicy` 里，JSON 上完全看不出来，所以照着 JSON 画预览会在出生房画出一个游戏里不存在的油罐（OZX-1057）。
- **`floorLootPlan.probabilistic[].itemId` / `stageMustDrop[].drops[].itemId` 悬空**：合成表照常建起来，只是那一条永远掉不出东西（见 §5.3）。
- **`floorLootPlan.categoryCaps[].category` 不是 taxonomy 节点**：cap 匹配不到任何道具，等于没写；这里至今没有 fail-fast。
- **跨 area 的接缝门缺了回程门**：单向门，过去回不来（OZX-1116 之前跨 area 的门本身就是死路，现在它是合法的接缝，只是要成对）。
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
- 引用字段（`encounterId`/`templateId`/`enemyId`/`itemId`/`skillId`/`keyId`/`lootTableId`）只能从现有 id 中选。
- 程序化关卡必须能按固定 seed 编译并展示 §6 的覆盖链，否则设计师无法解释「为什么我改的 drop 没生效」。

**不要做**

- 不要把 `inlineEncounter` / `inlineLootPlan` / `floorLootTableId` / `guaranteedCargo` / `effectiveStageWeight` 写进 json。
- 不要再写 `EncounterAction.dropTableIdOverride`：OZX-1041 把它改名为 `dropTableId`，JsonUtility 会静默丢弃未声明字段，漏改不报错，只是无声退回敌人自带的表。
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
