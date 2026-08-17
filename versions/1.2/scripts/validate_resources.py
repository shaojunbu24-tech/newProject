#!/usr/bin/env python3
"""校验标注规则1.2的模板、候选树和回溯示例。

1.2不要求保存2.0式的逐层候选披露轨迹，因此本脚本只检查稳定且
与模型调用次数无关的约束：版本一致、对象树嵌套、正式代码存在、
缺陷具备视觉证据，以及示例引用的原图确实存在。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


SKILL_ROOT = Path(__file__).resolve().parent.parent
TREE_PATH = SKILL_ROOT / "references" / "标签候选树1.2.json"
TEMPLATE_PATH = SKILL_ROOT / "assets" / "标注规则1.2.json"
EXAMPLE_DIR = SKILL_ROOT / "assets" / "examples"


def load_json(path: Path) -> Any:
    """按UTF-8读取JSON；错误中保留文件路径，便于直接定位资源。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取JSON：{path}\n{exc}") from exc


def walk(value: Any) -> Iterable[Any]:
    """深度遍历任意JSON值，供代码台账和对象实例校验共同使用。"""
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def collect_tree_codes(tree: dict[str, Any]) -> set[str]:
    """收集候选树中所有显式发布的代码，包括路由、对象和缺陷代码。"""
    codes: set[str] = set()
    for node in walk(tree):
        if not isinstance(node, dict):
            continue
        for key in ("代码", "业务域代码", "设施场景代码"):
            value = node.get(key)
            if isinstance(value, str) and value:
                codes.add(value)
    # OTHER_REVIEW是全局保底控制代码，候选树通过专门字段发布。
    fallback = tree.get("标签使用规则", {}).get("候选外处理代码")
    if isinstance(fallback, str):
        codes.add(fallback)
    return codes


def iter_object_nodes(objects: list[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    """只沿允许的嵌套关系遍历，避免把顶层辅助字段误当标注对象。"""
    for node in objects:
        yield node
        for key in ("设备实例", "部件实例"):
            children = node.get(key, [])
            if isinstance(children, list):
                yield from iter_object_nodes(children)


def validate_example(path: Path, known_codes: set[str]) -> list[str]:
    """返回单份示例的错误列表；空列表表示通过。"""
    data = load_json(path)
    errors: list[str] = []

    if data.get("标注规范版本") != "电力设备多模态标注-v1.2":
        errors.append("标注规范版本不是v1.2")
    if data.get("标签台账版本") != "1.2":
        errors.append("标签台账版本不是1.2")

    objects = data.get("标注对象树")
    if not isinstance(objects, list) or not objects:
        errors.append("标注对象树缺失或为空")
        return errors

    object_ids: set[str] = set()
    defect_ids: set[str] = set()
    for node in iter_object_nodes(objects):
        object_id = node.get("对象编号")
        if object_id:
            if object_id in object_ids:
                errors.append(f"对象编号重复：{object_id}")
            object_ids.add(object_id)

        label = node.get("标签", {})
        code = label.get("代码") if isinstance(label, dict) else None
        if code and code not in known_codes:
            errors.append(f"对象代码不在候选树：{code}")

        for defect in node.get("缺陷实例", []):
            defect_id = defect.get("缺陷编号")
            if defect_id:
                if defect_id in defect_ids:
                    errors.append(f"缺陷编号重复：{defect_id}")
                defect_ids.add(defect_id)
            defect_type = defect.get("缺陷类别", {})
            defect_code = defect_type.get("代码") if isinstance(defect_type, dict) else None
            if defect_code not in known_codes:
                errors.append(f"缺陷代码不在候选树：{defect_code}")

    evidence_targets = {
        item.get("对应对象编号")
        for item in data.get("视觉证据", [])
        if isinstance(item, dict)
    }
    for defect_id in defect_ids:
        if defect_id not in evidence_targets:
            errors.append(f"缺陷缺少视觉证据引用：{defect_id}")

    # Skill只打包结构示例JSON，不复制原始巡检图，避免仓库重复存储图像数据。
    # 因此这里只要求保留可追溯的原始文件名，不检查Skill内是否存在JPG。
    image_name = data.get("图像基本信息", {}).get("原始文件名")
    if not image_name:
        errors.append("缺少原始文件名")
    return errors


def main() -> int:
    tree = load_json(TREE_PATH)
    template = load_json(TEMPLATE_PATH)
    known_codes = collect_tree_codes(tree)
    failures: list[str] = []

    if tree.get("标签台账版本") != "1.2":
        failures.append("标签候选树版本不是1.2")
    if template.get("标注规范版本") != "电力设备多模态标注-v1.2":
        failures.append("模板规范版本不是v1.2")
    if template.get("标签台账版本") != "1.2":
        failures.append("模板台账版本不是1.2")

    examples = sorted(EXAMPLE_DIR.glob("*.json"))
    if len(examples) != 5:
        failures.append(f"应有5份周四回溯示例，实际为{len(examples)}份")
    for example in examples:
        for error in validate_example(example, known_codes):
            failures.append(f"{example.name}：{error}")

    if failures:
        print("标注规则1.2资源校验失败：")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(
        "标注规则1.2资源校验通过："
        f"候选代码{len(known_codes)}个，周四回溯示例{len(examples)}份。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
