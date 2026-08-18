#!/usr/bin/env python3
"""从3.0审计版确定性导出不含候选轨迹的内容版JSON。"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any


# 内容版只保留图像语义、对象关系和下游文本。这里列出的顶层字段属于
# 标注过程或治理信息，必须留在审计版中，不能进入训练输入。
CONTENT_TOP_LEVEL_KEYS = (
    "图像基本信息",
    "外部信息",
    "图像路由",
    "主要对象主题",
    "标注对象树",
    "环境标注",
    "关联关系",
    "视觉证据",
    "不确定信息",
    "多模态描述",
    "组合图像检索标注",
)

# 这些字段可能出现在任意对象层级。对象编号和缺陷编号不删除，因为它们是
# 嵌套关系、视觉证据和组合检索目标的稳定引用，不属于候选披露过程。
PROCESS_KEYS = {
    "_说明",
    "_模板说明",
    "_示例用途",
    "候选树路径",
    "候选披露轨迹编号",
    "台账版本",
    "标签性质",
    "标签来源",
    "候选树映射",
    "激活标签包",
    "标签包名称",
    "标签包版本",
    "候选代码",
    "选择代码",
    "候选快照摘要",
    "轨迹编号",
    "选择结果状态",
}


def clean_value(value: Any) -> Any:
    """递归删除过程字段，并去掉参考模式中不存在的空稳定代码。"""
    if isinstance(value, list):
        return [clean_value(item) for item in value]
    if not isinstance(value, dict):
        return value

    cleaned: dict[str, Any] = {}
    for key, child in value.items():
        if key in PROCESS_KEYS:
            continue
        if key in {"代码", "标签代码"} and child in (None, "", "待选择或留空"):
            continue
        cleaned[key] = clean_value(child)

    # 缺陷检查中的轨迹字段被删除后，空对象仍保留状态和说明，便于表达
    # “已检查无异常”“不可见”或“不可判定”等内容结论。
    return cleaned


def export_content_view(audit: dict[str, Any]) -> dict[str, Any]:
    """构建内容版；不修改传入的审计版对象。"""
    result: dict[str, Any] = {
        "标注规范版本": "电力设备多模态标注-v3.0-content"
    }
    for key in CONTENT_TOP_LEVEL_KEYS:
        if key in audit:
            result[key] = clean_value(copy.deepcopy(audit[key]))
    return result


def default_output_path(input_path: Path) -> Path:
    name = input_path.name
    if name.endswith(".audit.json"):
        return input_path.with_name(name[: -len(".audit.json")] + ".content.json")
    return input_path.with_name(input_path.stem + ".content.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="导出电力图像标注3.0内容版JSON")
    parser.add_argument("input", type=Path, help="审计版*.audit.json")
    parser.add_argument("--output", type=Path, help="内容版输出路径；默认与输入同目录")
    args = parser.parse_args()

    try:
        audit = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取审计版JSON：{exc}") from exc

    if audit.get("标注规范版本") != "电力设备多模态标注-v3.0":
        raise SystemExit("输入不是3.0审计版JSON")

    output = args.output or default_output_path(args.input)
    output.write_text(
        json.dumps(export_content_view(audit), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"已生成内容版：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
