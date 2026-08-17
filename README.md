# Power Image Annotation Skill

面向电力设备图像数据集建设的中文结构化标注 Skill。它通过“图像路由 → 设备主题分层标注 → 缺陷标注 → 双阶段校验”的流程，按当前选择渐进加载候选标签，减少自由编造、无关候选干扰和提示词 Token 消耗，并在校验通过后派生多种检索描述。

## 核心能力

- 固定枚举完成业务域、设施场景、拍摄任务和图像质量路由。
- 按“设施/线路 → 设备 → 部件 → 子部件”建立嵌套对象树。
- 选择设备后仅披露其允许包含的部件，选择载体后仅披露相关缺陷。
- 将缺陷绑定到具体设备或部件，并记录直接可见证据、置信状态和复核要求。
- 先执行确定性规则校验，再进行大模型语义复核。
- 从同一份标注 JSON 派生纯视觉描述、业务描述、检索描述和组合检索修改文本。

## 标注流程

```text
原始图像与文件名
  ↓
阶段一：图像路由
  业务域 → 设施场景 → 拍摄任务 → 适用标签包
  ↓
阶段二：设备主题分层标注
  设施/线路 → 主设备 → 部件 → 子部件 → 静态属性
  ↓
阶段三：缺陷标注
  根据载体类型加载缺陷候选 → 绑定载体 → 记录可见证据
  ↓
阶段四：自动校验
  确定性规则校验 → 大模型语义复核 → 门禁判定
  ↓
派生输出
  纯视觉描述、业务描述、检索描述、组合检索修改文本
```

这里的“渐进式披露”不会改变标注规则本身，只改变每个阶段向模型提供的候选范围。模型必须从候选树选择标签，不应临时创造设备、部件或缺陷名称。

## 当前覆盖范围

当前资源重点覆盖：

- 配电架空线路的杆塔、杆上设备、杆塔元件、运行环境、缺陷隐患等标签。
- 已纳入候选树的变电设备，包括变压器、断路器、避雷器、电容器等对象及其部分部件和缺陷关系。
- 输电、发电、用电及部分场站场景可先完成图像路由；未建立完整设备—部件—缺陷关系的标签包需要继续扩展。

候选树是受控词表和关系约束，不等同于已经完成行业专家终审。新增业务域或设备类型时，应同步进行标签覆盖审计和样例回归测试。

## 目录结构

```text
power-image-annotation/
├── SKILL.md                         # Codex 执行入口与工作流程
├── README.md                        # 面向使用者的说明
├── agents/openai.yaml               # Skill 展示与调用配置
├── assets/
│   └── 标注规则2.0.json              # 标注结构模板
├── references/
│   ├── 标注规则2.0.md                # 完整中文规则
│   ├── 标签候选树2.0.json            # 候选标签与允许关系
│   ├── 候选标签覆盖审计2.0.md        # 覆盖范围与缺口审计
│   ├── 大模型语义复核规范.md         # 第二阶段语义校验要求
│   └── 资源清单.json                 # 资源哈希与同步信息
└── scripts/
    ├── select_candidates.py          # 渐进式候选选择器
    ├── validate_annotation.py        # 标注 JSON 规则校验
    ├── render_captions.py            # 派生多种描述文本
    ├── sync_resources.py             # 从规则目录同步资源
    └── self_test.py                  # 内置回归测试
```

## 安装

将仓库克隆到 Codex 的 Skills 目录：

```bash
git clone https://github.com/shaojunbu24-tech/newProject.git \
  ~/.codex/skills/power-image-annotation
```

若目标目录已经存在，请在该目录中使用 `git pull` 更新，不要重复克隆覆盖本地修改。

## 在 Codex 中使用

可以显式指定 Skill，例如：

```text
使用 $power-image-annotation 标注这张配电架空线路图片。
先完成图像路由，再逐层给出候选，不允许自由生成标签；
最后执行规则校验和大模型语义复核，并输出检索描述。
```

也可以用于审核已有 JSON：

```text
使用 $power-image-annotation 审核这份标注 JSON，
检查标签是否在候选树中、部件是否属于设备、缺陷是否绑定正确载体，
并区分确定性错误与需要人工复核的语义问题。
```

## 渐进式查询候选

以下命令展示候选标签如何随选择结果逐步收窄：

```bash
cd ~/.codex/skills/power-image-annotation

# 查看图像路由候选
python3 scripts/select_candidates.py route --field domains

# 选择业务域后查看设施场景
python3 scripts/select_candidates.py scene --domain DIS

# 选择设施场景后查看设备主题
python3 scripts/select_candidates.py subject \
  --domain DIS --scene DIS_OHL

# 选择具体设备后查看允许部件
python3 scripts/select_candidates.py parts \
  --domain DIS --scene DIS_OHL --object-code DTR_3P

# 选择具体载体后查看允许缺陷
python3 scripts/select_candidates.py defects \
  --domain DIS --scene DIS_OHL \
  --carrier-code TR_PIPE_JOINT --parent-object-code DTR_3P
```

命令使用稳定代码传递已选标签，例如 `DIS` 表示“配电”、`DIS_OHL` 表示“配电架空线路”、`DTR_3P` 表示“三相配电变压器”。脚本输出会同时给出代码和中文名称，便于程序处理与人工检查。

具体参数和可用子命令可通过以下方式查看：

```bash
python3 scripts/select_candidates.py --help
```

## 校验标注 JSON

```bash
cd ~/.codex/skills/power-image-annotation
python3 scripts/validate_annotation.py /path/to/annotation.json
```

校验分为两层：

1. **确定性规则校验**：检查 JSON 结构、枚举值、候选台账、对象从属关系、缺陷载体、证据字段和跨字段逻辑。
2. **大模型语义复核**：结合图像检查对象识别、可见证据、描述一致性和不确定性表达，输出通过、退回修改或人工复核建议。

确定性规则失败时，不应生成下游描述；规则通过但视觉证据存在歧义时，应进入语义复核或人工复核，而不是强行确认缺陷。

## 生成检索描述

```bash
cd ~/.codex/skills/power-image-annotation
python3 scripts/render_captions.py /path/to/annotation.json
```

派生文本应始终可追溯到结构化字段。文件名解析出的电压等级、线路名称、杆号等外部信息需要标明来源，不能伪装成图像直接可见结论。

## 同步规则资源

当项目中的标注规则更新后，可显式指定来源目录进行同步：

```bash
cd ~/.codex/skills/power-image-annotation
python3 scripts/sync_resources.py \
  --source-dir /path/to/标注规则/2.0
```

同步后应检查差异并执行完整自测，再提交资源变更。资源清单仅记录文件名和哈希，不记录开发机器的绝对路径。

## 自测

```bash
cd ~/.codex/skills/power-image-annotation
python3 scripts/self_test.py
```

修改候选树、模板、选择器或校验器后都应运行该测试。若新增标签，还应确认：

- 标签具有稳定标识和中文名称；
- 设备与部件之间存在明确允许关系；
- 缺陷绑定到可承载该异常的对象类型；
- 正常、不可见、不可判定和疑似状态不会被误写成已确认缺陷；
- 示例覆盖新增路径及其反例。

## 标注原则

- 图像事实与文件名、台账等外部信息分开记录来源。
- 设备包含部件，部件可继续包含子部件，避免把所有对象平铺为并列数组。
- 缺陷不是孤立标签，必须绑定载体并给出可见证据。
- “未见明显异常”是检查结论，不是一个缺陷实例。
- 不把完整候选树一次性塞入提示词，只加载当前阶段需要的最小标签包。
- 候选中没有合适标签时标记待扩展或待复核，不允许模型自行造词补齐。

## 使用与发布说明

本仓库当前未附带开源许可证。在公开复制、再分发或用于商业项目之前，请先确认代码、规则文档、标签体系及相关数据的授权范围。
