"""
豆包输入法 系统字体补丁 - CI/跨平台版
通过命令行参数指定输入输出 APK 路径，兼容 GitHub Actions (Linux) 和本地 (Windows)

修改内容：
  1. KeyboardView.createTypeFace() → 保留图标字体，其余用系统字体
  2. CandidateListView / CandidateIdleView / MoreCandidateSyllableAdapter
     中直接加载 R.font.qihei → 系统字体
  3. 智能联想页面增加“英文单词补全联想”开关
  4. 开关关闭时，英文键盘光标更新不再请求单词补全联想
  5. 关闭开关时，英文 ASCII 标识符不因 SetPreeditRange 重新进入可覆盖编辑状态
  6. 数字/符号键盘切换前提交英文 composing 文本
"""

import argparse
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SMALI_KEYBOARD_VIEW = "com/bytedance/android/input/keyboard/KeyboardView.smali"
SMALI_SETTINGS_CONFIG = "com/bytedance/android/input/common/SettingsConfigNext.smali"
SMALI_KEYBOARD_JNI = "com/bytedance/android/doubaoime/KeyboardJni.smali"
SMALI_KEYBOARD_SELECTION = "com/bytedance/android/doubaoime/KeyboardJni$1.smali"
SMALI_BOARD_SWITCH_DIR = "com/bytedance/android/input/keyboard/areacontrol"
SMALI_ASSOCIATION_FRAGMENT = (
    "com/bytedance/android/input/fragment/settings/"
    "IntelligentAssociationFragment.smali"
)
ASSOCIATION_PATCH_CLASS = (
    "Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;"
)
DEX_METHOD_LIMIT = 65535
PRIMARY_DEX_MIGRATION_THRESHOLD = 65520
DEX_MIGRATION_CLASS = "J/N.smali"

# R.font.qihei 的资源 ID
QIHEI_FONT_RES_ID = "0x7f090003"

KEYSTORE_PASS = "android"
KEY_ALIAS = "androiddebugkey"
KEY_PASS = "android"

# EnglishAssociationPatch.smali 模板中的资源 ID 占位值（1.3.17 的原始 ID）。
# 构建时会解析输入 APK 的 resources.arsc，按资源名替换为当前版本的真实 ID。
ASSOCIATION_RES_ID_NAMES = {
    "0x7f0a0363": "id/item_title",
    "0x7f0a0629": "id/text_container",
    "0x7f0a0347": "id/item_accessory_container",
    "0x7f0703c3": "dimen/ime_dp_15",
    "0x7f0703b8": "dimen/ime_dp_14",
    "0x7f070491": "dimen/ime_dp_6",
    "0x7f060253": "color/ime_color_setting_item_bg",
}


def parse_string_pool(data, offset):
    """解析 ResStringPool 块，返回字符串列表。"""
    pool_type, header_size, _size, string_count, _style_count, flags, strings_start, _styles_start = (
        struct.unpack_from("<HHIIIIII", data, offset)
    )
    if pool_type != 0x0001:
        raise ValueError(f"无效的字符串池块类型: 0x{pool_type:04x}")
    utf8 = bool(flags & 0x100)
    offsets_base = offset + header_size
    strings_base = offset + strings_start
    strings = []
    for index in range(string_count):
        string_offset = struct.unpack_from("<I", data, offsets_base + index * 4)[0]
        pos = strings_base + string_offset
        if utf8:
            char_count = data[pos]
            pos += 2 if char_count & 0x80 else 1
            byte_count = data[pos]
            if byte_count & 0x80:
                byte_count = ((byte_count & 0x7F) << 8) | data[pos + 1]
                pos += 2
            else:
                pos += 1
            strings.append(data[pos:pos + byte_count].decode("utf-8"))
        else:
            char_count = struct.unpack_from("<H", data, pos)[0]
            pos += 2
            if char_count & 0x8000:
                char_count = ((char_count & 0x7FFF) << 16) | struct.unpack_from("<H", data, pos)[0]
                pos += 2
            strings.append(data[pos:pos + char_count * 2].decode("utf-16-le"))
    return strings


def parse_resource_ids(arsc_data):
    """解析 resources.arsc，返回 {"类型/名称": 资源 ID} 映射。"""
    table_type, header_size, _size = struct.unpack_from("<HHI", arsc_data, 0)
    if table_type != 0x0002:
        raise ValueError("无效的 resources.arsc 头部")
    package_count = struct.unpack_from("<I", arsc_data, 8)[0]
    resources = {}
    offset = header_size
    # 跳过全局字符串池
    _t, _h, global_pool_size = struct.unpack_from("<HHI", arsc_data, offset)
    offset += global_pool_size
    for _ in range(package_count):
        chunk_type, chunk_header_size, chunk_size = struct.unpack_from("<HHI", arsc_data, offset)
        if chunk_type != 0x0200:
            offset += chunk_size
            continue
        package_id = struct.unpack_from("<I", arsc_data, offset + 8)[0]
        type_strings_offset, _last_public_type, key_strings_offset = struct.unpack_from(
            "<III", arsc_data, offset + 12 + 256
        )
        type_names = parse_string_pool(arsc_data, offset + type_strings_offset)
        key_names = parse_string_pool(arsc_data, offset + key_strings_offset)
        cursor = offset + chunk_header_size
        chunk_end = offset + chunk_size
        while cursor < chunk_end:
            inner_type, inner_header_size, inner_size = struct.unpack_from("<HHI", arsc_data, cursor)
            if inner_type == 0x0201:  # RES_TABLE_TYPE_TYPE
                type_id = arsc_data[cursor + 8]
                type_flags = arsc_data[cursor + 9]
                entry_count = struct.unpack_from("<I", arsc_data, cursor + 12)[0]
                entries_start = struct.unpack_from("<I", arsc_data, cursor + 16)[0]
                if type_flags != 0:
                    raise ValueError(f"不支持的 type 块 flags: {type_flags}")
                type_name = type_names[type_id - 1]
                for entry_index in range(entry_count):
                    entry_offset = struct.unpack_from(
                        "<I", arsc_data, cursor + inner_header_size + entry_index * 4
                    )[0]
                    if entry_offset == 0xFFFFFFFF:
                        continue
                    entry_pos = cursor + entries_start + entry_offset
                    key_index = struct.unpack_from("<I", arsc_data, entry_pos + 4)[0]
                    resource_id = (package_id << 24) | (type_id << 16) | entry_index
                    key = f"{type_name}/{key_names[key_index]}"
                    resources.setdefault(key, resource_id)
            cursor += inner_size
        offset += chunk_size
    return resources


def resolve_association_resource_ids(input_apk):
    """解析输入 APK 的资源表，返回模板占位 ID → 当前版本真实 ID 的替换映射。"""
    with zipfile.ZipFile(input_apk) as archive:
        arsc_data = archive.read("resources.arsc")
    resources = parse_resource_ids(arsc_data)
    replacements = {}
    for placeholder, resource_name in ASSOCIATION_RES_ID_NAMES.items():
        if resource_name not in resources:
            raise ValueError(f"输入 APK 缺少资源: {resource_name}")
        replacements[placeholder] = f"0x{resources[resource_name]:08x}"
    return replacements


def read_dex_id_sizes(dex_data):
    """读取 DEX 头部的主要 ID 表数量。"""
    if len(dex_data) < 0x70 or not dex_data.startswith(b"dex\n"):
        raise ValueError("无效的 DEX 文件")

    def uint32(offset):
        return int.from_bytes(dex_data[offset:offset + 4], "little")

    return {
        "strings": uint32(0x38),
        "types": uint32(0x40),
        "protos": uint32(0x48),
        "fields": uint32(0x50),
        "methods": uint32(0x58),
        "classes": uint32(0x60),
    }


def select_helper_dex(dex_entries):
    """选择 method_ids 余量最大的次 DEX 存放辅助类。"""
    candidates = []
    for name, data in dex_entries.items():
        if name == "classes.dex":
            continue
        sizes = read_dex_id_sizes(data)
        candidates.append((sizes["methods"], name, sizes))
    if not candidates:
        raise ValueError("APK 不含次 DEX，无法安全安装英文联想辅助类")
    _, name, sizes = min(candidates)
    return name, sizes


def get_method_bounds(smali, declaration):
    """返回指定 smali 方法的起止位置。"""
    start = smali.index(declaration)
    end = smali.index(".end method", start) + len(".end method")
    return start, end


def insert_after_registers(method, snippet):
    """在方法 .registers 指令后插入代码。"""
    match = re.search(r"(^\s*\.registers\s+\d+\s*$)", method, re.MULTILINE)
    if not match:
        raise ValueError("方法中未找到 .registers")
    pos = match.end()
    return method[:pos] + "\n\n" + snippet.rstrip() + method[pos:]


def replace_method(smali, declaration, transform):
    """定位并转换一个 smali 方法。"""
    start, end = get_method_bounds(smali, declaration)
    old_method = smali[start:end]
    new_method = transform(old_method)
    if new_method == old_method:
        raise ValueError(f"方法未发生修改: {declaration}")
    return smali[:start] + new_method + smali[end:]


def replace_method_candidates(smali, declarations, transform, description):
    """从多个版本的方法签名中选择唯一存在的一项并转换。"""
    matches = [declaration for declaration in declarations if declaration in smali]
    if len(matches) != 1:
        raise ValueError(
            f"{description} 方法签名匹配数量异常: {len(matches)}，候选={declarations}"
        )
    print(f"    ✓ {description}: {matches[0]}")
    return replace_method(smali, matches[0], transform)


def detect_settings_sync_methods(settings_smali_path, keyboard_jni_path):
    """探测 SettingsConfigNext 的写入入口、设置进程写入者与输入法进程写入方法。

    - 写入入口：static (String,Object)V 方法，方法体通过 {p0, p1} 委托给同签名写入者
    - 设置进程写入者：写入入口的委托目标（补丁 hook 点）
    - 输入法进程写入者：KeyboardJni.updateSettingsStringValue 调用的
      SettingsConfigNext.(String,String)V 实例方法（补丁 hook 点）

    返回 (entry, settings_writer, ime_writer)，探测失败的位置为 None。
    """
    settings_smali = settings_smali_path.read_text(encoding="utf-8")
    entries = []
    for match in re.finditer(
        r"\.method public static final (\w+)\(Ljava/lang/String;Ljava/lang/Object;\)V\n"
        r"(.*?)\n\.end method",
        settings_smali,
        re.DOTALL,
    ):
        delegate = re.search(
            r"invoke-static \{p0, p1\}, "
            r"Lcom/bytedance/android/input/common/SettingsConfigNext;->"
            r"(\w+)\(Ljava/lang/String;Ljava/lang/Object;\)V",
            match.group(2),
        )
        if delegate:
            entries.append((match.group(1), delegate.group(1)))
    entry = settings_writer = None
    if len(entries) == 1:
        entry, settings_writer = entries[0]
        print(f"    ✓ 设置写入入口: {entry} → 写入者 {settings_writer}")
    else:
        print(f"    ⚠ 设置写入入口探测异常: {entries}")

    ime_writer = None
    keyboard_smali = keyboard_jni_path.read_text(encoding="utf-8")
    sync_match = re.search(
        r"\.method public static updateSettingsStringValue"
        r"\(Ljava/lang/String;Ljava/lang/String;\)V\n"
        r"(.*?)\n\.end method",
        keyboard_smali,
        re.DOTALL,
    )
    if sync_match:
        target = re.search(
            r"invoke-virtual \{[vp]\d+, p0, p1\}, "
            r"Lcom/bytedance/android/input/common/SettingsConfigNext;->"
            r"(\w+)\(Ljava/lang/String;Ljava/lang/String;\)V",
            sync_match.group(1),
        )
        if target:
            ime_writer = target.group(1)
    if ime_writer:
        print(f"    ✓ 输入法进程写入方法: {ime_writer}")
    else:
        print("    ⚠ 输入法进程写入方法探测失败，回退候选签名")
    return entry, settings_writer, ime_writer


def patch_settings_config(smali_path, settings_writer=None, ime_writer=None):
    """让自定义布尔配置复用原有 SettingsConfigNext 跨进程同步链路。"""
    print("  修补 SettingsConfigNext 自定义配置支持...")
    smali = smali_path.read_text(encoding="utf-8")

    get_snippet = f"""    invoke-static {{p0}}, {ASSOCIATION_PATCH_CLASS}->isPatchKey(Ljava/lang/String;)Z

    move-result v0

    if-eqz v0, :patch_assoc_get_continue

    invoke-static {{}}, {ASSOCIATION_PATCH_CLASS}->isEnabled()Z

    move-result v0

    invoke-static {{v0}}, Ljava/lang/Boolean;->valueOf(Z)Ljava/lang/Boolean;

    move-result-object v0

    return-object v0

    :patch_assoc_get_continue"""
    smali = replace_method(
        smali,
        ".method public static final f(Ljava/lang/String;)Ljava/lang/Object;",
        lambda method: insert_after_registers(method, get_snippet),
    )

    get_default_snippet = f"""    invoke-static {{p0}}, {ASSOCIATION_PATCH_CLASS}->isPatchKey(Ljava/lang/String;)Z

    move-result v0

    if-eqz v0, :patch_assoc_get_default_continue

    invoke-static {{}}, {ASSOCIATION_PATCH_CLASS}->isEnabled()Z

    move-result v0

    invoke-static {{v0}}, Ljava/lang/Boolean;->valueOf(Z)Ljava/lang/Boolean;

    move-result-object v0

    return-object v0

    :patch_assoc_get_default_continue"""
    smali = replace_method(
        smali,
        ".method public static final g(Ljava/lang/String;Ljava/lang/Object;)Ljava/lang/Object;",
        lambda method: insert_after_registers(method, get_default_snippet),
    )

    set_object_snippet = f"""    invoke-static {{p0}}, {ASSOCIATION_PATCH_CLASS}->isPatchKey(Ljava/lang/String;)Z

    move-result v0

    if-eqz v0, :patch_assoc_set_object_continue

    instance-of v0, p1, Ljava/lang/Boolean;

    if-eqz v0, :patch_assoc_set_object_continue

    check-cast p1, Ljava/lang/Boolean;

    invoke-virtual {{p1}}, Ljava/lang/Boolean;->booleanValue()Z

    move-result v0

    invoke-static {{v0}}, {ASSOCIATION_PATCH_CLASS}->setEnabled(Z)V

    return-void

    :patch_assoc_set_object_continue"""
    if settings_writer:
        set_object_declarations = [
            f".method public static final {settings_writer}"
            "(Ljava/lang/String;Ljava/lang/Object;)V"
        ]
    else:
        set_object_declarations = [
            ".method public static final o(Ljava/lang/String;Ljava/lang/Object;)V",
            ".method public static final p(Ljava/lang/String;Ljava/lang/Object;)V",
        ]
    smali = replace_method_candidates(
        smali,
        set_object_declarations,
        lambda method: insert_after_registers(method, set_object_snippet),
        "设置进程写入",
    )

    set_string_snippet = f"""    invoke-static {{p1}}, {ASSOCIATION_PATCH_CLASS}->isPatchKey(Ljava/lang/String;)Z

    move-result v0

    if-eqz v0, :patch_assoc_set_string_continue

    invoke-static {{p2}}, {ASSOCIATION_PATCH_CLASS}->setFromString(Ljava/lang/String;)V

    return-void

    :patch_assoc_set_string_continue"""
    if ime_writer:
        set_string_declarations = [
            f".method public final {ime_writer}(Ljava/lang/String;Ljava/lang/String;)V"
        ]
    else:
        set_string_declarations = [
            ".method public final m(Ljava/lang/String;Ljava/lang/String;)V",
            ".method public final o(Ljava/lang/String;Ljava/lang/String;)V",
        ]
    smali = replace_method_candidates(
        smali,
        set_string_declarations,
        lambda method: insert_after_registers(method, set_string_snippet),
        "输入法进程写入",
    )

    known_key_old = """    move-result p1

    if-nez p1, :cond_7e
"""
    known_key_new = f"""    move-result p1

    if-nez p1, :cond_7e

    invoke-static {{p2}}, {ASSOCIATION_PATCH_CLASS}->isPatchKey(Ljava/lang/String;)Z

    move-result p1

    if-nez p1, :cond_7e
"""
    start, end = get_method_bounds(
        smali,
        ".method public onSharedPreferenceChanged("
        "Landroid/content/SharedPreferences;Ljava/lang/String;)V",
    )
    method = smali[start:end]
    if method.count(known_key_old) != 1:
        raise ValueError("onSharedPreferenceChanged containsKey 分支不符合预期")
    method = method.replace(known_key_old, known_key_new, 1)
    smali = smali[:start] + method + smali[end:]

    checks = {
        "自定义配置读取": "patch_assoc_get_continue" in smali,
        "自定义配置默认读取": "patch_assoc_get_default_continue" in smali,
        "设置进程写入": "patch_assoc_set_object_continue" in smali,
        "输入法进程写入": "patch_assoc_set_string_continue" in smali,
        "未知键转为已知键": known_key_new in smali,
    }
    for desc, ok in checks.items():
        print(f"    [{'✓' if ok else '✗'}] {desc}")
    if not all(checks.values()):
        return False

    smali_path.write_text(smali, encoding="utf-8")
    return True


def patch_selection_association(smali_path):
    """在 SelectionUpdatedParams 送入 native 前过滤英文光标联想。"""
    print("  修补 KeyboardJni 光标联想参数...")
    smali = smali_path.read_text(encoding="utf-8")

    field_patches = [
        (
            "need_association:Z",
            "filterNeedAssociation",
        ),
        (
            "is_cursor_change_tag_for_association_disabled:Z",
            "filterAssociationDisabled",
        ),
    ]
    for field_name, helper_method in field_patches:
        pattern = re.compile(
            rf"(?P<indent>^[ \t]*)iput-boolean "
            rf"(?P<value>[vp]\d+), (?P<object>[vp]\d+), "
            rf"Lcom/bytedance/android/doubaoime/KeyboardJni\$SelectionUpdatedParams;"
            rf"->{re.escape(field_name)}$",
            re.MULTILINE,
        )
        match = pattern.search(smali)
        if not match or len(pattern.findall(smali)) != 1:
            print(f"    ✗ 字段 {field_name} 写入点数量不为 1")
            return False
        indent = match.group("indent")
        value_reg = match.group("value")
        object_reg = match.group("object")
        replacement = (
            f"{indent}invoke-static {{{value_reg}}}, {ASSOCIATION_PATCH_CLASS}"
            f"->{helper_method}(Z)Z\n\n"
            f"{indent}move-result {value_reg}\n\n"
            f"{indent}iput-boolean {value_reg}, {object_reg}, "
            "Lcom/bytedance/android/doubaoime/KeyboardJni$SelectionUpdatedParams;"
            f"->{field_name}"
        )
        smali = pattern.sub(replacement, smali, count=1)
        print(f"    ✓ 已过滤 {field_name}")

    smali_path.write_text(smali, encoding="utf-8")
    return True


def patch_keyboard_preedit_behavior(smali_path):
    """阻止英文标识符在数字/符号切换后重新进入 composing 状态。"""
    print("  修补 KeyboardJni 英文 preedit 范围...")
    smali = smali_path.read_text(encoding="utf-8")

    track_snippet = f"""    invoke-static {{p0}}, {ASSOCIATION_PATCH_CLASS}->trackEnglishPreedit(Ljava/lang/String;)V"""
    smali = replace_method(
        smali,
        ".method public static UpdatePreedit(Ljava/lang/String;)V",
        lambda method: insert_after_registers(method, track_snippet),
    )

    range_snippet = f"""    invoke-static {{v0, p0, p1}}, {ASSOCIATION_PATCH_CLASS}->suppressAsciiPreeditRange(Lcom/bytedance/android/input/editor/a;II)Z

    move-result v1

    if-eqz v1, :patch_ascii_preedit_continue

    return-void

    :patch_ascii_preedit_continue"""
    smali = replace_method(
        smali,
        ".method public static SetPreeditRange(II)V",
        lambda method: method.replace(
            "    :cond_21\n",
            "    :cond_21\n" + range_snippet + "\n\n",
            1,
        ),
    )

    finish_snippet = f"""    invoke-static {{}}, {ASSOCIATION_PATCH_CLASS}->clearEnglishPreeditActive()V

    invoke-static {{}}, Lcom/bytedance/android/doubaoime/KeyboardJni;->resetPreEditStartPosition()V"""
    smali = replace_method(
        smali,
        ".method public static finishPreedit(Z)V",
        lambda method: insert_after_registers(method, finish_snippet),
    )

    checks = {
        "跟踪英文 preedit": "trackEnglishPreedit" in smali,
        "过滤 ASCII preedit 范围": "patch_ascii_preedit_continue" in smali,
        "结束时重置 preedit 起点": "clearEnglishPreeditActive" in smali,
    }
    for desc, ok in checks.items():
        print(f"    [{'✓' if ok else '✗'}] {desc}")
    if not all(checks.values()):
        return False

    smali_path.write_text(smali, encoding="utf-8")
    return True


def patch_association_fragment(smali_path):
    """在智能联想页面程序化添加英文联想开关。"""
    print("  修补 IntelligentAssociationFragment 设置页面...")
    smali = smali_path.read_text(encoding="utf-8")
    declaration = (
        ".method public onViewCreated("
        "Landroid/view/View;Landroid/os/Bundle;)V"
    )
    start, end = get_method_bounds(smali, declaration)
    method = smali[start:end]
    anchor = (
        "    invoke-super {p0, p1, p2}, "
        "Lcom/bytedance/android/input/fragment/settings/BaseSettingsFragment;"
        "->onViewCreated(Landroid/view/View;Landroid/os/Bundle;)V\n"
    )
    if method.count(anchor) != 1:
        print("    ✗ onViewCreated 锚点数量不为 1")
        return False
    addition = (
        anchor
        + "\n    invoke-static {p0, p1}, "
        + ASSOCIATION_PATCH_CLASS
        + "->attach(Lcom/bytedance/android/input/fragment/settings/"
        "IntelligentAssociationFragment;Landroid/view/View;)V\n"
    )
    method = method.replace(anchor, addition, 1)
    smali = smali[:start] + method + smali[end:]
    smali_path.write_text(smali, encoding="utf-8")
    print("    ✓ 已添加英文单词补全联想开关")
    return True


def patch_board_switch_preedit(smali_path):
    """切换到数字或符号键盘前提交英文 composing 文本。"""
    print("  修补英文 preedit 的数字/符号键盘切换...")
    smali = smali_path.read_text(encoding="utf-8")
    helper_call = (
        "    invoke-static {}, "
        f"{ASSOCIATION_PATCH_CLASS}->commitEnglishPreeditBeforeBoardSwitch()V\n\n"
    )
    anchors = [
        (
            "数字键盘",
            "    const/4 v0, 0x5\n\n"
            "    invoke-virtual {p1, v0}, "
            "Lcom/bytedance/android/doubaoime/KeyboardJni;->switchKeyboard(I)V",
        ),
        (
            "符号键盘",
            "    invoke-virtual {p1, v1}, "
            "Lcom/bytedance/android/doubaoime/KeyboardJni;->switchKeyboard(I)V",
        ),
    ]
    for desc, anchor in anchors:
        if smali.count(anchor) != 1:
            print(f"    ✗ {desc} switchKeyboard 锚点数量不为 1")
            return False
        replacement = anchor.replace(
            "    invoke-virtual",
            helper_call + "    invoke-virtual",
            1,
        )
        smali = smali.replace(anchor, replacement, 1)
        print(f"    ✓ {desc}切换前提交英文 preedit")

    smali_path.write_text(smali, encoding="utf-8")
    return True


def find_board_switch_smali(classes_out):
    """定位同时处理数字与符号键盘切换的 InputViewRoot 内部类。"""
    search_dir = classes_out / SMALI_BOARD_SWITCH_DIR
    matches = []
    for path in search_dir.glob("InputViewRoot$*.smali"):
        content = path.read_text(encoding="utf-8")
        if (
            "InputBoardType;->kNumber" in content
            and "InputBoardType;->kSymbol" in content
            and content.count("->switchKeyboard(I)V") >= 2
        ):
            matches.append(path)
    if len(matches) != 1:
        print(f"    ✗ 数字/符号键盘切换类匹配数量异常: {len(matches)}")
        return None
    print(f"    ✓ 数字/符号键盘切换类: {matches[0].name}")
    return matches[0]


def install_english_association_patch(classes_out, helper_classes_out, resource_ids):
    """复制辅助类并修改设置、UI、光标联想三个入口。"""
    print()
    print("  安装英文单词补全联想设置补丁...")
    template_dir = SCRIPT_DIR / "smali"
    target_dir = (
        helper_classes_out
        / "com/bytedance/android/input/fragment/settings"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    for filename in [
        "EnglishAssociationPatch.smali",
        "EnglishAssociationPatch$1.smali",
    ]:
        source = template_dir / filename
        if not source.exists():
            print(f"    ✗ 缺少补丁模板: {source}")
            return False
        shutil.copy2(source, target_dir / filename)
        print(f"    ✓ 已复制 {filename}")

    settings_path = classes_out / SMALI_SETTINGS_CONFIG
    keyboard_jni_path = classes_out / SMALI_KEYBOARD_JNI
    selection_path = classes_out / SMALI_KEYBOARD_SELECTION
    board_switch_path = find_board_switch_smali(classes_out)
    fragment_path = classes_out / SMALI_ASSOCIATION_FRAGMENT
    if board_switch_path is None:
        return False
    for path in [
        settings_path,
        keyboard_jni_path,
        selection_path,
        board_switch_path,
        fragment_path,
    ]:
        if not path.exists():
            print(f"    ✗ 找不到目标 Smali: {path}")
            return False

    association_patch_path = target_dir / "EnglishAssociationPatch.smali"
    association_smali = association_patch_path.read_text(encoding="utf-8")
    for placeholder, resolved in resource_ids.items():
        if placeholder not in association_smali:
            print(f"    ✗ 模板缺少占位资源 ID: {placeholder}")
            return False
        association_smali = association_smali.replace(placeholder, resolved)
        print(f"    ✓ 资源 ID {placeholder} → {resolved}")
    association_patch_path.write_text(association_smali, encoding="utf-8")

    entry, settings_writer, ime_writer = detect_settings_sync_methods(
        settings_path, keyboard_jni_path
    )
    if entry is None:
        print("    ✗ 无法确定设置写入入口，拒绝构建")
        return False
    listener_path = target_dir / "EnglishAssociationPatch$1.smali"
    listener_smali = listener_path.read_text(encoding="utf-8")
    listener_target = (
        "Lcom/bytedance/android/input/common/SettingsConfigNext;->"
        "l(Ljava/lang/String;Ljava/lang/Object;)V"
    )
    if listener_target not in listener_smali:
        print("    ✗ 监听器模板缺少 SettingsConfigNext 调用占位")
        return False
    listener_smali = listener_smali.replace(
        listener_target,
        "Lcom/bytedance/android/input/common/SettingsConfigNext;->"
        f"{entry}(Ljava/lang/String;Ljava/lang/Object;)V",
    )
    listener_path.write_text(listener_smali, encoding="utf-8")
    print(f"    ✓ 监听器写入入口回填为 {entry}")

    return (
        patch_settings_config(settings_path, settings_writer, ime_writer)
        and patch_keyboard_preedit_behavior(keyboard_jni_path)
        and patch_selection_association(selection_path)
        and patch_board_switch_preedit(board_switch_path)
        and patch_association_fragment(fragment_path)
    )


def migrate_primary_class(classes_out, helper_classes_out, relative_path):
    """将仅由次 DEX 使用的类迁出主 DEX，以释放 method_ids。"""
    source = classes_out / relative_path
    target = helper_classes_out / relative_path
    if not source.exists():
        print(f"    ✗ 找不到可迁移类: {source}")
        return False
    if target.exists():
        print(f"    ✗ 目标 DEX 已包含同名类: {target}")
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    print(f"    ✓ 已将 {relative_path} 迁移到辅助 DEX")
    return True


def find_qihei_loading_files(classes_out_dir):
    """
    自动检测所有加载 qihei 字体 (0x7f090003) 的 smali 文件。
    排除 KeyboardView.smali（由 patch_smali 单独处理）。
    """
    results = []
    for smali_path in Path(classes_out_dir).rglob("*.smali"):
        try:
            content = smali_path.read_text(encoding="utf-8")
            if QIHEI_FONT_RES_ID in content and "ResourcesCompat;->getFont" in content:
                if "KeyboardView.smali" not in smali_path.name:
                    results.append(smali_path)
        except Exception:
            pass
    return results


def find_java():
    java_path = os.environ.get("JAVA") or shutil.which("java")
    if not java_path:
        for jdk in [
            r"C:\Program Files\Java\jdk-21.0.10\bin\java.exe",
            r"C:\Program Files\Java\jdk-17\bin\java.exe",
        ]:
            if os.path.exists(jdk):
                return jdk
    return java_path


def find_apksigner():
    configured = os.environ.get("APKSIGNER")
    if configured and Path(configured).is_file():
        return configured

    on_path = shutil.which("apksigner") or shutil.which("apksigner.bat")
    if on_path:
        return on_path

    android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not android_home:
        for c in [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk",
            Path.home() / "Android" / "Sdk",
            Path.home() / "Library" / "Android" / "sdk",
            Path("/usr/local/lib/android/sdk"),
        ]:
            if c.exists():
                android_home = str(c)
                break

    if android_home:
        build_tools = Path(android_home) / "build-tools"
        if build_tools.exists():
            def version_key(path):
                return tuple(int(part) for part in re.findall(r"\d+", path.name))

            for v in sorted(build_tools.iterdir(), key=version_key, reverse=True):
                if not v.is_dir():
                    continue
                apksigner = v / ("apksigner.bat" if os.name == "nt" else "apksigner")
                if apksigner.exists():
                    return str(apksigner)
    return None


def find_jarsigner():
    jarsigner = shutil.which("jarsigner")
    if not jarsigner:
        for jdk_home in [
            r"C:\Program Files\Java\jdk-21.0.10",
            r"C:\Program Files\Java\jdk-17",
        ]:
            p = os.path.join(jdk_home, "bin", "jarsigner.exe")
            if os.path.exists(p):
                return p
    return jarsigner


def ensure_debug_keystore(keystore_path):
    if Path(keystore_path).exists():
        return True
    Path(keystore_path).parent.mkdir(parents=True, exist_ok=True)
    keytool = shutil.which("keytool")
    if not keytool:
        for jdk_home in [
            r"C:\Program Files\Java\jdk-21.0.10",
            r"C:\Program Files\Java\jdk-17",
        ]:
            p = os.path.join(jdk_home, "bin", "keytool.exe")
            if os.path.exists(p):
                keytool = p
                break
    if not keytool:
        print("错误：找不到 keytool")
        return False
    subprocess.run([
        keytool, "-genkey", "-v",
        "-keystore", str(keystore_path),
        "-alias", KEY_ALIAS,
        "-storepass", KEYSTORE_PASS,
        "-keypass", KEY_PASS,
        "-keyalg", "RSA", "-keysize", "2048",
        "-validity", "10000",
        "-dname", "CN=Android Debug,O=Android,C=US",
        "-noprompt",
    ], check=True)
    print(f"已创建 Debug 证书: {keystore_path}")
    return True


def patch_smali(smali_path):
    print(f"读入: {smali_path}")
    with open(smali_path, "r", encoding="utf-8") as f:
        smali = f.read()

    mi = smali.index(
        ".method public static createTypeFace(Ljava/lang/String;ZI)"
        "Landroid/graphics/Typeface;"
    )
    ei = smali.index(".end method", mi)

    update_call = (
        "    invoke-static {},"
        " Lcom/bytedance/android/doubaoime/KeyboardJni;"
        "->updateIsSupportSystemFont()V\n"
    )
    if update_call in smali:
        smali = smali.replace(update_call, "")
        print("  ✓ 已移除 updateIsSupportSystemFont() 调用")
    else:
        print("  - updateIsSupportSystemFont() 调用不存在")

    old_start = smali.index('const-string v2, "_adaptive"', mi, ei)
    catch_pos = smali.index(".catch Ljava/lang/Exception;", old_start, ei)
    end_of_catch = smali.index("\n", catch_pos) + 1

    old_text = smali[old_start:end_of_catch]
    new_text = (
        '    invoke-static {p0, v0},'
        " Landroid/graphics/Typeface;->create(Ljava/lang/String;I)"
        "Landroid/graphics/Typeface;\n"
        "\n"
        "    move-result-object p0\n"
        "\n"
        "    :try_end_9a\n"
        "    .catch Ljava/lang/Exception;"
        " {:try_start_8 .. :try_end_9a} :catch_9b\n"
    )

    count = smali.count(old_text)
    if count != 1:
        print(f"错误：old_text 出现 {count} 次，期望恰好 1 次")
        return False

    smali = smali.replace(old_text, new_text)

    vmi = smali.index(".method public static createTypeFace")
    vei = smali.index(".end method", vmi)
    method = smali[vmi:vei]

    checks = {
        "updateIsSupportSystemFont 已删除": "updateIsSupportSystemFont" not in method,
        "oimeui2023 图标字体路径保留": "oimeui2023" in method,
        "oimeui2025 图标字体路径保留": "oimeui2025" in method,
        "_adaptive 判断已删除": "_adaptive" not in method,
        "Typeface.create(String) 已添加": "invoke-static {p0, v0}, Landroid/graphics/Typeface;->create" in method,
        "createFromAsset 皮肤字体保留": "createFromAsset" in method,
        "ResourcesCompat.getFont 保留": "ResourcesCompat" in method,
        "qihei 默认字体已删除": "0x7f090003" not in method,
    }

    all_ok = True
    for desc, ok in checks.items():
        status = "✓" if ok else "✗"
        if not ok:
            all_ok = False
        print(f"  [{status}] {desc}")

    if all_ok:
        with open(smali_path, "w", encoding="utf-8") as f:
            f.write(smali)
        print(f"已写入: {smali_path}")
        return True
    else:
        print("错误：验证未通过，不写入文件")
        return False


def patch_qihei_direct_loads(smali_path, description):
    """
    修补 smali 文件中直接通过 ResourcesCompat.getFont(context, R.font.qihei)
    加载字体的代码，替换为 Typeface.create("sans-serif", Typeface.NORMAL)。
    """
    print(f"  修补: {description}")
    print(f"  文件: {smali_path}")

    if not smali_path.exists():
        print(f"  ✗ 文件不存在: {smali_path}")
        return False

    with open(smali_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    patched_count = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if QIHEI_FONT_RES_ID in stripped and (
            stripped.startswith("const ") or
            stripped.startswith("const/high16 ") or
            stripped.startswith("const-wide ")
        ):
            parts = stripped.split()
            if len(parts) >= 2:
                res_reg = parts[1].rstrip(",")

                found_invoke = False
                for j in range(i + 1, min(i + 8, len(lines))):
                    invoke_line = lines[j].strip()
                    if ("invoke-static" in invoke_line and
                            "ResourcesCompat;->getFont" in invoke_line):

                        m = re.search(
                            r'invoke-static\s*\{([^}]+)\}',
                            invoke_line
                        )
                        if m:
                            regs = [r.strip() for r in m.group(1).split(",")]
                            if len(regs) == 2:
                                ctx_reg = regs[0]

                                result_reg = None
                                for k in range(j + 1, min(j + 4, len(lines))):
                                    mr_line = lines[k].strip()
                                    if mr_line.startswith("move-result-object"):
                                        result_reg = mr_line.split()[1]
                                        break

                                lines[i] = (
                                    f'    const-string v2, '
                                    f'"sans-serif"\n'
                                )

                                style_line = (
                                    f'    const/4 v1, 0x0\n'
                                )
                                lines.insert(j, style_line)

                                lines[j + 1] = (
                                    f'    invoke-static {{v2, v1}},'
                                    f' Landroid/graphics/Typeface;'
                                    f'->create(Ljava/lang/String;I)'
                                    f'Landroid/graphics/Typeface;\n'
                                )

                                patched_count += 1
                                found_invoke = True
                                print(
                                    f"    ✓ 已替换 qihei 加载点 "
                                    f"(行 {i+1}, 寄存器 {ctx_reg}, "
                                    f"结果 → {result_reg})"
                                )
                                i = j + 2
                                break

                if not found_invoke:
                    i += 1
            else:
                i += 1
        else:
            i += 1

    if patched_count == 0:
        print(f"  ⚠ 未找到需要替换的 qihei 加载点")
        return True

    print(f"  共替换 {patched_count} 处 qihei 加载点")

    with open(smali_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"  ✓ 已写入: {smali_path}")
    return True


def build_apk(input_apk, output_apk, keystore_path):
    print("=" * 60)
    print("豆包输入法 系统字体补丁")
    print("=" * 60)
    print()

    input_apk = Path(input_apk)
    output_apk = Path(output_apk)
    keystore_path = Path(keystore_path)

    # 1. 检查依赖
    print("[1/7] 检查环境依赖...")
    java = find_java()
    if not java:
        print("错误：找不到 Java")
        return False
    print(f"  Java: {java}")

    apksigner = find_apksigner()
    jarsigner = None
    if apksigner:
        print(f"  apksigner: {apksigner}")
    else:
        print("  apksigner: 未找到")
        jarsigner = find_jarsigner()
        if not jarsigner:
            print("错误：找不到 apksigner 或 jarsigner")
            return False
        print(f"  jarsigner: {jarsigner}")

    baksmali_jar = Path(os.environ.get("BAKSMALI_JAR", str(SCRIPT_DIR / "baksmali.jar")))
    smali_jar = Path(os.environ.get("SMALI_JAR", str(SCRIPT_DIR / "smali.jar")))

    for jar_path in [baksmali_jar, smali_jar]:
        if not jar_path.exists():
            print(f"错误：找不到 {jar_path}")
            return False
    print(f"  baksmali.jar: {baksmali_jar}")
    print(f"  smali.jar: {smali_jar}")

    if not input_apk.exists():
        print(f"错误：找不到原版 APK: {input_apk}")
        return False
    print(f"  原版 APK: {input_apk}")
    print()

    # 2. 工作目录
    print("[2/7] 创建工作目录...")
    temp_root = Path(os.environ.get("TEMP", os.environ.get("TMPDIR", tempfile.gettempdir())))
    temp_root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="doubao_patch_", dir=temp_root))
    print(f"  工作目录: {work_dir}")

    # 3. 提取全部 DEX
    print("[3/7] 提取 DEX...")
    with zipfile.ZipFile(input_apk) as z:
        dex_names = sorted(
            name for name in z.namelist()
            if re.fullmatch(r"classes(?:\d+)?\.dex", name)
        )
        dex_entries = {name: z.read(name) for name in dex_names}
        dex_data = dex_entries["classes.dex"]
        dex_paths = {}
        for name, data in dex_entries.items():
            dex_paths[name] = work_dir / name
            dex_paths[name].write_bytes(data)
        helper_dex_name, _ = select_helper_dex(dex_entries)

    try:
        association_resource_ids = resolve_association_resource_ids(input_apk)
    except Exception as exc:
        print(f"错误：解析 resources.arsc 失败: {exc}")
        return False
    print("  ✓ 已解析英文联想开关所需资源 ID")
    primary_dex_sizes = read_dex_id_sizes(dex_data)
    for name, data in dex_entries.items():
        sizes = read_dex_id_sizes(data)
        suffix = "，辅助类目标" if name == helper_dex_name else ""
        print(
            f"  {name}: MD5={hashlib.md5(data).hexdigest()}, "
            f"methods={sizes['methods']}/{DEX_METHOD_LIMIT}, "
            f"size={len(data)}{suffix}"
        )

    # 4. 反编译
    print(f"[4/7] 反编译 {len(dex_entries)} 个 DEX...")
    dex_out_dirs = {}
    for name, dex_path in dex_paths.items():
        out_dir = work_dir / f"{Path(name).stem}_out"
        subprocess.run(
            [java, "-jar", str(baksmali_jar),
             "disassemble", str(dex_path), "-o", str(out_dir)],
            check=True, capture_output=True,
        )
        dex_out_dirs[name] = out_dir
        print(f"  ✓ {name}")
    classes_out = dex_out_dirs["classes.dex"]
    helper_classes_out = dex_out_dirs[helper_dex_name]

    # 5. 打补丁
    print("[5/7] 修改 smali 文件...")
    smali_path = classes_out / SMALI_KEYBOARD_VIEW
    if not smali_path.exists():
        print(f"错误：找不到 {smali_path}")
        return False
    if not patch_smali(smali_path):
        return False

    # 修补候选栏拼音显示区域的字体（自动检测）
    print()
    print("  修补候选栏拼音显示区域字体（自动检测）...")
    qihei_files = []
    for out_dir in dex_out_dirs.values():
        qihei_files.extend(find_qihei_loading_files(out_dir))
    if not qihei_files:
        print("  ⚠ 未找到候选栏 qihei 加载文件")
    else:
        for f in qihei_files:
            desc = f"自动检测: {f.name}"
            if not patch_qihei_direct_loads(f, desc):
                print(f"警告：修补 {desc} 失败，继续构建")

    if primary_dex_sizes["methods"] >= PRIMARY_DEX_MIGRATION_THRESHOLD:
        print()
        print("  主 DEX method_ids 接近上限，迁移 native 桥接类释放空间...")
        if not migrate_primary_class(
            classes_out, helper_classes_out, DEX_MIGRATION_CLASS
        ):
            return False

    if not install_english_association_patch(
        classes_out, helper_classes_out, association_resource_ids
    ):
        print("错误：英文单词补全联想补丁安装失败")
        return False

    # 6. 汇编
    print(f"[6/7] 汇编 {len(dex_entries)} 个 DEX...")
    dex_new_paths = {}
    for name, out_dir in dex_out_dirs.items():
        dex_new_path = work_dir / f"{Path(name).stem}_new.dex"
        subprocess.run(
            [java, "-jar", str(smali_jar),
             "assemble", str(out_dir), "-o", str(dex_new_path)],
            check=True, capture_output=True,
        )
        dex_new_paths[name] = dex_new_path
        dex_new_size = dex_new_path.stat().st_size
        print(
            f"  {name}: {dex_new_size} bytes "
            f"(原始: {len(dex_entries[name])})"
        )
        minimum_size = max(65536, len(dex_entries[name]) // 2)
        if dex_new_size < minimum_size:
            print(
                f"错误：新的 {name} 太小 ({dex_new_size}，"
                f"最低预期 {minimum_size})"
            )
            return False

    # 7. 构建 APK
    print("[7/7] 构建 APK...")
    patched_apk = work_dir / "patched.apk"
    with zipfile.ZipFile(input_apk, "r") as zin:
        with zipfile.ZipFile(patched_apk, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename.startswith("META-INF/"):
                    continue
                if item.filename in dex_new_paths:
                    continue
                zout.writestr(item, zin.read(item.filename))
            for name, dex_new_path in dex_new_paths.items():
                zout.write(dex_new_path, name)

    # 签名
    print("  签名 APK...")
    if not ensure_debug_keystore(keystore_path):
        return False

    if apksigner:
        subprocess.run([
            apksigner, "sign",
            "--ks", str(keystore_path),
            "--ks-pass", f"pass:{KEYSTORE_PASS}",
            "--ks-key-alias", KEY_ALIAS,
            "--v1-signing-enabled", "true",
            "--v2-signing-enabled", "true",
            "--v3-signing-enabled", "true",
            str(patched_apk),
        ], check=True, capture_output=True)
        subprocess.run([
            apksigner, "verify", "--verbose", str(patched_apk),
        ], check=True, capture_output=True)
        print("  ✓ apksigner 签名及验证完成（V2/V3）")
    else:
        subprocess.run([
            jarsigner,
            "-keystore", str(keystore_path),
            "-storepass", KEYSTORE_PASS,
            "-keypass", KEY_PASS,
            "-sigalg", "SHA256withRSA",
            "-digestalg", "SHA-256",
            str(patched_apk),
            KEY_ALIAS,
        ], check=True, capture_output=True)
        print("  ✓ jarsigner 签名完成（仅 V1）")

    output_apk.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(patched_apk, output_apk)
    out_size = output_apk.stat().st_size
    print(f"  输出: {output_apk} ({out_size / 1024 / 1024:.1f} MB)")

    print()
    print("=" * 60)
    print("完成！")
    print(f"APK 已生成: {output_apk}")
    print("=" * 60)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="豆包输入法 系统字体补丁")
    parser.add_argument("input_apk", help="原版 APK 路径")
    parser.add_argument("output_apk", help="输出 APK 路径")
    parser.add_argument("--keystore", default=str(Path.home() / ".android" / "debug.keystore"),
                        help="签名 keystore 路径")
    args = parser.parse_args()

    ok = build_apk(args.input_apk, args.output_apk, args.keystore)
    sys.exit(0 if ok else 1)
