---
name: power-image-annotation
description: 对电力设备图像执行四阶段中文结构化标注：图像路由、设施/设备/部件树形标注、按载体动态筛选缺陷、确定性规则校验与大模型语义复核，并在门禁通过后生成多模态和组合检索描述。用于配电架空线路、输电架空线路绝缘子专项、当前已覆盖的变电设备图像、标签候选选择、标注JSON审核、候选树扩展和数据集构建；尤其适用于要求模型不能自由编造标签、需按业务域和对象路径渐进披露候选的任务。
---

# 电力图像渐进式标注

## 核心约束

保持四阶段规则不变：

```text
ROUTE图像路由
→ SUBJECT设备主题分层标注
→ DEFECT按载体标注缺陷
→ VALIDATE规则校验和大模型语义复核
→ DERIVE派生描述
```

阶段一至三渐进式披露候选。不要一次读取或发送全部缺陷标签；不要让模型选择本轮候选快照之外的正式代码。

权威结果使用嵌套`标注对象树`：设施包含设备，设备包含部件，部件可以包含子部件，缺陷嵌套在实际载体内部。业务域与设备类型分开，例如10kV杆上变压器通常属于配电业务域。

## 开始前

1. 使用`assets/标注规则2.0.json`作为新标注骨架。
2. 需要理解字段和边界时读取`references/标注规则2.0.md`；不要凭记忆改写规则。
3. 候选选择通过`scripts/select_candidates.py`执行，避免把全量`references/标签候选树2.0.json`加载进提示词。
4. 如果项目源规则刚被修改，运行`scripts/sync_resources.py --source-dir /项目中的标注规则/2.0`同步快照，再继续标注。
5. 读取`references/资源清单.json`确认当前Skill快照的来源和摘要。

## 阶段一：ROUTE

先观察图像，再把文件名解析保存在`外部信息`。文件名不能直接作为视觉结论。

按需获取路由候选：

```bash
python3 scripts/select_candidates.py route --field domains
python3 scripts/select_candidates.py scene --domain DIS
python3 scripts/select_candidates.py route --field tasks
python3 scripts/select_candidates.py route --field ranges
python3 scripts/select_candidates.py route --field modalities
python3 scripts/select_candidates.py route --field clarity
python3 scripts/select_candidates.py route --field occlusion
python3 scripts/select_candidates.py route --field continuation
```

每次把脚本返回的`阶段代码、父节点代码、标签包名称、标签包版本、候选代码、候选快照摘要`和最终`选择代码`写入`标注流程状态.候选披露轨迹`。

只有路由状态为“可以”或“部分可以”时进入阶段二。规划级场景只有路由能力时，不得把`规划设备名称`当作正式训练标签。

## 阶段二：SUBJECT

先获取场景设施和设备：

```bash
python3 scripts/select_candidates.py subject --domain DIS --scene DIS_OHL
```

选择对象后加载下一层：

```bash
python3 scripts/select_candidates.py parts \
  --domain DIS --scene DIS_OHL --object-code DTR_3P
```

若所选部件还有子部件，用同一个`parts`命令继续查询。`subject`和`parts`都属于阶段二；不要在阶段二加载缺陷。

输电架空线路当前只开放绝缘子专项，不代表输电全专业覆盖。路由为`TRA/TRA_OHL`时，可以正式选择`TRA_TOWER`或`TRA_LINE_CORRIDOR`，再渐进选择`TRA_INSULATOR_STRING`、绝缘子类型、本体、釉表面或伞裙；导地线、金具等仍处于规划级时必须转扩展审核。10kV文件名通常提示配电，但只能作为外部信息，最终业务域仍以可见结构和数据来源复核为准。

只实例化清晰可见的对象。父设备不在图中而部件可见时，可以将部件挂在设施节点并注明“父设备图外未实例化”，不要虚构不可见设备。

完成对象树和静态属性后，建立`待检查载体队列`。表计读数、油位、温度和开关位置写入`状态量标注`，不强制转成缺陷。

## 阶段三：DEFECT

对待检查载体逐个查询，不要一次查询整张图的所有缺陷：

```bash
python3 scripts/select_candidates.py defects \
  --domain DIS \
  --scene DIS_OHL \
  --carrier-code TOWER_BODY \
  --parent-object-code POLE_TOWER \
  --attributes-json '{"杆塔材质":"shuinigan"}' \
  --task 缺陷巡视
```

脚本优先使用部件精确映射，其次使用对象载体映射，并应用材质等确定性约束。正常模式不输出被过滤标签；审核规则时可以追加`--audit`查看过滤原因。

绝缘子代码以`jyz-`开头时，缺少精确映射也必须回退到绝缘子受限候选，不得继承父杆塔的裂纹、倾斜或抱箍锈蚀。釉表面灼伤、放电样痕迹、电弧烧伤、闪络路径、污秽和电蚀属于可见现象；“雷击”属于文件名、工单或专家诊断中的故障原因，不能直接作为视觉缺陷；“一般/严重”同样只作为外部严重度，除非业务规则另有确认。

注意材质与载体边界：

- 水泥杆或复合材料杆的杆体不能标金属腐蚀；
- 钢管杆和角钢塔本体可以腐蚀；
- 水泥杆上的金属抱箍、横担、法兰和紧固件仍可锈蚀。

每个适用载体必须得到缺陷检查结论。`NO_VISIBLE_ANOMALY`表示检查结论，不创建缺陷实例。每个实际缺陷必须绑定载体、候选轨迹和至少一条直接可见证据；图像不能确认的原因或严重等级写入`不可确认内容`。

## 阶段四A：确定性规则校验

运行：

```bash
python3 scripts/validate_annotation.py /absolute/path/to/annotation.json
```

新标注禁止使用`--allow-legacy`。该选项只用于检查旧示例兼容性。

若报告中`允许进入大模型语义复核`为false，按问题的`退回阶段`修改权威JSON并重新校验，不执行语义复核。不要静默自动替换标签或移动载体。

## 阶段四B：大模型语义复核

规则校验无阻断后，完整读取`references/大模型语义复核规范.md`并按其中格式审核。采用视觉优先的两个视图：

1. 先看图像、对象树、当前候选子树和视觉证据，暂不使用文件名中的故障原因与严重等级；
2. 再加入文件名、设备台账和工单，检查来源隔离与冲突。

审核正式建议只能来自当前候选子树；候选外对象使用`OTHER_REVIEW`。大模型提出修改后，退回对应阶段修改，再重新运行规则校验和语义复核，不能把审核建议直接当成事实。

把结构化审核结果写入`自动校验结果.大模型语义复核`，再计算`最终门禁`。规则与大模型冲突、图像证据不足、候选缺失或外部信息冲突时转人工复核。

## 派生输出

只有`自动校验结果.最终门禁.允许生成派生输出`为true时运行：

```bash
python3 scripts/render_captions.py /absolute/path/to/annotation.json
```

把审核后的输出写回`多模态描述`。任何对象、属性、状态量、缺陷或证据修改都会使旧派生文本失效，必须重新执行阶段四并重新生成。

## 标签树维护

项目候选树是唯一人工维护事实源。修改设施、设备、部件、子部件、缺陷或跨字段规则后：

1. 在项目2.0目录运行`python3 校验候选树.py`；
2. 运行`scripts/sync_resources.py --source-dir /项目中的标注规则/2.0`同步Skill快照；
3. 重新验证Skill；
4. 用变压器部件、水泥杆杆体、金属抱箍和存量示例做回归测试。

不要直接手改Skill中的同步JSON快照，否则项目台账和运行时规则会产生双份事实源。
