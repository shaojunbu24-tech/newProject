#!/usr/bin/env python3
"""按四阶段流程返回当前一步允许披露的最小候选视图。

这个脚本只做确定性的候选裁剪，不识别图像，也不替模型作选择。Skill先查看图像，
再把已经确定的业务域、场景、父对象和静态属性传给本脚本。脚本返回：

* 当前问题可以选择的候选；
* 用于``候选披露轨迹``的代码数组和SHA-256摘要；
* 下一步应该执行的阶段或选择动作。

正常标注模式不会返回被过滤标签，避免视觉模型受到不可能标签干扰。只有显式传入
``--audit``时才输出过滤原因，供规则审核和候选树维护使用。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


SKILL_ROOT = Path(__file__).resolve().parents[1]
# 项目源目录把候选树放在3.0根目录，安装后的Skill放在references/。
# 自动选择存在的路径，使同一脚本无需维护两份实现。
_skill_ledger = SKILL_ROOT / "references" / "标签候选树3.0.json"
_source_ledger = SKILL_ROOT / "标签候选树3.0.json"
DEFAULT_LEDGER = _skill_ledger if _skill_ledger.exists() else _source_ledger


def load_ledger(path: Path) -> dict[str, Any]:
    """读取候选树快照；不存在或JSON错误时立即终止，避免空候选继续标注。"""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取标签候选树 {path}: {exc}") from exc


def build_definition_index(value: Any, index: dict[str, dict[str, Any]]) -> None:
    """递归建立代码到完整标签对象的索引。

    同一代码在树中可能被引用多次；索引保留第一次包含中文名称的正式定义。运行时
    输出会复制该定义，从而同时提供代码、中文名称、标签性质和训练状态。
    """

    if isinstance(value, dict):
        code = value.get("代码")
        name = value.get("中文名称")
        if isinstance(code, str) and isinstance(name, str):
            index.setdefault(code, value)
        for child in value.values():
            build_definition_index(child, index)
    elif isinstance(value, list):
        for child in value:
            build_definition_index(child, index)


def is_selectable(item: dict[str, Any], include_nontrainable: bool) -> bool:
    """判断标签是否可作为当前正式候选。

    ``OTHER_REVIEW``虽然不参与训练，但必须保留作为候选外兜底，因此由调用处显式
    加入；其他虚标签、辅助标签、停用标签默认不披露。
    """

    if include_nontrainable:
        return True
    if item.get("参与训练", True) is False:
        return False
    if item.get("标签性质", "实标签") in {"虚标签", "辅助标签"}:
        return False
    if item.get("启用状态") == "暂不启用":
        return False
    return True


def find_domain(ledger: dict[str, Any], domain_code: str) -> dict[str, Any]:
    """按代码定位业务域，并对未知代码给出明确错误。"""

    for domain in ledger["业务候选树"]:
        if domain["业务域"]["代码"] == domain_code:
            return domain
    raise SystemExit(f"未知业务域代码：{domain_code}")


def find_scene(
    ledger: dict[str, Any], domain_code: str, scene_code: str
) -> dict[str, Any]:
    """在已选择业务域内部定位设施场景，阻止跨业务域加载场景。"""

    domain = find_domain(ledger, domain_code)
    for scene in domain["设施场景"]:
        if scene["代码"] == scene_code:
            return scene
    raise SystemExit(f"场景{scene_code}不属于业务域{domain_code}")


def deduplicate_candidates(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按代码稳定去重，保留候选树中的原始顺序。"""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        code = item.get("代码")
        if not isinstance(code, str) or code in seen:
            continue
        seen.add(code)
        result.append(dict(item))
    return result


def make_snapshot(
    *,
    stage: str,
    parent_code: str | None,
    package_name: str,
    ledger_version: str,
    candidates: list[dict[str, Any]],
    next_action: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成候选视图和可复现摘要。

    摘要只对规范化代码数组计算，不包含展示名称和说明，避免中文展示名调整导致历史
    快照全部失效。代码顺序保留，因为顺序也是当时模型实际看到的候选顺序。
    """

    codes = [item["代码"] for item in candidates]
    canonical = json.dumps(codes, ensure_ascii=False, separators=(",", ":"))
    result: dict[str, Any] = {
        "阶段代码": stage,
        "父节点代码": parent_code,
        "标签包名称": package_name,
        "标签包版本": ledger_version,
        "候选": candidates,
        "候选代码": codes,
        "候选快照摘要": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "下一步": next_action,
    }
    if extra:
        result.update(extra)
    return result


def route_candidates(
    ledger: dict[str, Any], field: str
) -> tuple[str, list[dict[str, Any]]]:
    """把路由字段统一转换成带代码和中文名称的候选对象。"""

    route = ledger["通用路由候选"]
    field_map = {
        "domains": "业务域",
        "tasks": "拍摄任务",
        "ranges": "拍摄范围",
        "modalities": "图像模态",
        "clarity": "图像清晰程度",
        "occlusion": "遮挡程度",
        "continuation": "可继续标注状态",
    }
    chinese_key = field_map[field]
    raw_items = route[chinese_key]
    candidates = []
    for item in raw_items:
        if isinstance(item, dict):
            candidates.append(item)
        else:
            # 旧台账中的流程枚举是纯字符串；生成稳定代码时保留原中文值，避免另建
            # 一套不必要的流程标签台账。
            candidates.append({"代码": item, "中文名称": item, "标签性质": "流程枚举"})
    return chinese_key, candidates


def subject_candidates(
    scene: dict[str, Any], include_nontrainable: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """返回当前场景可选设施和设备；规划场景只返回提示词，不伪造正式代码。"""

    facilities = [
        item
        for item in scene.get("设施候选", [])
        if is_selectable(item, include_nontrainable)
    ]
    devices = [
        item
        for item in scene.get("设备候选", [])
        if is_selectable(item, include_nontrainable)
    ]
    return facilities, devices, scene.get("规划设备名称", [])


def append_group_candidates(
    target: list[dict[str, Any]],
    group_names: Iterable[str],
    groups: dict[str, Any],
    include_nontrainable: bool,
) -> None:
    """把部件组展平到候选列表，同时过滤说明字段和非正式标签。"""

    for group_name in group_names:
        for item in groups.get(group_name, []):
            if isinstance(item, dict) and is_selectable(item, include_nontrainable):
                target.append(item)


def part_candidates(
    ledger: dict[str, Any],
    scene: dict[str, Any],
    object_code: str,
    index: dict[str, dict[str, Any]],
    include_nontrainable: bool,
) -> tuple[list[dict[str, Any]], str]:
    """返回设施、设备或部件的下一层候选。

    设施和设备使用各自部件组；已经选到部件时优先读取直接子部件映射。配电杆塔
    还会合并场景内的线路元件和标志牌，因为这些对象直接属于杆塔/线路设施。
    """

    candidates: list[dict[str, Any]] = []
    child_map = ledger.get("部件子部件映射", {})
    if object_code in child_map:
        for child_code in child_map[object_code]:
            item = index[child_code]
            if is_selectable(item, include_nontrainable):
                candidates.append(item)
        return deduplicate_candidates(candidates), "子部件"

    device_groups = ledger["设备部件映射"].get(object_code, [])
    append_group_candidates(
        candidates,
        device_groups,
        ledger["设备部件候选"],
        include_nontrainable,
    )

    facility_groups = ledger["设施部件映射"].get(object_code, [])
    append_group_candidates(
        candidates,
        facility_groups,
        ledger["设施部件候选"],
        include_nontrainable,
    )

    if object_code == "POLE_TOWER" and scene.get("代码") == "DIS_OHL":
        for items in scene.get("线路元件候选", {}).values():
            candidates.extend(
                item for item in items if is_selectable(item, include_nontrainable)
            )
        candidates.extend(
            item
            for item in scene.get("杆塔属性候选", {}).get("标志牌_多实例", [])
            if is_selectable(item, include_nontrainable)
        )

    return deduplicate_candidates(candidates), "部件"


def parse_attributes(raw: str | None) -> dict[str, Any]:
    """解析父设施静态属性；要求JSON对象，避免模糊自由文本影响约束。"""

    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--attributes-json不是有效JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit("--attributes-json必须是JSON对象")
    return value


def condition_matches(
    condition: dict[str, Any],
    carrier_code: str,
    attributes: dict[str, Any],
    task: str | None,
) -> bool:
    """匹配当前选择器能够确定执行的跨字段条件。

    来源隔离和油污/渗漏等语义规则由阶段四大模型复核执行，本函数只处理载体、
    材质和拍摄任务等确定性条件。遇到本函数不认识的条件字段时视为不匹配，避免
    误删候选。
    """

    supported = {"载体代码", "父设施属性.杆塔材质", "拍摄任务"}
    if not set(condition).issubset(supported):
        return False
    if "载体代码" in condition and carrier_code not in condition["载体代码"]:
        return False
    if "父设施属性.杆塔材质" in condition:
        if attributes.get("杆塔材质") not in condition["父设施属性.杆塔材质"]:
            return False
    if "拍摄任务" in condition and task not in condition["拍摄任务"]:
        return False
    return True


def defect_candidates(
    ledger: dict[str, Any],
    carrier_code: str,
    parent_object_code: str | None,
    attributes: dict[str, Any],
    task: str | None,
    index: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """按精确映射、载体族、对象映射和跨字段规则生成缺陷候选。

    载体族必须先于父对象回退。例如绝缘子是杆塔的部件，但它不能因为某个新型号
    暂时没有精确映射，就继承杆塔裂纹、倾斜和抱箍锈蚀。候选树可通过
    ``载体代码前缀缺陷映射``把稳定代码族映射到受限缺陷组；只有精确映射和族映射
    都不存在时，才考虑对象自身或显式传入的父对象映射。
    """

    source = "部件缺陷精确映射"
    candidate_codes = list(ledger["部件缺陷精确映射"].get(carrier_code, []))

    if not candidate_codes:
        # 代码前缀规则用于同一专业对象族的安全回退。它不会把全量通用缺陷开放给
        # 模型，只会加载台账中显式声明的一个或多个载体缺陷组。
        matched_groups: list[str] = []
        for mapping in ledger.get("载体代码前缀缺陷映射", []):
            prefix = mapping.get("代码前缀")
            if isinstance(prefix, str) and carrier_code.startswith(prefix):
                matched_groups.extend(mapping.get("载体缺陷组", []))
        if matched_groups:
            source = "载体代码前缀缺陷映射"
            for group in matched_groups:
                candidate_codes.extend(ledger["载体缺陷候选"].get(group, []))

    if not candidate_codes:
        source = "对象缺陷载体映射"
        groups = ledger["对象缺陷载体映射"].get(carrier_code, [])
        if not groups and parent_object_code:
            groups = ledger["对象缺陷载体映射"].get(parent_object_code, [])
        for group in groups:
            candidate_codes.extend(ledger["载体缺陷候选"].get(group, []))

    if not candidate_codes:
        # 不自动开放全量“通用设备或部件”。台账没有建立映射时，只能进入候选外
        # 复核，避免因宽泛回退污染训练标签。
        source = "候选外兜底"
        candidate_codes = ["OTHER_REVIEW"]

    if "OTHER_REVIEW" not in candidate_codes:
        candidate_codes.append("OTHER_REVIEW")

    filtered: list[dict[str, Any]] = []
    filter_audit: list[dict[str, Any]] = []
    for rule in ledger.get("跨字段约束规则", []):
        if not condition_matches(rule.get("条件", {}), carrier_code, attributes, task):
            continue
        denied = set(rule.get("禁止缺陷", []))
        if not denied:
            continue
        retained_codes = []
        for code in candidate_codes:
            if code in denied:
                filter_audit.append(
                    {
                        "缺陷代码": code,
                        "规则编号": rule["规则编号"],
                        "过滤原因": rule["规则名称"],
                    }
                )
            else:
                retained_codes.append(code)
        candidate_codes = retained_codes

    for code in candidate_codes:
        item = index.get(code)
        if item:
            filtered.append(item)
    return deduplicate_candidates(filtered), filter_audit, source


def build_parser() -> argparse.ArgumentParser:
    """定义分阶段命令行接口；子命令名称对应标注动作而非文件结构。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    subparsers = parser.add_subparsers(dest="command", required=True)

    route = subparsers.add_parser("route", help="阶段一：获取一个路由字段的候选")
    route.add_argument(
        "--field",
        required=True,
        choices=(
            "domains",
            "tasks",
            "ranges",
            "modalities",
            "clarity",
            "occlusion",
            "continuation",
        ),
    )

    scene = subparsers.add_parser("scene", help="阶段一：按业务域获取设施场景")
    scene.add_argument("--domain", required=True)

    subject = subparsers.add_parser("subject", help="阶段二：获取场景设施和设备")
    subject.add_argument("--domain", required=True)
    subject.add_argument("--scene", required=True)
    subject.add_argument("--include-nontrainable", action="store_true")

    parts = subparsers.add_parser("parts", help="阶段二：获取对象的部件或子部件")
    parts.add_argument("--domain", required=True)
    parts.add_argument("--scene", required=True)
    parts.add_argument("--object-code", required=True)
    parts.add_argument("--include-nontrainable", action="store_true")

    defects = subparsers.add_parser("defects", help="阶段三：获取当前载体缺陷候选")
    defects.add_argument("--domain", required=True)
    defects.add_argument("--scene", required=True)
    defects.add_argument("--carrier-code", required=True)
    defects.add_argument("--parent-object-code")
    defects.add_argument("--attributes-json")
    defects.add_argument("--task")
    defects.add_argument("--modality")
    defects.add_argument("--audit", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    ledger = load_ledger(args.ledger)
    version = ledger["标签台账版本"]
    index: dict[str, dict[str, Any]] = {}
    build_definition_index(ledger, index)

    if args.command == "route":
        field_name, candidates = route_candidates(ledger, args.field)
        result = make_snapshot(
            stage="ROUTE",
            parent_code=None,
            package_name=f"通用路由候选/{field_name}",
            ledger_version=version,
            candidates=candidates,
            next_action="继续完成阶段一路由字段",
        )

    elif args.command == "scene":
        domain = find_domain(ledger, args.domain)
        candidates = [
            {
                "代码": scene["代码"],
                "中文名称": scene["中文名称"],
                "标签包": scene["标签包"],
                "成熟度": scene["成熟度"],
            }
            for scene in domain["设施场景"]
        ]
        result = make_snapshot(
            stage="ROUTE",
            parent_code=args.domain,
            package_name=f"{domain['业务域']['中文名称']}设施场景",
            ledger_version=version,
            candidates=candidates,
            next_action="选择设施场景并完成图像质量与继续标注状态",
        )

    elif args.command == "subject":
        scene = find_scene(ledger, args.domain, args.scene)
        facilities, devices, planned = subject_candidates(
            scene, args.include_nontrainable
        )
        candidates = deduplicate_candidates(facilities + devices)
        extra = {
            "设施候选代码": [item["代码"] for item in facilities],
            "设备候选代码": [item["代码"] for item in devices],
            "标签包成熟度": scene["成熟度"],
        }
        if planned and not candidates:
            extra["规划设备名称_不可作为正式标签"] = planned
            extra["处理要求"] = "当前场景只有路由级规划词表，应转人工或建立专业标签包"
        result = make_snapshot(
            stage="SUBJECT",
            parent_code=args.scene,
            package_name=scene["标签包"],
            ledger_version=version,
            candidates=candidates,
            next_action="选择可见设施或设备，再按所选对象加载下一层部件",
            extra=extra,
        )

    elif args.command == "parts":
        scene = find_scene(ledger, args.domain, args.scene)
        candidates, child_level = part_candidates(
            ledger,
            scene,
            args.object_code,
            index,
            args.include_nontrainable,
        )
        result = make_snapshot(
            stage="SUBJECT",
            parent_code=args.object_code,
            package_name=scene["标签包"],
            ledger_version=version,
            candidates=candidates,
            next_action=f"选择可见{child_level}；需要时继续查询子部件，否则完成静态属性和对象树",
            extra={"下一层对象类型": child_level},
        )

    else:
        scene = find_scene(ledger, args.domain, args.scene)
        attributes = parse_attributes(args.attributes_json)
        candidates, audit, source = defect_candidates(
            ledger,
            args.carrier_code,
            args.parent_object_code,
            attributes,
            args.task,
            index,
        )
        # 缺陷和“无异常/不可见/待复核”等检查结论是两类不同语义，但它们都在本轮
        # 向模型披露。候选快照因此对两类代码的并集计算摘要，并在每项上标明类型；
        # 这使轨迹校验既能验证缺陷选择，也能验证检查结论选择。
        defect_options = [dict(item, 候选类型="缺陷") for item in candidates]
        check_options = [
            dict(item, 候选类型="检查结论")
            for item in ledger["缺陷检查结论候选"]
        ]
        all_disclosed_options = defect_options + check_options
        extra = {
            "候选来源": source,
            "缺陷候选": defect_options,
            "缺陷候选代码": [item["代码"] for item in defect_options],
            "缺陷检查结论候选": check_options,
            "缺陷检查结论候选代码": [item["代码"] for item in check_options],
            "图像模态": args.modality,
            "静态属性": attributes,
        }
        if args.audit:
            extra["过滤审计"] = audit
        result = make_snapshot(
            stage="DEFECT",
            parent_code=args.carrier_code,
            package_name=f"{scene['标签包']}/载体缺陷候选",
            ledger_version=version,
            candidates=all_disclosed_options,
            next_action="选择缺陷或检查结论，并把缺陷绑定到当前载体及直接可见证据",
            extra=extra,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
