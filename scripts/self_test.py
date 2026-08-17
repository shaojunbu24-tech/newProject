#!/usr/bin/env python3
"""运行Skill的无图像确定性回归测试。

测试覆盖最容易发生规则漂移的路径：

1. 配电架空场景选择三相变压器后，只加载变压器部件；
2. 水泥杆杆体候选中删除腐蚀，角钢塔杆体保留腐蚀；
3. 水泥杆上的金属抱箍仍允许抱箍锈蚀；
4. 一份包含完整候选轨迹、对象树、缺陷和证据的新标注通过阶段四A。
5. 配电十二种绝缘子和输电绝缘子专项不会错误继承父杆塔缺陷；
6. 釉表面灼伤、放电、污秽、电弧烧伤和闪络痕迹均有受控候选。

脚本使用内存对象，不创建或修改用户标注文件。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from select_candidates import (
    DEFAULT_LEDGER,
    build_definition_index,
    defect_candidates,
    find_scene,
    load_ledger,
    part_candidates,
    subject_candidates,
)
from validate_annotation import Validator
from render_captions import render


def make_trace(
    trace_id: str,
    stage: str,
    parent: str | None,
    package: str,
    candidates: list[str],
    choices: list[str],
) -> dict[str, Any]:
    """生成与选择器相同摘要算法的候选轨迹测试对象。"""

    canonical = json.dumps(candidates, ensure_ascii=False, separators=(",", ":"))
    return {
        "轨迹编号": trace_id,
        "阶段代码": stage,
        "父节点代码": parent,
        "标签包名称": package,
        "标签包版本": "2.0",
        "候选代码": candidates,
        "选择代码": choices,
        "候选快照摘要": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "选择结果状态": "已完成",
    }


def disclosed_defect_codes(
    ledger: dict[str, Any],
    index: dict[str, dict[str, Any]],
    carrier: str,
    parent: str | None,
    attributes: dict[str, Any],
) -> list[str]:
    """返回缺陷与检查结论的联合披露代码，模拟阶段三输出。"""

    defects, _, _ = defect_candidates(
        ledger, carrier, parent, attributes, "缺陷巡视", index
    )
    return [item["代码"] for item in defects] + [
        item["代码"] for item in ledger["缺陷检查结论候选"]
    ]


def build_valid_annotation(
    ledger: dict[str, Any], index: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """构造一份最小但完整的杆上变压器异物标注。"""

    scene = find_scene(ledger, "DIS", "DIS_OHL")
    facilities, devices, _ = subject_candidates(scene, False)
    subject_codes = [item["代码"] for item in facilities + devices]
    transformer_parts, _ = part_candidates(
        ledger, scene, "DTR_3P", index, include_nontrainable=False
    )
    part_codes = [item["代码"] for item in transformer_parts]

    route_domain_codes = [
        item["代码"] for item in ledger["通用路由候选"]["业务域"]
    ]
    route_scene_codes = [item["代码"] for item in find_domain_scenes(ledger, "DIS")]
    pole_checks = disclosed_defect_codes(
        ledger, index, "POLE_TOWER", None, {"杆塔材质": "shuinigan"}
    )
    transformer_checks = disclosed_defect_codes(
        ledger, index, "DTR_3P", "POLE_TOWER", {"杆塔材质": "shuinigan"}
    )
    top_checks = disclosed_defect_codes(
        ledger, index, "TR_TANK_TOP", "DTR_3P", {"杆塔材质": "shuinigan"}
    )

    traces = [
        make_trace("轨迹01", "ROUTE", None, "通用路由候选/业务域", route_domain_codes, ["DIS"]),
        make_trace("轨迹02", "ROUTE", "DIS", "配电设施场景", route_scene_codes, ["DIS_OHL"]),
        make_trace("轨迹03", "SUBJECT", "DIS_OHL", scene["标签包"], subject_codes, ["POLE_TOWER", "DTR_3P"]),
        make_trace("轨迹04", "SUBJECT", "DTR_3P", scene["标签包"], part_codes, ["TR_TANK_TOP"]),
        make_trace("轨迹05", "DEFECT", "POLE_TOWER", f"{scene['标签包']}/载体缺陷候选", pole_checks, ["NO_VISIBLE_ANOMALY"]),
        make_trace("轨迹06", "DEFECT", "DTR_3P", f"{scene['标签包']}/载体缺陷候选", transformer_checks, ["NO_VISIBLE_ANOMALY"]),
        make_trace("轨迹07", "DEFECT", "TR_TANK_TOP", f"{scene['标签包']}/载体缺陷候选", top_checks, ["CHECK_DEFECT_FOUND", "FOREIGN_OBJECT"]),
    ]

    return {
        "标注规范版本": "电力设备多模态标注-v2.0",
        "标签台账版本": "2.0",
        "图像基本信息": {"图像编号": "SELF-TEST-001", "原始文件名": "自检示例.jpg"},
        "外部信息": {"文件名解析": [], "设备台账信息": [], "工单信息": []},
        "标注流程状态": {
            "当前阶段": "VALIDATE",
            "阶段状态": {
                "ROUTE": "已完成",
                "SUBJECT": "已完成",
                "DEFECT": "已完成",
                "VALIDATE": "进行中",
                "DERIVE": "未开始",
            },
            "候选披露轨迹": traces,
            "待检查载体队列": [],
            "退回记录": [],
        },
        "图像路由": {
            "业务域": {"代码": "DIS", "中文名称": "配电", "来源": "图像视觉"},
            "设施场景": {"代码": "DIS_OHL", "中文名称": "配电架空线路", "来源": "图像视觉"},
            "拍摄任务": "缺陷巡视",
            "拍摄范围": {"代码": "gantashebei", "中文名称": "设备部件近景"},
            "图像模态": {"代码": "VISIBLE", "中文名称": "可见光图像"},
            "激活标签包": [scene["标签包"]],
        },
        "主要对象主题": {
            "对象编号": "设备01",
            "对象层级": "设备",
            "标签代码": "DTR_3P",
            "中文名称": "三相配电变压器",
            "候选树路径": "DIS/DIS_OHL/DTR_3P",
        },
        "标注对象树": [
            {
                "对象类型": "设施",
                "对象编号": "设施01",
                "标签": {"代码": "POLE_TOWER", "中文名称": "杆塔", "台账版本": "2.0"},
                "候选树路径": "DIS/DIS_OHL/POLE_TOWER",
                "候选披露轨迹编号": "轨迹03",
                "边界框_归一化坐标": [0, 0, 1000, 1000],
                "属性": {"杆塔材质": "shuinigan"},
                "缺陷检查": {"状态": "NO_VISIBLE_ANOMALY", "候选披露轨迹编号": "轨迹05", "检查说明": None},
                "设备实例": [
                    {
                        "对象类型": "设备",
                        "对象编号": "设备01",
                        "标签": {"代码": "DTR_3P", "中文名称": "三相配电变压器", "台账版本": "2.0"},
                        "候选树路径": "DIS/DIS_OHL/DTR_3P",
                        "候选披露轨迹编号": "轨迹03",
                        "边界框_归一化坐标": [100, 100, 900, 900],
                        "属性": [],
                        "缺陷检查": {"状态": "NO_VISIBLE_ANOMALY", "候选披露轨迹编号": "轨迹06", "检查说明": None},
                        "设备实例": [],
                        "部件实例": [
                            {
                                "对象类型": "部件",
                                "对象编号": "部件01",
                                "标签": {"代码": "TR_TANK_TOP", "中文名称": "箱盖", "台账版本": "2.0"},
                                "候选树路径": "变压器通用/TR_TANK_TOP",
                                "候选披露轨迹编号": "轨迹04",
                                "边界框_归一化坐标": [200, 150, 800, 500],
                                "属性": [],
                                "设备实例": [],
                                "部件实例": [],
                                "状态量标注": [],
                                "缺陷检查": {"状态": "CHECK_DEFECT_FOUND", "候选披露轨迹编号": "轨迹07", "检查说明": None},
                                "缺陷实例": [
                                    {
                                        "对象类型": "缺陷",
                                        "缺陷编号": "缺陷01",
                                        "缺陷类别": {"代码": "FOREIGN_OBJECT", "中文名称": "异物", "台账版本": "2.0"},
                                        "候选树路径": "变压器/箱盖/FOREIGN_OBJECT",
                                        "候选披露轨迹编号": "轨迹07",
                                        "载体对象编号": "部件01",
                                        "判定状态": "缺陷",
                                        "边界框_归一化坐标": [300, 200, 700, 450],
                                        "直接可见证据编号": ["证据01"],
                                        "不可确认内容": [],
                                        "视觉识别置信度": 0.95,
                                    }
                                ],
                            }
                        ],
                        "状态量标注": [],
                        "缺陷实例": [],
                    }
                ],
                "部件实例": [],
                "状态量标注": [],
                "缺陷实例": [],
            }
        ],
        "环境标注": [],
        "关联关系": [],
        "视觉证据": [
            {"证据编号": "证据01", "对应对象编号": "缺陷01", "可见事实": "变压器箱盖上存在非设备结构附着物", "证据来源": "图像视觉", "可靠程度": "高"}
        ],
        "不确定信息": [],
    }


def find_domain_scenes(ledger: dict[str, Any], domain_code: str) -> list[dict[str, Any]]:
    """返回业务域场景，保持测试代码不依赖命令行输出。"""

    for domain in ledger["业务候选树"]:
        if domain["业务域"]["代码"] == domain_code:
            return domain["设施场景"]
    raise AssertionError(f"测试台账缺少业务域{domain_code}")


def main() -> None:
    ledger = load_ledger(DEFAULT_LEDGER)
    index: dict[str, dict[str, Any]] = {}
    build_definition_index(ledger, index)
    scene = find_scene(ledger, "DIS", "DIS_OHL")

    transformer_parts, _ = part_candidates(
        ledger, scene, "DTR_3P", index, include_nontrainable=False
    )
    transformer_codes = {item["代码"] for item in transformer_parts}
    assert "TR_TANK_TOP" in transformer_codes
    assert "BRK_OPERATING_MECH" not in transformer_codes

    concrete_codes = set(
        disclosed_defect_codes(
            ledger, index, "TOWER_BODY", "POLE_TOWER", {"杆塔材质": "shuinigan"}
        )
    )
    steel_codes = set(
        disclosed_defect_codes(
            ledger, index, "TOWER_BODY", "POLE_TOWER", {"杆塔材质": "jiaogangta"}
        )
    )
    hoop_codes = set(
        disclosed_defect_codes(
            ledger, index, "TOWER_HOOP", "POLE_TOWER", {"杆塔材质": "shuinigan"}
        )
    )
    assert "CORROSION" not in concrete_codes
    assert "CORROSION" in steel_codes
    assert "HOOP_CORROSION" in hoop_codes

    # 输电架空线路仍不是全专业标签包，但绝缘子专项必须能够建立正式设施和部件
    # 路径，避免所有样本被迫使用OTHER_REVIEW。
    transmission_scene = find_scene(ledger, "TRA", "TRA_OHL")
    tra_facilities, tra_devices, _ = subject_candidates(
        transmission_scene, include_nontrainable=False
    )
    assert {item["代码"] for item in tra_facilities} == {
        "TRA_TOWER",
        "TRA_LINE_CORRIDOR",
    }
    assert tra_devices == []
    tra_parts, _ = part_candidates(
        ledger,
        transmission_scene,
        "TRA_TOWER",
        index,
        include_nontrainable=False,
    )
    tra_part_codes = {item["代码"] for item in tra_parts}
    assert {"TRA_INSULATOR_STRING", "INSULATOR_GENERIC"}.issubset(tra_part_codes)

    # 配电PDF中的十二种绝缘子都必须得到绝缘子专用候选，且绝不能出现杆塔裂纹、
    # 倾斜或抱箍锈蚀。陶瓷绝缘子还应开放釉表面灼伤这一材质专用可见现象。
    insulator_codes = {
        "jyz-boli_xuanshi",
        "jyz-taoci_xuanshi",
        "jyz-fuhe_bangxing_xuanshi",
        "jyz-taoci_bangxing_xuanshi",
        "jyz-taoci_hengdan",
        "jyz-taoci_zhushi",
        "jyz-fuhe_zhenshi",
        "jyz-fuhe_fanglei_zhenshi",
        "jyz-taoci_zhenshi",
        "jyz-taoci_dieshi",
        "jyz-taoci_laxian",
        "jyz-taoci_fanglei_zhushi",
    }
    forbidden_tower_defects = {"TOWER_CRACK", "TOWER_TILT", "HOOP_CORROSION"}
    for insulator_code in insulator_codes:
        disclosed = set(
            disclosed_defect_codes(
                ledger, index, insulator_code, "POLE_TOWER", {}
            )
        )
        assert {"INSULATOR_POLLUTION", "INSULATOR_DISCHARGE_MARK"}.issubset(
            disclosed
        )
        assert not (disclosed & forbidden_tower_defects)
        if "taoci" in insulator_code:
            assert "INSULATOR_GLAZE_BURN_MARK" in disclosed

    glaze_codes = set(
        disclosed_defect_codes(
            ledger, index, "INSULATOR_GLAZE_SURFACE", "PORCELAIN_INSULATOR_BODY", {}
        )
    )
    assert {
        "INSULATOR_GLAZE_BURN_MARK",
        "INSULATOR_DISCHARGE_MARK",
        "INSULATOR_ARC_BURN_MARK",
        "INSULATOR_FLASHOVER_TRACE",
        "INSULATOR_POLLUTION",
    }.issubset(glaze_codes)

    # 模拟未来新增但尚未建立精确映射的jyz型号，验证前缀族回退仍返回绝缘子候选，
    # 而不是沿父对象回退到杆塔结构缺陷。
    future_insulator_codes = set(
        disclosed_defect_codes(
            ledger, index, "jyz-future-type", "POLE_TOWER", {}
        )
    )
    assert "INSULATOR_DISCHARGE_MARK" in future_insulator_codes
    assert not (future_insulator_codes & forbidden_tower_defects)

    annotation = build_valid_annotation(ledger, index)
    report = Validator(annotation, ledger, allow_legacy=False).run()["规则校验"]
    assert report["阻断问题数量"] == 0, json.dumps(report, ensure_ascii=False, indent=2)
    assert report["警告问题数量"] == 0, json.dumps(report, ensure_ascii=False, indent=2)
    captions = render(annotation)
    assert "三相配电变压器" in captions["纯视觉完整描述"]
    assert "异物" in captions["简短检索描述"]

    # Skill随包携带的配电、输电绝缘子严格结构示例必须在生产严格模式下达到零阻断、
    # 零警告，并且任何候选轨迹都不能实际选择OTHER_REVIEW。候选数组仍保留该兜底，
    # 但已覆盖场景应优先选择正式代码或不可判定/复核检查结论。
    example_dir = Path(__file__).resolve().parents[1] / "assets" / "examples"
    for example_name in (
        "绝缘子釉表面灼伤-配电-严格结构示例.json",
        "绝缘子釉表面灼伤-输电-严格结构示例.json",
    ):
        example = json.loads((example_dir / example_name).read_text(encoding="utf-8"))
        chosen_codes = {
            code
            for trace in example["标注流程状态"]["候选披露轨迹"]
            for code in trace["选择代码"]
        }
        assert "OTHER_REVIEW" not in chosen_codes
        example_report = Validator(
            example, ledger, allow_legacy=False
        ).run()["规则校验"]
        assert example_report["阻断问题数量"] == 0, json.dumps(
            example_report, ensure_ascii=False, indent=2
        )
        assert example_report["警告问题数量"] == 0, json.dumps(
            example_report, ensure_ascii=False, indent=2
        )
        assert example_report["包含候选外标签"] is False
        assert example_report["允许进入训练前置条件"] is True
    print("Skill自检通过：渐进候选、材质约束、绝缘子专项、载体缺陷和严格规则校验均符合预期。")


if __name__ == "__main__":
    main()
