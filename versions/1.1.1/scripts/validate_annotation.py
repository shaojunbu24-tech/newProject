#!/usr/bin/env python3
"""校验标注规则1.1.1的树形对象、逐实例标注和覆盖统计。

本校验器不依赖候选标签树，它关心的是1.1.1相比1.0的核心改动：
对象必须嵌套、缺陷必须属于真实载体，且可分辨绝缘子不得被合并成一个大框。

Skill只打包结构示例JSON，不复制原始巡检图，避免仓库重复存储图像数据，
因此这里只要求`原始图像路径`保留可追溯的原始文件名，不检查Skill内是否存在JPG。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


SKILL_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = SKILL_ROOT / "assets" / "examples"


def load(path: Path) -> dict[str, Any]:
    """按UTF-8读取JSON，让语法错误直接带出文件路径。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取JSON：{path}\n{exc}") from exc


def walk_objects(nodes: list[dict[str, Any]], parent_type: str | None = None) -> Iterable[tuple[dict[str, Any], str | None]]:
    """只沿1.1.1允许的设备/部件嵌套数组遍历对象。"""
    for node in nodes:
        yield node, parent_type
        for key in ("设备实例", "部件实例"):
            children = node.get(key, [])
            if isinstance(children, list):
                yield from walk_objects(children, node.get("对象类型"))


def valid_bbox(value: Any) -> bool:
    """边界框必须是范围合法且宽高非零的四元整数数组。"""
    if not isinstance(value, list) or len(value) != 4 or not all(isinstance(x, int) for x in value):
        return False
    x1, y1, x2, y2 = value
    return 0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000


def validate(path: Path) -> list[str]:
    """返回单份标注的所有问题；空列表表示通过。"""
    data = load(path)
    errors: list[str] = []
    if data.get("标注规范版本") != "电力设备多模态标注-v1.1.1":
        errors.append("标注规范版本不是v1.1.1")

    image_path = data.get("图像基本信息", {}).get("原始图像路径")
    if not isinstance(image_path, str) or not image_path:
        errors.append("原始图像路径缺失")
    # 只要求保留可追溯的原图文件名；Skill内不打包JPG，故不检查文件是否真实存在。

    # 阻止1.0的顶层并列结构回流。
    for forbidden in ("设施实例", "设备实例", "部件实例", "缺陷实例"):
        if forbidden in data:
            errors.append(f"不允许顶层并列字段：{forbidden}")

    roots = data.get("标注对象树")
    if not isinstance(roots, list) or not roots:
        return errors + ["标注对象树缺失或为空"]

    object_ids: set[str] = set()
    defect_ids: set[str] = set()
    object_count = 0
    insulator_count = 0
    for node, parent_type in walk_objects(roots):
        object_count += 1
        kind = node.get("对象类型")
        object_id = node.get("对象编号")
        if kind not in ("设施", "设备", "部件"):
            errors.append(f"{object_id or '未知对象'}：非法对象类型{kind}")
        if parent_type is None and kind != "设施":
            errors.append(f"{object_id or '未知对象'}：对象树根节点必须是设施")
        if kind == "设备" and parent_type != "设施":
            errors.append(f"{object_id}：设备必须直接嵌套在设施下")
        if kind == "部件" and parent_type not in ("设施", "设备", "部件"):
            errors.append(f"{object_id}：部件父节点类型非法")
        if not object_id:
            errors.append("存在缺少对象编号的节点")
        elif object_id in object_ids:
            errors.append(f"对象编号重复：{object_id}")
        else:
            object_ids.add(object_id)
        if not valid_bbox(node.get("边界框_归一化坐标")):
            errors.append(f"{object_id or '未知对象'}：边界框非法")
        label = node.get("标签", {})
        name = label.get("中文名称", "") if isinstance(label, dict) else ""
        if "绝缘子组" in name or "套管组" in name:
            errors.append(f"{object_id}：不得用实例组替代可分辨实例")
        if "绝缘子" in name or "套管" in name:
            insulator_count += 1

        for item in node.get("缺陷实例", []):
            defect_id = item.get("缺陷编号")
            if not defect_id:
                errors.append(f"{object_id}：缺陷节点缺少缺陷编号")
            elif defect_id in defect_ids:
                errors.append(f"缺陷编号重复：{defect_id}")
            else:
                defect_ids.add(defect_id)
            if not valid_bbox(item.get("边界框_归一化坐标")):
                errors.append(f"{defect_id or '未知缺陷'}：缺陷边界框非法")

    evidence_targets = {
        item.get("对应对象编号")
        for item in data.get("视觉证据", [])
        if isinstance(item, dict) and item.get("可见事实")
    }
    for defect_id in defect_ids:
        if defect_id not in evidence_targets:
            errors.append(f"{defect_id}：缺少非空视觉证据")

    coverage = data.get("实例覆盖统计", {})
    if coverage.get("可分辨电力对象数") != object_count:
        errors.append(f"可分辨电力对象数与对象树不一致：统计{coverage.get('可分辨电力对象数')}，实际{object_count}")
    if coverage.get("已标注电力对象数") != object_count:
        errors.append(f"已标注电力对象数与对象树不一致：统计{coverage.get('已标注电力对象数')}，实际{object_count}")
    if coverage.get("可分辨绝缘子数量") != insulator_count:
        errors.append(f"可分辨绝缘子数量与对象树不一致：统计{coverage.get('可分辨绝缘子数量')}，实际{insulator_count}")
    if coverage.get("已标注绝缘子数量") != insulator_count:
        errors.append(f"已标注绝缘子数量与对象树不一致：统计{coverage.get('已标注绝缘子数量')}，实际{insulator_count}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验1.1.1树形标注")
    parser.add_argument("annotation", nargs="?", type=Path, help="单份待校验JSON")
    parser.add_argument("--all", action="store_true", help="校验examples下所有JSON")
    args = parser.parse_args()
    if args.all:
        paths = sorted(EXAMPLES.glob("*.json"))
    elif args.annotation:
        paths = [args.annotation]
    else:
        parser.error("请提供annotation或--all")

    failures = 0
    for path in paths:
        errors = validate(path)
        if errors:
            failures += 1
            print(f"[FAIL] {path.name}")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"[PASS] {path.name}")
    print(f"校验完成：{len(paths) - failures}份通过，{failures}份失败。")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
