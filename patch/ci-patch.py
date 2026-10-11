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
  7. 可选替换 launcher 图标（--icon-dir，默认仓库 Android/ 目录），
     同时导出两个 APK：仅特性版、特性+图标版
"""

import argparse
import difflib
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
# EnglishAssociationPatch.smali 里 ImeListItemView 开关访问器的占位符，
# 构建时按上游当前版本解析（见 resolve_switch_accessor）
SWITCH_ACCESSOR_PLACEHOLDER = "__SWITCH_ACCESSOR__"
DEX_METHOD_LIMIT = 65535
PRIMARY_DEX_MIGRATION_THRESHOLD = 65520
DEX_MIGRATION_CLASS = "J/N.smali"

# R.font.qihei 的资源 ID
QIHEI_FONT_RES_ID = "0x7f090003"

DEFAULT_ICON_DIR = SCRIPT_DIR.parent / "Android"
EXPECTED_ICON_SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
ICON_DPI_RANK = {
    "ldpi": 120, "mdpi": 160, "hdpi": 240,
    "xhdpi": 320, "xxhdpi": 480, "xxxhdpi": 640,
    "nodpi": 0xFFFE, "anydpi": -1,
}

# 重建 APK 时的 deflate 级别。必须是显式常量而不是让解释器用默认值：
# Python 3.13 起官方 Windows 构建换成 zlib-ng，默认级别的压缩率明显低于
# classic zlib，会让同一份产物白白大出 4 MiB（见 write_patched_apk）。
APK_COMPRESS_LEVEL = 9

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


def parse_string_pool_raw(data, offset):
    """解析 ResStringPool，返回按索引排列的字符串（容忍非 UTF-8 脏数据）。"""
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
            strings.append(data[pos:pos + byte_count].decode("utf-8", "replace"))
        else:
            char_count = struct.unpack_from("<H", data, pos)[0]
            pos += 2
            if char_count & 0x8000:
                char_count = ((char_count & 0x7FFF) << 16) | struct.unpack_from("<H", data, pos)[0]
                pos += 2
            strings.append(
                bytes(data[pos:pos + char_count * 2]).decode("utf-16-le", "replace")
            )
    return strings


def read_png_size(data):
    """读取 PNG IHDR 宽高，非 PNG 返回 None。"""
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return struct.unpack_from(">II", data, 16)
    return None


def parse_mipmap_paths(arsc_data):
    """解析 arsc 里 mipmap 类型下每个资源名在各密度下的文件路径。

    v1.5.x 起上游混淆了资源文件路径（res/mipmap-hdpi/ic_launcher.png →
    res/9w.png），但类型名与资源名（ic_launcher）仍在，所以不能再靠字符串池里的
    路径前缀匹配，改为按「资源名 + 密度」查表。

    返回 {资源名: {density: (字符串池索引, 路径)}}。
    """
    data = bytearray(arsc_data)
    table_type, header_size, _size = struct.unpack_from("<HHI", data, 0)
    if table_type != 0x0002:
        raise ValueError("无效的 resources.arsc 头部")
    package_count = struct.unpack_from("<I", data, 8)[0]
    offset = header_size
    _t, _h, global_pool_size = struct.unpack_from("<HHI", data, offset)
    strings_raw = parse_string_pool_raw(data, offset)
    offset += global_pool_size

    paths = {}
    for _ in range(package_count):
        chunk_type, chunk_header_size, chunk_size = struct.unpack_from(
            "<HHI", data, offset)
        if chunk_type != 0x0200:
            offset += chunk_size
            continue
        type_strings_offset, _last_public_type, key_strings_offset = struct.unpack_from(
            "<III", data, offset + 12 + 256)
        type_names = parse_string_pool_raw(data, offset + type_strings_offset)
        key_names = parse_string_pool_raw(data, offset + key_strings_offset)
        cursor = offset + chunk_header_size
        chunk_end = offset + chunk_size
        while cursor < chunk_end:
            inner_type, inner_header_size, inner_size = struct.unpack_from(
                "<HHI", data, cursor)
            if (inner_type == 0x0201
                    and type_names[data[cursor + 8] - 1] == "mipmap"):
                # ResTable_type 头 20 字节后是 ResTable_config，density 在其 +14
                density = struct.unpack_from("<H", data, cursor + 20 + 14)[0]
                entry_count = struct.unpack_from("<I", data, cursor + 12)[0]
                entries_start = struct.unpack_from("<I", data, cursor + 16)[0]
                for entry_index in range(entry_count):
                    entry_offset = struct.unpack_from(
                        "<I", data, cursor + inner_header_size + entry_index * 4
                    )[0]
                    if entry_offset == 0xFFFFFFFF:
                        continue
                    entry_pos = cursor + entries_start + entry_offset
                    flags = struct.unpack_from("<H", data, entry_pos + 2)[0]
                    if flags & 0x0001:
                        continue
                    key = key_names[
                        struct.unpack_from("<I", data, entry_pos + 4)[0]
                    ]
                    value_pos = entry_pos + 8
                    if data[value_pos + 3] != 0x03:
                        continue
                    string_index = struct.unpack_from("<I", data, value_pos + 4)[0]
                    paths.setdefault(key, {})[density] = (
                        string_index, strings_raw[string_index]
                    )
            cursor += inner_size
        offset += chunk_size
    return paths


def best_launcher_png_entry(mipmap_paths, key):
    """取资源名下密度最高的 PNG 条目（跳过 anydpi 上的 .xml），返回 (索引, 路径)。"""
    candidates = [
        (density, entry)
        for density, entry in mipmap_paths.get(key, {}).items()
        if density > 0 and not entry[1].endswith(".xml")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def retarget_adaptive_icon_entries(arsc_data, icon_keys, mipmap_paths):
    """
    将 mipmap/ic_launcher* 在 anydpi-v26 配置下指向 .xml 的条目，
    原地重定向到 arsc 中已有的最高密度 PNG 路径，
    使 Android 8.0+ 设备使用替换后的普通 PNG 图标而非原自适应图标。
    仅修改 entry 的字符串索引（4 字节），块大小不变。
    """
    data = bytearray(arsc_data)
    table_type, header_size, _size = struct.unpack_from("<HHI", data, 0)
    if table_type != 0x0002:
        raise ValueError("无效的 resources.arsc 头部")
    package_count = struct.unpack_from("<I", data, 8)[0]
    offset = header_size
    _t, _h, global_pool_size = struct.unpack_from("<HHI", data, offset)
    strings_raw = parse_string_pool_raw(data, offset)
    offset += global_pool_size

    retargeted = []
    for _ in range(package_count):
        chunk_type, chunk_header_size, chunk_size = struct.unpack_from("<HHI", data, offset)
        if chunk_type != 0x0200:
            offset += chunk_size
            continue
        type_strings_offset, _last_public_type, key_strings_offset = struct.unpack_from(
            "<III", data, offset + 12 + 256
        )
        type_names = parse_string_pool_raw(data, offset + type_strings_offset)
        key_names = parse_string_pool_raw(data, offset + key_strings_offset)
        cursor = offset + chunk_header_size
        chunk_end = offset + chunk_size
        while cursor < chunk_end:
            inner_type, inner_header_size, inner_size = struct.unpack_from("<HHI", data, cursor)
            if inner_type == 0x0201 and type_names[data[cursor + 8] - 1] == "mipmap":
                entry_count = struct.unpack_from("<I", data, cursor + 12)[0]
                entries_start = struct.unpack_from("<I", data, cursor + 16)[0]
                for entry_index in range(entry_count):
                    entry_offset = struct.unpack_from(
                        "<I", data, cursor + inner_header_size + entry_index * 4
                    )[0]
                    if entry_offset == 0xFFFFFFFF:
                        continue
                    entry_pos = cursor + entries_start + entry_offset
                    flags = struct.unpack_from("<H", data, entry_pos + 2)[0]
                    if flags & 0x0001:
                        continue
                    key = key_names[struct.unpack_from("<I", data, entry_pos + 4)[0]]
                    if key not in icon_keys:
                        continue
                    value_pos = entry_pos + 8
                    if data[value_pos + 3] != 0x03:
                        continue
                    old_index = struct.unpack_from("<I", data, value_pos + 4)[0]
                    old_path = strings_raw[old_index]
                    if not old_path.endswith(".xml"):
                        continue
                    new_entry = best_launcher_png_entry(mipmap_paths, key)
                    if new_entry is None:
                        raise ValueError(f"arsc 中找不到 {key} 的 PNG 路径")
                    new_index = new_entry[0]
                    struct.pack_into("<I", data, value_pos + 4, new_index)
                    retargeted.append((key, old_path, new_entry[1]))
            cursor += inner_size
        offset += chunk_size

    return bytes(data), retargeted


def collect_icon_replacements(zip_names, icon_dir, mipmap_paths):
    """
    收集图标目录中的 launcher PNG，映射到 APK 内实际路径。
    返回 (zip 内容替换映射, 需要自适应图标重定向的资源名集合)。
    """
    available = set(zip_names)
    replacements = {}
    redirect_keys = set()
    for dpi_dir in sorted(
        p for p in Path(icon_dir).iterdir()
        if p.is_dir() and p.name.startswith("mipmap-")
    ):
        dpi = dpi_dir.name[len("mipmap-"):]
        expected_size = EXPECTED_ICON_SIZES.get(dpi)
        density = ICON_DPI_RANK.get(dpi)
        for png in sorted(dpi_dir.glob("*.png")):
            key = png.stem
            entry = mipmap_paths.get(key, {}).get(density)
            if entry is None:
                print(f"  - 跳过 {png.relative_to(icon_dir)}: APK 未包含该密度的 {key}")
                continue
            zip_path = entry[1]
            if zip_path not in available:
                print(f"  - 跳过 {png.relative_to(icon_dir)}: APK 内缺少 {zip_path}")
                continue
            data = png.read_bytes()
            size = read_png_size(data)
            if size and expected_size and size != (expected_size, expected_size):
                print(
                    f"  ⚠ {png.name} 尺寸 {size[0]}x{size[1]} "
                    f"与 {dpi} 标准 {expected_size}x{expected_size} 不符，继续使用"
                )
            replacements[zip_path] = data
            redirect_keys.add(key)
            print(f"  ✓ {zip_path} ← {png.name} ({len(data)} bytes)")
    return replacements, redirect_keys


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


def _param_named(method_body, name):
    """按 Kotlin 形参名反查 smali 寄存器（p0/p1/...）。

    Kotlin 编译器会给每个引用类型形参插入一次
    `checkNotNullParameter(x, "<形参名>")`，形参名以字符串字面量留在字节码里。
    上游改版会重排形参顺序（v1.5.1 起 SettingsConfigNext 的 key 从第一个形参挪到
    第二个、value 变成第一个），按名字取寄存器比按位置硬编码稳。

    找不到时返回 None，交由调用方回退到按位置推断。
    """
    match = re.search(
        r'const-string(?:/jumbo)? v\d+, "' + re.escape(name) + r'"\n'
        r'\s*\n'
        r'\s*invoke-static \{(p\d+), v\d+\}, '
        r'Lkotlin/jvm/internal/\w+;->\w+\(',
        method_body,
    )
    return match.group(1) if match else None


def _counterpart_register(register):
    """给定一个形参寄存器，返回两参方法里的另一个形参。"""
    return "p1" if register == "p0" else "p0"


def _locate_method(smali, declaration_pattern):
    """按声明正则定位 smali 方法，返回 (声明行, 方法体)，未命中返回 (None, None)。

    声明正则需带 (?P<name>\\w+)；方法体不带声明行与 .end method。
    """
    match = re.search(
        declaration_pattern + r"\n(?P<body>.*?)\n\.end method", smali, re.DOTALL
    )
    if not match:
        return None, None
    return match.group(0).split("\n", 1)[0], match.group("body")


def detect_settings_sync_methods(settings_smali_path, keyboard_jni_path):
    """探测 SettingsConfigNext 的读写入口及各自的 key/value 寄存器。

    上游每次改版都会重新混淆方法名（v1.4.6 的入口是 f/g/m，v1.5.1 变成
    d/e/j/k/n），还会重排形参顺序，所以这里全部按结构探测而不是硬编码。

    返回 dict，探测不到的位置为 None：

      get_decl / get_key
          读取入口 `(String)Object`
      get_default_decl / get_default_key / get_default_value
          带默认值的读取入口
      set_entry
          设置进程写入入口 `static (String,Object)V`
      set_writer_decl / set_key / set_value
          设置进程实际写入方法（set_entry 的委托目标，补丁 hook 点）
      ime_writer_decl / ime_key / ime_value
          输入法进程写入方法（KeyboardJni.updateSettingsStringValue 的调用目标）
      changed_decl / changed_key / changed_result / changed_label
          onSharedPreferenceChanged 里 containsKey 的 key 寄存器、结果寄存器
          与"已注册"分支标签
    """
    settings_smali = settings_smali_path.read_text(encoding="utf-8")
    info = dict.fromkeys(
        [
            "get_decl", "get_key",
            "get_default_decl", "get_default_key", "get_default_value",
            "set_entry", "set_writer_decl", "set_key", "set_value",
            "ime_writer_decl", "ime_key", "ime_value",
            "changed_decl", "changed_key", "changed_result", "changed_label",
        ]
    )

    def report(role, found, extra=""):
        status = f"✓ {found}" if found else "⚠ 探测失败"
        print(f"    [{status}]{extra}")

    # --- 读取入口：static (String)Object ---
    decl, body = _locate_method(
        settings_smali,
        r"\.method public static (?:final )?(?P<name>\w+)"
        r"\(Ljava/lang/String;\)Ljava/lang/Object;",
    )
    if decl:
        info["get_decl"] = decl
        info["get_key"] = _param_named(body, "key") or "p0"
    report("读取入口", info["get_decl"], f"，key={info['get_key']}" if decl else "")

    # --- 带默认值的读取入口：(String,Object)Object 或 (Object,String)Object ---
    decl, body = _locate_method(
        settings_smali,
        r"\.method public static (?:final )?(?P<name>\w+)\("
        r"(?:Ljava/lang/String;Ljava/lang/Object;"
        r"|Ljava/lang/Object;Ljava/lang/String;)"
        r"\)Ljava/lang/Object;",
    )
    if decl:
        info["get_default_decl"] = decl
        key = _param_named(body, "key")
        if key is None:
            # 回退：key 是被送进 HashMap.get 的那个形参
            lookup = re.search(
                r"invoke-virtual \{v\d+, (p\d+)\}, "
                r"Ljava/util/HashMap;->get\(Ljava/lang/Object;\)",
                body,
            )
            key = lookup.group(1) if lookup else "p0"
        info["get_default_key"] = key
        info["get_default_value"] = (
            _param_named(body, "default") or _counterpart_register(key)
        )
    report("默认值读取入口", info["get_default_decl"])
    report("默认值读取 key", info["get_default_key"])

    # --- 设置进程写入入口：static (String,Object)V，方法体尾部委托给同类写入方法 ---
    entry_decl, body = _locate_method(
        settings_smali,
        r"\.method public static (?:final )?(?P<name>\w+)"
        r"\(Ljava/lang/String;Ljava/lang/Object;\)V",
    )
    writer = None
    if body:
        info["set_entry"] = re.search(r"(?P<name>\w+)\(", entry_decl).group("name")
        delegates = re.findall(
            r"invoke-\w+ \{[^}]+\}, "
            r"Lcom/bytedance/android/input/common/SettingsConfigNext;->"
            r"(\w+)\([^)]*\)V",
            body,
        )
        delegates = [d for d in delegates if d != info["set_entry"]]
        if delegates:
            writer = delegates[-1]
    report("设置写入入口", info["set_entry"])

    if writer:
        info["set_writer_decl"], writer_body = _locate_method(
            settings_smali,
            r"\.method public (?:static )?(?:final )?(?P<name>" +
            re.escape(writer) + r")\([^)]*\)V",
        )
        key = _param_named(writer_body or "", "key")
        if key:
            info["set_key"] = key
            info["set_value"] = (
                _param_named(writer_body or "", "value") or _counterpart_register(key)
            )
    report("设置进程写入点", info["set_writer_decl"])
    report("设置写入 key", info["set_key"])

    # --- 输入法进程写入：KeyboardJni.updateSettingsStringValue 的调用目标 ---
    keyboard_smali = keyboard_jni_path.read_text(encoding="utf-8")
    _, sync_body = _locate_method(
        keyboard_smali,
        r"\.method public static (?P<name>updateSettingsStringValue)"
        r"\(Ljava/lang/String;Ljava/lang/String;\)V",
    )
    ime_writer = None
    if sync_body:
        for call in re.finditer(
            r"invoke-(?:static|virtual) \{([^}]*)\}, "
            r"Lcom/bytedance/android/input/common/SettingsConfigNext;->"
            r"(\w+)\(Ljava/lang/String;Ljava/lang/String;\)V",
            sync_body,
        ):
            args = [a.strip() for a in call.group(1).split(",")]
            if args[-2:] == ["p0", "p1"]:
                ime_writer = call.group(2)
                break
    if ime_writer:
        info["ime_writer_decl"], writer_body = _locate_method(
            settings_smali,
            r"\.method public (?:static )?(?:final )?(?P<name>" +
            re.escape(ime_writer) + r")\(Ljava/lang/String;Ljava/lang/String;\)V",
        )
        key = _param_named(writer_body or "", "key")
        if key:
            info["ime_key"] = key
            info["ime_value"] = (
                _param_named(writer_body or "", "value") or _counterpart_register(key)
            )
    report("输入法进程写入点", info["ime_writer_decl"])
    report("输入法写入 key", info["ime_key"])

    # --- onSharedPreferenceChanged 的 containsKey 分支 ---
    decl, changed_body = _locate_method(
        settings_smali,
        r"\.method public (?:final )?(?P<name>onSharedPreferenceChanged)"
        r"\(Landroid/content/SharedPreferences;Ljava/lang/String;\)V",
    )
    if changed_body:
        contains = re.search(
            r"invoke-virtual \{v\d+, (p\d+)\}, "
            r"Ljava/util/HashMap;->containsKey\(Ljava/lang/Object;\)Z\n"
            r"\s*\n"
            r"\s*move-result (\w+)\n"
            r"\s*\n"
            r"\s*if-nez \2, :(\w+)",
            changed_body,
        )
        if contains:
            info["changed_decl"] = decl
            info["changed_key"] = contains.group(1)
            info["changed_result"] = contains.group(2)
            info["changed_label"] = contains.group(3)
    report(
        "未知键分支",
        info["changed_label"],
        f"，key={info['changed_key']}，结果寄存器={info['changed_result']}"
        if info["changed_label"] else "",
    )

    return info


def patch_settings_config(smali_path, info):
    """让自定义布尔配置复用原有 SettingsConfigNext 跨进程同步链路。"""
    print("  修补 SettingsConfigNext 自定义配置支持...")
    smali = smali_path.read_text(encoding="utf-8")

    required = {
        "读取入口": "get_decl",
        "默认值读取入口": "get_default_decl",
        "设置进程写入点": "set_writer_decl",
        "输入法进程写入点": "ime_writer_decl",
        "未知键分支": "changed_label",
    }
    missing = [label for label, key in required.items() if not info.get(key)]
    if missing:
        print(f"    ✗ 探测结果不完整，缺少: {'、'.join(missing)}")
        return False

    def read_snippet(key_register, label):
        return (
            f"    invoke-static {{{key_register}}}, {ASSOCIATION_PATCH_CLASS}"
            "->isPatchKey(Ljava/lang/String;)Z\n"
            "\n"
            "    move-result v0\n"
            "\n"
            f"    if-eqz v0, :{label}\n"
            "\n"
            f"    invoke-static {{}}, {ASSOCIATION_PATCH_CLASS}->isEnabled()Z\n"
            "\n"
            "    move-result v0\n"
            "\n"
            "    invoke-static {v0}, Ljava/lang/Boolean;->valueOf(Z)Ljava/lang/Boolean;\n"
            "\n"
            "    move-result-object v0\n"
            "\n"
            "    return-object v0\n"
            "\n"
            f"    :{label}"
        )

    smali = replace_method(
        smali, info["get_decl"],
        lambda method: insert_after_registers(
            method, read_snippet(info["get_key"], "patch_assoc_get_continue")
        ),
    )
    smali = replace_method(
        smali, info["get_default_decl"],
        lambda method: insert_after_registers(
            method,
            read_snippet(
                info["get_default_key"], "patch_assoc_get_default_continue"
            ),
        ),
    )

    set_object_snippet = (
        f"    invoke-static {{{info['set_key']}}}, {ASSOCIATION_PATCH_CLASS}"
        "->isPatchKey(Ljava/lang/String;)Z\n"
        "\n"
        "    move-result v0\n"
        "\n"
        "    if-eqz v0, :patch_assoc_set_object_continue\n"
        "\n"
        f"    instance-of v0, {info['set_value']}, Ljava/lang/Boolean;\n"
        "\n"
        "    if-eqz v0, :patch_assoc_set_object_continue\n"
        "\n"
        f"    check-cast {info['set_value']}, Ljava/lang/Boolean;\n"
        "\n"
        f"    invoke-virtual {{{info['set_value']}}}, "
        "Ljava/lang/Boolean;->booleanValue()Z\n"
        "\n"
        "    move-result v0\n"
        "\n"
        f"    invoke-static {{v0}}, {ASSOCIATION_PATCH_CLASS}->setEnabled(Z)V\n"
        "\n"
        "    return-void\n"
        "\n"
        "    :patch_assoc_set_object_continue"
    )
    smali = replace_method(
        smali, info["set_writer_decl"],
        lambda method: insert_after_registers(method, set_object_snippet),
    )

    set_string_snippet = (
        f"    invoke-static {{{info['ime_key']}}}, {ASSOCIATION_PATCH_CLASS}"
        "->isPatchKey(Ljava/lang/String;)Z\n"
        "\n"
        "    move-result v0\n"
        "\n"
        "    if-eqz v0, :patch_assoc_set_string_continue\n"
        "\n"
        f"    invoke-static {{{info['ime_value']}}}, {ASSOCIATION_PATCH_CLASS}"
        "->setFromString(Ljava/lang/String;)V\n"
        "\n"
        "    return-void\n"
        "\n"
        "    :patch_assoc_set_string_continue"
    )
    smali = replace_method(
        smali, info["ime_writer_decl"],
        lambda method: insert_after_registers(method, set_string_snippet),
    )

    known_key_anchor = (
        r"(?P<anchor>    move-result " +
        re.escape(info["changed_result"]) + r"\n"
        r"\n"
        r"    if-nez " + re.escape(info["changed_result"]) + r", :" +
        re.escape(info["changed_label"]) + r"\n)"
    )
    known_key_insert = (
        "\n"
        f"    invoke-static {{{info['changed_key']}}}, {ASSOCIATION_PATCH_CLASS}"
        "->isPatchKey(Ljava/lang/String;)Z\n"
        "\n"
        f"    move-result {info['changed_result']}\n"
        "\n"
        f"    if-nez {info['changed_result']}, :{info['changed_label']}\n"
    )
    start, end = get_method_bounds(smali, info["changed_decl"])
    method = smali[start:end]
    method, count = re.subn(
        known_key_anchor, r"\g<anchor>" + known_key_insert, method, count=1
    )
    if count != 1:
        raise ValueError("onSharedPreferenceChanged containsKey 分支不符合预期")
    smali = smali[:start] + method + smali[end:]

    checks = {
        "自定义配置读取": "patch_assoc_get_continue" in smali,
        "自定义配置默认读取": "patch_assoc_get_default_continue" in smali,
        "设置进程写入": "patch_assoc_set_object_continue" in smali,
        "输入法进程写入": "patch_assoc_set_string_continue" in smali,
        "未知键转为已知键": known_key_insert.strip() in smali,
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

    # 参数类型必须与 EnglishAssociationPatch.smali 模板中的签名保持一致。
    # 使用框架接口 InputConnection 而非上游混淆类 editor/a：v1.4.6 起 editor/a
    # 已被复用为无关的 synthetic lambda 类，硬编码会让验证器报 VerifyError。
    def insert_preedit_guard(method):
        """在 InputConnection 空值分支之后插入 ASCII preedit 拦截。

        不能硬编码 `:cond_21`：标签名是 baksmali 按指令偏移生成的，上游加一条
        指令就会变成 :cond_22 之类，而 str.replace 找不到时是静默无操作。这里
        改为按 "InputConnection is null" 日志定位空值分支，取紧随其后的标签，
        并从守卫指令里取出真正持有 InputConnection 的寄存器。
        """
        log_pos = method.find('"InputConnection is null"')
        if log_pos < 0:
            raise ValueError("SetPreeditRange 中找不到 InputConnection 空值分支")
        guards = list(re.finditer(r"if-nez (\w+), :\w+", method[:log_pos]))
        if not guards:
            raise ValueError("SetPreeditRange 中找不到 InputConnection 空值判断")
        conn_reg = guards[-1].group(1)
        label = re.search(
            r"^[ \t]*:\w+[ \t]*\n", method[log_pos:], re.MULTILINE
        )
        if label is None:
            raise ValueError("SetPreeditRange 空值分支之后找不到标签")
        end = log_pos + label.end()
        snippet = (
            f"    invoke-static {{{conn_reg}, p0, p1}}, {ASSOCIATION_PATCH_CLASS}"
            "->suppressAsciiPreeditRange("
            "Landroid/view/inputmethod/InputConnection;II)Z\n"
            "\n"
            "    move-result v1\n"
            "\n"
            "    if-eqz v1, :patch_ascii_preedit_continue\n"
            "\n"
            "    return-void\n"
            "\n"
            "    :patch_ascii_preedit_continue"
        )
        return method[:end] + snippet + "\n\n" + method[end:]

    smali = replace_method(
        smali,
        ".method public static SetPreeditRange(II)V",
        insert_preedit_guard,
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
    # 修饰符随版本漂移（v1.5.1 起是 `public final`），按正则取真实声明行
    declaration, _ = _locate_method(
        smali,
        r"\.method public (?:final )?(?P<name>onViewCreated)"
        r"\(Landroid/view/View;Landroid/os/Bundle;\)V",
    )
    if not declaration:
        print("    ✗ 找不到 onViewCreated(Landroid/view/View;Landroid/os/Bundle;)V")
        return False
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
    """切换到数字或符号键盘前提交英文 composing 文本。

    不能用固定寄存器锚点：v1.4.x 是 `const/4 v0, 0x5` + `switchKeyboard`，
    v1.5.1 改成 `sget-object ... InputBoardType->kNumber` + `Enum.ordinal()`。
    这里改为先按 kNumber/kSymbol 标记切出窗口，再取窗口内唯一的
    `switchKeyboard(I)V` 调用点（find_board_switch_smali 已保证两个标记都存在）。
    """
    print("  修补英文 preedit 的数字/符号键盘切换...")
    smali = smali_path.read_text(encoding="utf-8")

    helper_call = (
        f"    invoke-static {{}}, {ASSOCIATION_PATCH_CLASS}"
        "->commitEnglishPreeditBeforeBoardSwitch()V\n\n"
    )
    calls = [
        match
        for match in re.finditer(
            r"[ \t]*invoke-virtual \{[^}]*\}, "
            r"Lcom/bytedance/android/doubaoime/KeyboardJni;->switchKeyboard\(I\)V",
            smali,
        )
    ]
    boards = [
        ("数字键盘", "InputBoardType;->kNumber"),
        ("符号键盘", "InputBoardType;->kSymbol"),
    ]

    targets = []
    for desc, marker in boards:
        marker_pos = smali.find(marker)
        if marker_pos < 0:
            print(f"    ✗ 找不到{desc}的板型标记 {marker}")
            return False
        # 窗口右界取"下一个板型标记"，避免误插到另一块键盘的调用点之前
        boundary = min(
            (
                pos
                for _, other in boards
                if other != marker
                for pos in [smali.find(other, marker_pos + len(marker))]
                if pos >= 0
            ),
            default=len(smali),
        )
        window = [m for m in calls if marker_pos < m.start() < boundary]
        if len(window) != 1:
            print(f"    ✗ {desc} switchKeyboard 锚点数量为 {len(window)}，期望 1")
            return False
        targets.append((desc, window[0]))

    # 从后往前插入，避免前面的插入让后面的偏移失效
    for desc, match in sorted(targets, key=lambda item: item[1].start(), reverse=True):
        indent = re.match(r"[ \t]*", match.group(0)).group(0)
        smali = (
            smali[: match.start()]
            + helper_call.replace("    ", indent, 1)
            + smali[match.start():]
        )
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


def resolve_switch_accessor(dex_out_dirs):
    """探测 ImeListItemView 上返回 SwitchCompat 的无参访问器方法名。

    v1.4.6 是混淆名 `a()`，v1.5.1 起上游改用 Kotlin 属性访问器
    `getSwitchButton()`。名字写死在模板里一旦漂移就变成运行期 NoSuchMethodError
    —— 引用完整性校验拦不住：ImeListItemView 的父类是框架类，沿继承链找不到时
    只能判"未知"（返回 None），不能证伪。
    """
    relative = "com/bytedance/common_biz/ui/widget/ImeListItemView.smali"
    for out_dir in dex_out_dirs.values():
        path = Path(out_dir) / relative
        if not path.exists():
            continue
        matches = re.findall(
            r"^\.method public (?:final )?(?P<name>\w+)"
            r"\(\)Landroidx/appcompat/widget/SwitchCompat;$",
            path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        if len(matches) == 1:
            return matches[0]
        raise ValueError(f"ImeListItemView 的 SwitchCompat 访问器不唯一: {matches}")
    raise ValueError(f"输入 APK 缺少 {relative}")


def install_english_association_patch(
    classes_out, helper_classes_out, resource_ids, dex_out_dirs
):
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

    try:
        accessor = resolve_switch_accessor(dex_out_dirs)
    except ValueError as exc:
        print(f"    ✗ {exc}")
        return False
    if SWITCH_ACCESSOR_PLACEHOLDER not in association_smali:
        print(f"    ✗ 模板缺少占位符 {SWITCH_ACCESSOR_PLACEHOLDER}")
        return False
    association_smali = association_smali.replace(
        SWITCH_ACCESSOR_PLACEHOLDER, accessor
    )
    print(f"    ✓ ImeListItemView 开关访问器: {accessor}()")
    association_patch_path.write_text(association_smali, encoding="utf-8")

    info = detect_settings_sync_methods(settings_path, keyboard_jni_path)
    entry = info["set_entry"]
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
        patch_settings_config(settings_path, info)
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


# ---------------------------------------------------------------------------
# 补丁引用完整性校验
#
# smali 汇编只检查语法与标签，不检查跨类方法/字段是否存在、也不做参数类型
# 兼容性验证——这两类错误会拖到运行时才变成 VerifyError / NoSuchMethodError，
# 表现为"CI 全绿但装机闪退"（v1.4.6 的 editor/a 改名事故即此类）。
# 下面在汇编前对补丁【新增】的指令做静态校验，发现问题立即中止构建。
# ---------------------------------------------------------------------------

# 这些前缀的类由 Android 框架/SDK 提供，不在 APK 内，无法也无需在树内解析。
FRAMEWORK_TYPE_PREFIXES = ("Landroid/", "Ljava/", "Ljavax/", "Ldalvik/", "Lsun/")

_OBJ = "Ljava/lang/Object;"
# java.lang.Object 上必然存在的方法（树内没有 Object 的 smali，需特判）
_OBJECT_METHOD_NAMES = {
    "<init>", "<clinit>", "toString", "hashCode", "equals", "clone",
    "finalize", "wait", "notify", "notifyAll", "getClass", "registerNatives",
}

# 寄存器类型哨兵
_T_UNKNOWN = object()   # 无法解析 → 该参数跳过检查（保守，不报错）
_T_NULL = object()      # const 0 → 可赋值给任意引用类型

_INVOKE_RE = re.compile(
    r"^invoke-(?P<kind>static|virtual|interface|super|direct)(?P<range>/range)?"
    r"\s+\{(?P<regs>[^}]+)\},\s+"
    r"(?P<owner>L[\w/$]+;)->(?P<name>[^\s(]+)(?P<desc>\([^)]*\)\S+)"
)
_FIELD_RE = re.compile(
    r"^(?:sget|sput|iget|iput)-\w+\s+\{[^}]+\},\s+"
    r"(L[\w/$]+;)->([^\s:]+):"
)
_TYPE_RE = re.compile(
    r"^(?:new-instance|check-cast|const-class)\s+[vp]\d+,\s+(L[\w/$]+;)"
)
_INSTANCE_OF_RE = re.compile(r"^instance-of\s+[vp]\d+,\s+[vp]\d+,\s+(L[\w/$]+;)")
_REG_RE = re.compile(r"^[vp]\d+$")
_CATCH_RE = re.compile(r"^\.catch\s+\S+\s+\{:(\w+) \.\. :(\w+)\}\s+:(\w+)")
_CATCHALL_RE = re.compile(r"^\.catchall\s+\{:(\w+) \.\. :(\w+)\}\s+:(\w+)")


def _parse_param_types(desc):
    """把方法描述符 '(Ljava/lang/String;I)V' 解析为参数类型列表；
    原始类型与数组类型置 None（跳过检查）。"""
    params = []
    i = desc.index("(") + 1
    while desc[i] != ")":
        start = i
        while desc[i] == "[":
            i += 1
        if desc[i] == "L":
            i = desc.index(";", i) + 1
            params.append(desc[start:i])
        else:
            i += 1
            params.append(None)
    # 数组（以 '[' 开头）仅做精确匹配收益低，一并置 None
    return [None if p and p.startswith("[") else p for p in params]


def _is_framework_type(cls):
    return cls.startswith(FRAMEWORK_TYPE_PREFIXES)


def _build_class_index(dex_out_dirs):
    """扫描全部 smali，建立 类 → {methods, fields, super, interfaces, interface} 索引。

    注意 .super/.implements 行出现在 .class 行【之后】，必须在整个文件头部
    期间持续收集，不能在拿到类名后就停止（否则继承链断裂导致误判）。
    """
    index = {}
    for out_dir in dex_out_dirs.values():
        for path in Path(out_dir).rglob("*.smali"):
            cls = None
            entry = {
                "methods": set(), "fields": set(),
                "super": None, "interfaces": [], "interface": False,
            }
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if line.startswith(".class "):
                            cls = line.split()[-1].strip()
                            entry["interface"] = " interface " in line
                        elif line.startswith(".super "):
                            entry["super"] = line.split()[-1].strip()
                        elif line.startswith(".implements "):
                            entry["interfaces"].append(line.split()[-1].strip())
                        elif line.startswith(".method "):
                            entry["methods"].add(line.split()[-1].strip())
                        elif line.startswith(".field "):
                            m = re.match(r"\.field\s+(?:\S+\s+)*?([^\s:]+):", line)
                            if m:
                                entry["fields"].add(m.group(1))
            except OSError:
                continue
            if cls:
                index[cls] = entry
    return index


def _assignable(index, t, p):
    """t 是否可赋值给 p：True / False / None(无法判定)。"""
    if t is _T_NULL:
        return True
    if t is _T_UNKNOWN or t is None or p is None:
        return None
    if t == p:
        return True
    if p == _OBJ:
        return True
    if t.startswith("[") or p.startswith("["):
        # 数组仅接受精确匹配，否则保守跳过
        return None if t != p else True
    if t not in index:
        return None  # 框架类，父链不可知
    # BFS 遍历 t 的父类与接口闭包
    seen = set()
    queue = [t]
    while queue:
        cls = queue.pop(0)
        if cls in seen:
            continue
        seen.add(cls)
        if cls == p:
            return True
        if cls == _OBJ:
            continue  # Object 顶点，该分支结束
        if cls in index:
            e = index[cls]
            if e["super"]:
                queue.append(e["super"])
            queue.extend(e["interfaces"])
            continue
        # 框架类祖先：其祖先链只可能是框架类
        if p in index:
            # p 是树内（应用/捆绑）类，绝不可能出现在框架祖先链上 → 跳过该分支
            continue
        return None  # p 也是框架类，无法判定
    # 所有分支都完整走完仍未命中 → 确认不可赋值
    return False


def _member_resolves(index, owner, member, kind):
    """沿父类/接口链查找 member：True / False / None(框架祖先上无法判定)。"""
    if owner not in index:
        return None if _is_framework_type(owner) else False
    seen = set()
    queue = [owner]
    unknown_possible = False
    while queue:
        cls = queue.pop(0)
        if cls in seen:
            continue
        seen.add(cls)
        if cls in index:
            e = index[cls]
            if member in e[kind]:
                return True
            if e["super"]:
                queue.append(e["super"])
            queue.extend(e["interfaces"])
            continue
        if cls == _OBJ:
            name = member.split("(")[0]
            if kind == "methods" and name in _OBJECT_METHOD_NAMES:
                return True
            continue
        # 框架祖先：该方法可能声明在框架类上
        unknown_possible = True
        continue
    return None if unknown_possible else False


def _norm_reg(tok, n_regs, n_params):
    """p/v 别名归一化为物理寄存器号；无法解析返回 None。"""
    if not _REG_RE.match(tok):
        return None
    if tok[0] == "v":
        return int(tok[1:])
    return n_regs - n_params + int(tok[1:])


class _MethodCtx:
    """单个 smali 方法的指令/CFG 视图，供反向 reaching-type 分析使用。"""

    def __init__(self, lines, start, end):
        self.lines = lines
        self.texts = []          # 指令文本（已 strip）
        label_pos = {}
        pending = []
        catches = []             # (start_label, end_label, handler_label)
        n_regs = None
        is_static = False
        desc = None
        cls_type = None

        head = lines[start].split()
        is_static = "static" in head
        desc = head[-1]

        i = start + 1
        while i < end and not lines[i].strip():
            i += 1
        if i < end and lines[i].strip().startswith((".registers", ".locals")):
            n_regs = int(lines[i].split()[1])
            i += 1
        while i < end:
            raw = lines[i].strip()
            if raw.startswith(":"):
                pending.append(raw[1:].rstrip(":"))
            elif raw.startswith(".catch "):
                m = _CATCH_RE.match(raw)
                if m:
                    catches.append(m.groups())
            elif raw.startswith(".catchall "):
                m = _CATCHALL_RE.match(raw)
                if m:
                    catches.append(m.groups())
            elif (raw and not raw.startswith(".")
                  and not raw.startswith("#")):
                pos = len(self.texts)
                for lab in pending:
                    label_pos[lab] = pos
                pending = []
                self.texts.append(raw)
            # 其余（.line/.annotation/空行/注释）忽略
            i += 1

        if n_regs is None or not self.texts:
            self.valid = False
            return
        self.valid = True
        self.n_regs = n_regs
        self.is_static = is_static
        self.desc = desc
        params_desc = _parse_param_types(desc)
        self.n_params = len(params_desc) + (0 if is_static else 1)
        if is_static:
            self.param_types = params_desc
        else:
            # 实例方法：p0 为 this，类型在外部通过 bind_class 设置
            self.param_types = [None] + params_desc
        self._cls_type = None  # 由 bind_class 设置

        m = len(self.texts)
        # 计算每条指令的后继
        self.succs = []
        for idx, text in enumerate(self.texts):
            op = text.split()[0]
            succ = set()
            if op.startswith("goto"):
                lab = text.split()[-1].lstrip(":")
                if lab in label_pos:
                    succ.add(label_pos[lab])
            elif op.startswith("if-"):
                if idx + 1 < m:
                    succ.add(idx + 1)
                lab = text.split()[-1].lstrip(":")
                if lab in label_pos:
                    succ.add(label_pos[lab])
            elif op.startswith("return") or op == "throw":
                pass
            else:
                if idx + 1 < m:
                    succ.add(idx + 1)
            self.succs.append(succ)
        # 异常边：try 区内指令 → handler
        for lab_a, lab_b, lab_h in catches:
            if lab_a in label_pos and lab_b in label_pos and lab_h in label_pos:
                a, b = label_pos[lab_a], label_pos[lab_b]
                h = label_pos[lab_h]
                for k in range(a, min(b, m)):
                    self.succs[k].add(h)
        # 反转为前驱
        self.preds = [set() for _ in range(m)]
        for idx, ss in enumerate(self.succs):
            for s in ss:
                self.preds[s].add(idx)

    def bind_class(self, cls_type):
        self._cls_type = cls_type
        if not self.is_static and self.param_types:
            self.param_types[0] = cls_type

    def initial_type(self, phys_reg):
        base = self.n_regs - self.n_params
        if phys_reg >= base:
            k = phys_reg - base
            if k < len(self.param_types):
                return self.param_types[k] if self.param_types[k] else _T_UNKNOWN
        return _T_UNKNOWN

    # ---- 指令语义 ----
    @staticmethod
    def _dest_of(text):
        """指令写入的目标寄存器（无写入返回 None）。"""
        op = text.split()[0]
        if (op.startswith(("invoke", "if-", "goto", "return", "sput", "iput"))
                or op in ("throw", "aput", "packed-switch", "sparse-switch",
                          "fill-array-data")
                or op.startswith("move-result")):
            return None
        parts = text.split()
        if len(parts) >= 2:
            tok = parts[1].rstrip(",")
            if _REG_RE.match(tok):
                return tok
        return None

    def def_type(self, idx, text, dest_phys):
        """dest 寄存器在该指令处的赋值类型。返回 ('switch', reg) 或类型/哨兵。"""
        op = text.split()[0]
        parts = text.split()
        if op.startswith("move-result"):
            return _T_UNKNOWN  # 宽/原始 move-result（对象的已在调用侧处理）
        if op.startswith("move-object") or op == "move":
            if len(parts) >= 3:
                return ("switch", parts[2].rstrip(","))
            return _T_UNKNOWN
        if op.startswith("move-wide"):
            return _T_UNKNOWN
        if op == "new-instance":
            return parts[-1]
        if op == "check-cast":
            return parts[-1]
        if op == "const-string" or op == "const-string/jumbo":
            return "Ljava/lang/String;"
        if op == "const-class":
            return "Ljava/lang/Class;"
        if op.startswith("const"):
            # const/4 v0, 0x0 → null
            try:
                val = parts[-1]
                if val in ("0x0", "0", "-0x1"):
                    return _T_NULL if val != "-0x1" else _T_UNKNOWN
            except IndexError:
                pass
            return _T_UNKNOWN
        if op.startswith("sget-object"):
            return text.split(",")[-1].strip().rsplit(":", 1)[-1]
        if op.startswith("sget"):
            return _T_UNKNOWN
        return _T_UNKNOWN  # 算术/数组/iget 等

    def reaching_types(self, start_idx, reg_tok):
        """反向数据流：求 start_idx 处寄存器的 reaching 类型集合。"""
        start_phys = _norm_reg(reg_tok, self.n_regs, self.n_params)
        if start_phys is None:
            return [_T_UNKNOWN]
        types = []
        seen = set()
        stack = [(start_idx, start_phys)]
        while stack:
            idx, phys = stack.pop()
            if (idx, phys) in seen:
                continue
            seen.add((idx, phys))
            text = self.texts[idx]
            op = text.split()[0]
            parts = text.split()

            # move-result-object 绑定上一条 invoke 的返回类型
            if op == "move-result-object":
                dest = _norm_reg(parts[1], self.n_regs, self.n_params)
                if dest == phys:
                    prev = self.texts[idx - 1] if idx > 0 else ""
                    m = _INVOKE_RE.match(prev)
                    if m:
                        ret = m.group("desc").split(")", 1)[1]
                        if ret.startswith(("L", "[")):
                            types.append(ret)
                        else:
                            types.append(_T_UNKNOWN)
                    else:
                        types.append(_T_UNKNOWN)
                    continue

            dest_tok = self._dest_of(text)
            if dest_tok is not None:
                dest_phys = _norm_reg(dest_tok, self.n_regs, self.n_params)
                if dest_phys == phys:
                    d = self.def_type(idx, text, dest_phys)
                    if isinstance(d, tuple) and d[0] == "switch":
                        other = _norm_reg(d[1], self.n_regs, self.n_params)
                        if other is None:
                            types.append(_T_UNKNOWN)
                        else:
                            stack.append((idx, other))
                        continue
                    types.append(d)
                    continue

            # 该指令不写目标寄存器 → 沿前驱继续
            ps = self.preds[idx]
            if not ps:
                # 无前驱 = 方法入口 → 使用参数初始类型
                types.append(self.initial_type(phys))
                continue
            for p in ps:
                stack.append((p, phys))
        return types or [_T_UNKNOWN]


def _merge_verdicts(verdicts):
    if any(v is False for v in verdicts):
        return False
    if any(v is None for v in verdicts):
        return None
    return True


def _check_invoke(line, method, idx, index):
    """校验一条 invoke 指令：目标可解析 + 实参类型可赋值。返回问题列表。"""
    m = _INVOKE_RE.match(line)
    if not m:
        return []  # invoke-polymorphic/custom 等罕见形式，跳过
    problems = []
    owner = m.group("owner")
    member = m.group("name") + m.group("desc")

    # 1. 目标方法存在性（框架 owner 无法校验，跳过）
    if not _is_framework_type(owner):
        res = _member_resolves(index, owner, member, "methods")
        if res is False:
            kind = "类" if owner not in index else "方法"
            problems.append(
                f"invoke 目标{kind}不存在: {owner}{member}"
                + ("" if owner in index else "（类不在 APK 内）")
            )

    # 2. 实参类型可赋值性（含 receiver）
    if m.group("range"):
        return problems  # range 形式寄存器展开复杂，补丁未使用，保守跳过
    regs = [r.strip() for r in m.group("regs").split(",")]
    params = _parse_param_types(m.group("desc"))
    if m.group("kind") == "static":
        arg_pairs = list(zip(regs, params))
    else:
        if not regs:
            return problems
        arg_pairs = [(regs[0], owner)] + list(zip(regs[1:], params))

    for reg, p in arg_pairs:
        if p is None:
            continue  # 原始/数组参数跳过
        types = method.reaching_types(idx, reg)
        # 逐个 reaching 类型判定：任一不可赋值即失败（验证器同样按合并类型裁决）
        per = [
            None if t is _T_UNKNOWN else _assignable(index, t, p)
            for t in types
        ]
        if _merge_verdicts(per) is False:
            shown = ", ".join(
                "?" if t is _T_UNKNOWN else
                ("null" if t is _T_NULL else t) for t in types
            )
            problems.append(
                f"实参类型不可赋值: {reg}={{{shown}}} → 参数 {p}"
            )
    return problems


def verify_patched_references(dex_out_dirs, snapshot_dir):
    """对比补丁前后的 smali，校验补丁新增指令的引用完整性。

    - 新增/新文件中的每个 invoke：目标方法必须能在 APK 类层次中解析；
    - invoke 实参（含 receiver）：寄存器的 reaching 类型必须可赋值给声明参数；
    - 字段指令：目标字段必须存在；
    - new-instance/check-cast：目标类必须存在。
    校验失败返回 False，中止构建。
    """
    print("  校验补丁引用完整性...")
    index = _build_class_index(dex_out_dirs)
    print(f"  ✓ 已索引 {len(index)} 个类定义")

    # 快照文件集合（用于识别"移动未修改"的类）
    snap_files = {}
    if Path(snapshot_dir).exists():
        for sp in Path(snapshot_dir).rglob("*.smali"):
            try:
                snap_files[hashlib.sha1(sp.read_bytes()).hexdigest()] = sp
            except OSError:
                pass

    problems = []
    checked_files = 0

    for dex_name, out_dir in dex_out_dirs.items():
        snap_root = Path(snapshot_dir) / dex_name
        for path in Path(out_dir).rglob("*.smali"):
            rel = path.relative_to(out_dir)
            snap_path = snap_root / rel
            try:
                new_text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                problems.append(f"{rel}: 读取失败 {exc}")
                continue

            check_all_lines = False
            check_line_nums = None  # None → 全部；set → 仅这些行（0 基）
            if not snap_path.exists():
                # 新文件：可能是 migrate 移动的原文件（内容一致则跳过）
                digest = hashlib.sha1(new_text.encode("utf-8")).hexdigest()
                if digest in snap_files:
                    continue
                check_all_lines = True
            else:
                old_text = snap_path.read_text(
                    encoding="utf-8", errors="replace")
                if old_text == new_text:
                    continue
                old_lines = old_text.splitlines()
                new_lines = new_text.splitlines()
                sm = difflib.SequenceMatcher(
                    None, old_lines, new_lines, autojunk=False)
                check_line_nums = set()
                for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
                    if tag in ("insert", "replace"):
                        check_line_nums.update(range(j1, j2))
                if not check_line_nums:
                    continue

            lines = new_text.splitlines()
            checked_files += 1

            # 逐方法处理：收集待检查行所属的方法
            method_spans = []  # (start, end)
            for i, ln in enumerate(lines):
                if ln.startswith(".method "):
                    for j in range(i + 1, len(lines)):
                        if lines[j].startswith(".end method"):
                            method_spans.append((i, j))
                            break

            for start, end in method_spans:
                if check_all_lines:
                    targets = [
                        k for k in range(start + 1, end)
                        if lines[k].strip()
                        and not lines[k].strip().startswith(("."))
                    ]
                else:
                    targets = [
                        k for k in (check_line_nums or set())
                        if start < k < end
                    ]
                if not targets:
                    continue
                ctx = _MethodCtx(lines, start, end)
                if not ctx.valid:
                    continue
                # 绑定当前类
                cls_type = None
                for k in range(start, -1, -1):
                    if lines[k].startswith(".class "):
                        cls_type = lines[k].split()[-1].strip()
                        break
                ctx.bind_class(cls_type)

                # 指令行号 → 指令索引 映射
                line_to_idx = {}
                pos = 0
                for k in range(start + 1, end):
                    raw = lines[k].strip()
                    if (raw and not raw.startswith((".", "#", ":"))):
                        line_to_idx[k] = pos
                        pos += 1

                for k in sorted(targets):
                    raw = lines[k].strip()
                    if not raw or raw.startswith((".", "#", ":")):
                        continue
                    file_rel = f"{dex_name}/{rel}"
                    loc = f"{file_rel}:{k + 1}"

                    if raw.startswith("invoke-"):
                        idx = line_to_idx.get(k)
                        if idx is None:
                            continue
                        for prob in _check_invoke(raw, ctx, idx, index):
                            problems.append(f"{loc}: {prob}")
                        continue

                    fm = _FIELD_RE.match(raw)
                    if fm:
                        owner, field = fm.group(1), fm.group(2)
                        if not _is_framework_type(owner):
                            res = _member_resolves(index, owner, field, "fields")
                            if res is False:
                                problems.append(
                                    f"{loc}: 字段不存在: {owner}->{field}"
                                )
                        continue

                    tm = _TYPE_RE.match(raw) or _INSTANCE_OF_RE.match(raw)
                    if tm:
                        t = tm.group(1)
                        if not _is_framework_type(t) and t not in index:
                            problems.append(
                                f"{loc}: 类不存在: {t}（类不在 APK 内）"
                            )
                        continue

    print(f"  ✓ 已校验 {checked_files} 个被修改/新增的 smali 文件")
    if problems:
        limit = 50
        for prob in problems[:limit]:
            print(f"  ✗ {prob}")
        if len(problems) > limit:
            print(f"  ... 另有 {len(problems) - limit} 个问题")
        print(f"  ✗ 引用完整性校验失败：共 {len(problems)} 个问题")
        return False
    print("  ✓ 引用完整性校验通过")
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

    def method_bounds(text):
        start = text.index(
            ".method public static createTypeFace(Ljava/lang/String;ZI)"
            "Landroid/graphics/Typeface;"
        )
        return start, text.index(".end method", start)

    mi, ei = method_bounds(smali)

    update_call = (
        "    invoke-static {},"
        " Lcom/bytedance/android/doubaoime/KeyboardJni;"
        "->updateIsSupportSystemFont()V\n"
    )
    if update_call in smali:
        smali = smali.replace(update_call, "")
        # 删除一行后其后所有偏移都会前移，方法边界必须重新计算，
        # 否则下面的切片范围会溢出到相邻方法。
        mi, ei = method_bounds(smali)
        print("  ✓ 已移除 updateIsSupportSystemFont() 调用")
    else:
        print("  - updateIsSupportSystemFont() 调用不存在")

    # 目标寄存器由 baksmali 分配，随上游版本漂移（v1.4.6 是 v2，v1.5.1 变成 v0）。
    adaptive_match = re.search(r'const-string (v\d+), "_adaptive"', smali[mi:ei])
    if not adaptive_match:
        print("错误：createTypeFace 中找不到 _adaptive 判断")
        return False
    old_start = mi + adaptive_match.start()

    catch_pos = smali.index(".catch Ljava/lang/Exception;", old_start, ei)
    end_of_catch = smali.index("\n", catch_pos) + 1

    old_text = smali[old_start:end_of_catch]

    # 标签名由 baksmali 按指令偏移量生成，会随上游版本漂移（v1.4.4 是
    # :try_end_9a/:catch_9b，v1.4.5 变成 :try_end_84/:catch_85）。硬编码会让
    # .catch 指向不存在的处理块，汇编时报 "Cannot get the location of a label
    # that hasn't been placed yet"。这里从原文本解析出真实标签名再复用。
    label_match = re.search(
        r"\.catch Ljava/lang/Exception;"
        r"\s*\{:(try_start_\w+) \.\. :(try_end_\w+)\}\s*:(\w+)",
        old_text,
    )
    if not label_match:
        print("错误：无法从 _adaptive 段解析出 try/catch 标签")
        return False
    try_start_label, try_end_label, catch_label = label_match.groups()

    # 风格寄存器同样随版本漂移（v1.4.6 是 v0，v1.5.1 变成 v1）。catch 兜底分支里
    # 那句 Typeface.create(String,I) 与本补丁要做的事完全同构，直接复用它的寄存器，
    # 避免硬编码后汇编出 "register v0 has type ... but expected ..." 之类的错误。
    style_match = re.search(
        r"invoke-static \{p0, (v\d+)\},\s*"
        r"Landroid/graphics/Typeface;->create\(Ljava/lang/String;I\)",
        smali[catch_pos:ei],
    )
    if not style_match:
        print("错误：无法从 createTypeFace 的 catch 分支解析风格寄存器")
        return False
    style_reg = style_match.group(1)

    new_text = (
        f"    invoke-static {{p0, {style_reg}}},"
        " Landroid/graphics/Typeface;->create(Ljava/lang/String;I)"
        "Landroid/graphics/Typeface;\n"
        "\n"
        "    move-result-object p0\n"
        "\n"
        f"    :{try_end_label}\n"
        f"    .catch Ljava/lang/Exception;"
        f" {{:{try_start_label} .. :{try_end_label}}} :{catch_label}\n"
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
        "Typeface.create(String) 已添加": (
            f"invoke-static {{p0, {style_reg}}},"
            " Landroid/graphics/Typeface;->create" in method
        ),
        "createFromAsset 皮肤字体保留": "createFromAsset" in method,
        "ResourcesCompat.getFont 保留": "ResourcesCompat" in method,
        "qihei 默认字体已删除": "0x7f090003" not in method,
        "异常处理块标签已定义": f"\n    :{catch_label}\n" in smali,
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


def write_patched_apk(dest_path, input_apk, dex_new_paths, file_overrides):
    """按原 ZIP 条目顺序重建 APK，替换 DEX 与指定资源内容，剥离旧签名。

    压缩级别必须显式钉死：Python 3.13 起官方 Windows 构建改用 zlib-ng，
    默认级别的压缩率明显低于原版 classic zlib，实测让同一个 APK 从 163 MiB 涨到
    167 MiB —— 连没动过的 .so 和词典资源也会一起变大。固定 level 9 后产物大小与
    原版基本一致，也不再随解释器漂移。

    注意 compresslevel 必须逐条传给 writestr：传入 ZipInfo 时 zipfile 只读
    zinfo._compresslevel，构造函数上的 compresslevel 对它不起作用（只影响
    ZipFile.write()）。
    """
    with zipfile.ZipFile(input_apk, "r") as zin:
        with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename.startswith("META-INF/"):
                    continue
                if item.filename in dex_new_paths:
                    continue
                if item.filename in file_overrides:
                    payload = file_overrides[item.filename]
                else:
                    payload = zin.read(item.filename)
                zout.writestr(item, payload, compresslevel=APK_COMPRESS_LEVEL)
            for name, dex_new_path in dex_new_paths.items():
                zout.write(dex_new_path, name, compresslevel=APK_COMPRESS_LEVEL)


def sign_apk(apk_path, keystore_path, apksigner, jarsigner):
    """对 APK 做 debug 签名并校验。"""
    print(f"  签名 {Path(apk_path).name}...")
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
            str(apk_path),
        ], check=True, capture_output=True)
        subprocess.run([
            apksigner, "verify", "--verbose", str(apk_path),
        ], check=True, capture_output=True)
        print("  ✓ apksigner 签名及验证完成（V1/V2/V3）")
    else:
        subprocess.run([
            jarsigner,
            "-keystore", str(keystore_path),
            "-storepass", KEYSTORE_PASS,
            "-keypass", KEY_PASS,
            "-sigalg", "SHA256withRSA",
            "-digestalg", "SHA-256",
            str(apk_path),
            KEY_ALIAS,
        ], check=True, capture_output=True)
        print("  ✓ jarsigner 签名完成（仅 V1）")
    return True


def build_apk(input_apk, output_apk, keystore_path, icon_dir=None):
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

    # 图标替换准备
    icon_replacements = None
    patched_arsc = None
    if icon_dir is not None:
        print()
        print("  准备图标替换...")
        print(f"  图标目录: {icon_dir}")
        with zipfile.ZipFile(input_apk) as archive:
            zip_names = archive.namelist()
            original_arsc = archive.read("resources.arsc")
        mipmap_paths = parse_mipmap_paths(original_arsc)
        icon_replacements, redirect_keys = collect_icon_replacements(
            zip_names, icon_dir, mipmap_paths
        )
        if not icon_replacements:
            print("  ⚠ 未收集到任何图标，本次仅输出特性版")
            icon_replacements = None
        else:
            patched_arsc, retargeted = retarget_adaptive_icon_entries(
                original_arsc, redirect_keys, mipmap_paths
            )
            for key, old_path, new_path in retargeted:
                print(f"  ✓ 自适应图标重定向 {key}: {old_path} → {new_path}")
            if not retargeted:
                print("  ⚠ 未发现 v26 自适应图标定义，跳过 resources.arsc 重定向")

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

    # 快照原始反编译结果，供补丁后的引用完整性校验做前后对比
    snapshot_dir = work_dir / "_orig_smali_snapshot"
    for name, out_dir in dex_out_dirs.items():
        shutil.copytree(out_dir, snapshot_dir / name)
    print("  ✓ 已快照原始 smali（用于引用完整性校验）")

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
        classes_out, helper_classes_out, association_resource_ids, dex_out_dirs
    ):
        print("错误：英文单词补全联想补丁安装失败")
        return False

    # 5.5 引用完整性校验：补丁新增的跨类引用必须在本 APK 内可解析，
    #     且 invoke 实参类型必须与声明参数兼容，否则运行时会 VerifyError。
    print()
    if not verify_patched_references(dex_out_dirs, snapshot_dir):
        print("错误：补丁引用完整性校验失败，拒绝打包")
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
    variants = [("仅特性版", output_apk, {})]
    if icon_replacements is not None:
        icons_output_apk = output_apk.with_name(
            f"{output_apk.stem}-icons{output_apk.suffix}"
        )
        icon_overrides = dict(icon_replacements)
        if patched_arsc is not None:
            icon_overrides["resources.arsc"] = patched_arsc
        variants.append(("特性+图标版", icons_output_apk, icon_overrides))

    for variant_name, variant_output, file_overrides in variants:
        print(f"  --- {variant_name} ---")
        patched_apk = work_dir / variant_output.name
        write_patched_apk(patched_apk, input_apk, dex_new_paths, file_overrides)
        if not sign_apk(patched_apk, keystore_path, apksigner, jarsigner):
            return False
        variant_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(patched_apk, variant_output)
        out_size = variant_output.stat().st_size
        print(f"  输出: {variant_output} ({out_size / 1024 / 1024:.1f} MB)")

    print()
    print("=" * 60)
    print("完成！")
    for _label, variant_output, _overrides in variants:
        print(f"APK 已生成: {variant_output}")
    print("=" * 60)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="豆包输入法 系统字体补丁")
    parser.add_argument("input_apk", help="原版 APK 路径")
    parser.add_argument("output_apk", help="输出 APK 路径")
    parser.add_argument("--keystore", default=str(Path.home() / ".android" / "debug.keystore"),
                        help="签名 keystore 路径")
    parser.add_argument("--icon-dir", default=str(DEFAULT_ICON_DIR),
                        help=f"launcher 图标目录（默认 {DEFAULT_ICON_DIR}）")
    parser.add_argument("--no-icons", action="store_true",
                        help="禁用图标替换，仅输出特性版")
    args = parser.parse_args()

    icon_dir = None
    if not args.no_icons:
        candidate = Path(args.icon_dir)
        if candidate.is_dir():
            icon_dir = candidate
        else:
            print(f"提示: 图标目录不存在，仅输出特性版: {candidate}")

    ok = build_apk(args.input_apk, args.output_apk, args.keystore, icon_dir=icon_dir)
    sys.exit(0 if ok else 1)
