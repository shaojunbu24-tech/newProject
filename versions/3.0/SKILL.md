---
name: power-image-annotation-v3
description: 对电力设备图像执行3.0双模式中文结构化标注，并为每图固定生成审计版和纯内容版两个JSON。支持严格标签包模式，将标签候选树作为硬约束并保存候选轨迹；也支持参考标签包模式，将标签树作为专业参考、允许可追溯的开放中文标签。用于嵌套设施/设备/部件/缺陷标注、开放词汇标签发现、受控训练数据、标注审计、多模态描述和组合图像检索数据构建。
---

# 电力图像双模式标注3.0

## 固定产物

为每张图生成两个文件：

```text
<图像名>.audit.json    # 权威审计版
<图像名>.content.json  # 由审计版确定性导出的内容版
```

只编辑审计版。内容版始终用`scripts/export_content_view.py`重新生成，不分别人工维护。

审计版保留候选轨迹、候选代码数组、标签映射、规则校验和人工审核。内容版只保留图像信息、路由结果、嵌套标注对象树、缺陷、视觉证据、不确定项、多模态描述和组合检索标注。

## 选择模式

开始前完整读取`references/标注规则3.0.md`。根据任务选择：

- 需要固定类别训练、正式数据集或严格复现时，选择`STRICT_PACKAGE`；
- 需要发现新标签、覆盖候选树缺口或进行开放词汇标注时，选择`REFERENCE_PACKAGE`；
- 用户未指定且任务没有明显的标签发现目的时，默认`STRICT_PACKAGE`。

模式只改变标签约束，不改变嵌套对象树、缺陷载体和视觉证据要求。

## 建立审计版

复制`assets/标注规则3.0_审计版.json`，填写`标注模式`和两个输出文件名。

始终先看图，再解析文件名。把线路名、杆号、故障原词和严重度保存在外部信息并标明来源，不得把文件名直接作为视觉结论。

按以下结构写入`标注对象树`：

```text
设施 → 设备 → 部件 → 子部件 → 缺陷
```

只实例化图中可见对象。缺陷嵌套在实际载体中，并绑定直接可见证据。对象编号和缺陷编号全树唯一。

### 严格标签包

使用`scripts/select_candidates.py`分阶段获取候选，并把返回的候选代码、选择代码和快照摘要写入候选披露轨迹。正式代码只能来自当前候选快照；没有合适标签时使用`OTHER_REVIEW`并退出受控训练。

示例：

```bash
python3 scripts/select_candidates.py subject --domain DIS --scene DIS_OHL
python3 scripts/select_candidates.py parts \
  --domain DIS --scene DIS_OHL --object-code POLE_TOWER
python3 scripts/select_candidates.py defects \
  --domain DIS --scene DIS_OHL --carrier-code jyz-taoci_zhushi
```

### 参考标签包

读取`references/标签候选树3.0.json`作为推荐词表和同义映射参考，不要求逐层保存候选轨迹。

候选树精确匹配时使用正式代码。不能精确匹配时保留图像对应的中文开放标签，代码留空，并填写：

- `标签来源`：`候选树参考扩展`或`人工确认扩展`；
- `候选树映射.映射状态`：`近似匹配`或`未匹配`；
- 最近候选代码、最近候选名称和差异说明；
- 开放标签复核状态。

不要生成临时英文代码冒充稳定标签。待审核开放标签只能进入开放词汇候选池，不能直接进入受控分类训练。

## 校验与导出

先校验审计版：

```bash
python3 scripts/validate_annotation.py <图像名>.audit.json
```

只有报告中的`允许导出内容版`为true时才能导出：

```bash
python3 scripts/export_content_view.py \
  <图像名>.audit.json --output <图像名>.content.json
python3 scripts/validate_annotation.py <图像名>.content.json
```

内容版会删除轨迹编号、候选代码数组、选择代码、候选快照、候选树路径、标签来源、映射过程和校验过程。保留对象/缺陷编号及已经选定的稳定标签代码；参考模式的开放标签没有稳定代码时只保留中文名称。

需要格式参考时读取`assets/examples/`。其中包含严格模式和参考模式各一组审计版/内容版，共4份JSON。

## 维护与自检

修改规则、模板、标签树或脚本后运行：

```bash
python3 scripts/build_examples.py
python3 scripts/self_test.py
python3 /home/bushaojun/.codex/skills/.system/skill-creator/scripts/quick_validate.py .
```

不要把内容版反向写回审计版，也不要让参考模式的新词静默进入严格标签树。
