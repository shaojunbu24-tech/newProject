#!/usr/bin/env python3
"""由严格审计示例构造参考模式示例，并导出两个内容版视图。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable

from export_content_view import export_content_view


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "例子"
if not EXAMPLES.exists():
    EXAMPLES = ROOT / "assets" / "examples"
STRICT_AUDIT = EXAMPLES / "绝缘子釉表面灼伤-严格标签包.audit.json"
STRICT_CONTENT = EXAMPLES / "绝缘子釉表面灼伤-严格标签包.content.json"
REFERENCE_AUDIT = EXAMPLES / "绝缘子釉面局部复合异常-参考标签包.audit.json"
REFERENCE_CONTENT = EXAMPLES / "绝缘子釉面局部复合异常-参考标签包.content.json"


def iter_objects(objects: list[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for node in objects:
        yield node
        for key in ("设备实例", "部件实例"):
            children = node.get(key, [])
            if isinstance(children, list):
                yield from iter_objects(children)


def remove_trace_references(value: Any) -> None:
    """参考模式不要求轨迹，递归删除对象上的轨迹引用避免产生伪依赖。"""
    if isinstance(value, dict):
        value.pop("候选披露轨迹编号", None)
        for child in value.values():
            remove_trace_references(child)
    elif isinstance(value, list):
        for child in value:
            remove_trace_references(child)


def replace_text(value: Any, old: str, new: str) -> Any:
    """只做示例文本的机械替换，不改变代码和对象关系。"""
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [replace_text(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: replace_text(child, old, new) for key, child in value.items()}
    return value


def dump(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    strict = json.loads(STRICT_AUDIT.read_text(encoding="utf-8"))
    strict["输出视图"]["内容版生成状态"] = "已生成"
    dump(STRICT_AUDIT, strict)
    dump(STRICT_CONTENT, export_content_view(strict))

    reference = copy.deepcopy(strict)
    reference["_示例用途"] = (
        "用于验证参考标签包模式可以保留候选树外中文可见现象，并记录最近候选和复核状态；"
        "该结构回归示例不对应真实图片。"
    )
    reference["标注模式"] = {
        "模式代码": "REFERENCE_PACKAGE",
        "中文名称": "参考标签包",
        "标签树约束级别": "参考约束",
        "候选外标签处理": "保留开放中文标签、最近候选和人工复核状态",
        "训练使用策略": "待审核开放标签只进入候选池，不进入受控分类训练",
    }
    reference["输出视图"].update(
        {
            "审计版文件名": REFERENCE_AUDIT.name,
            "内容版文件名": REFERENCE_CONTENT.name,
            "内容版生成状态": "已生成",
        }
    )
    reference["图像基本信息"]["图像编号"] = "REGRESSION-INSULATOR-REFERENCE-001"
    reference["标注流程状态"]["候选披露轨迹"] = []
    remove_trace_references(reference["标注对象树"])

    # 将一个已知缺陷改为候选树外的更细粒度可见现象，展示“标签树仅作参考”。
    changed = False
    for node in iter_objects(reference["标注对象树"]):
        for defect in node.get("缺陷实例", []):
            if defect.get("缺陷类别", {}).get("代码") == "INSULATOR_GLAZE_BURN_MARK":
                defect["缺陷类别"] = {
                    "代码": None,
                    "中文名称": "绝缘子釉面局部复合异常斑痕",
                    "台账版本": "3.0",
                    "标签来源": "候选树参考扩展",
                    "候选树映射": {
                        "映射状态": "近似匹配",
                        "最近候选代码": [
                            "INSULATOR_GLAZE_BURN_MARK",
                            "INSULATOR_DISCHARGE_MARK",
                        ],
                        "最近候选名称": [
                            "绝缘子釉表面灼伤痕迹",
                            "绝缘子放电样痕迹",
                        ],
                        "差异说明": "图像只支持复合异常斑痕，暂不能稳定归入单一灼伤或放电标签",
                        "开放标签复核状态": "待审核",
                    },
                }
                changed = True
    if not changed:
        raise SystemExit("未找到待转换的绝缘子釉表面灼伤缺陷")

    reference = replace_text(
        reference,
        "釉表面灼伤",
        "釉面局部复合异常斑痕",
    )
    # 全局文本替换后恢复最近候选的正式台账名称，避免示例把开放词写回候选树。
    for node in iter_objects(reference["标注对象树"]):
        for defect in node.get("缺陷实例", []):
            category = defect.get("缺陷类别", {})
            if category.get("代码") is None:
                category["候选树映射"]["最近候选名称"][0] = "绝缘子釉表面灼伤痕迹"
    gate = reference.get("自动校验结果", {}).get("最终门禁", {})
    gate["允许进入训练"] = False
    gate["人工复核要求"] = ["开放标签需先完成专家归并或批准"]

    dump(REFERENCE_AUDIT, reference)
    dump(REFERENCE_CONTENT, export_content_view(reference))
    print("已生成严格/参考两种模式的审计版与内容版示例，共4份JSON。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
