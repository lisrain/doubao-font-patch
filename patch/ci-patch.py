"""
豆包输入法 系统字体补丁 - CI/跨平台版
通过命令行参数指定输入输出 APK 路径，兼容 GitHub Actions (Linux) 和本地 (Windows)

修改内容：
  1. KeyboardView.createTypeFace() → 保留图标字体，其余用系统字体
  2. CandidateListView / CandidateIdleView / MoreCandidateSyllableAdapter
     中直接加载 R.font.qihei → 系统字体
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SMALI_KEYBOARD_VIEW = "com/bytedance/android/input/keyboard/KeyboardView.smali"

# R.font.qihei 的资源 ID
QIHEI_FONT_RES_ID = "0x7f090003"

KEYSTORE_PASS = "android"
KEY_ALIAS = "androiddebugkey"
KEY_PASS = "android"


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
    java_path = shutil.which("java")
    if not java_path:
        for jdk in [
            r"C:\Program Files\Java\jdk-21.0.10\bin\java.exe",
            r"C:\Program Files\Java\jdk-17\bin\java.exe",
        ]:
            if os.path.exists(jdk):
                return jdk
    return java_path


def find_apksigner():
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
            for v in sorted(build_tools.iterdir(), reverse=True):
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
    work_dir = Path(os.environ.get("TEMP", os.environ.get("TMPDIR", "/tmp"))) / "doubao_patch"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    print(f"  工作目录: {work_dir}")

    # 3. 提取 classes.dex
    print("[3/7] 提取 classes.dex...")
    with zipfile.ZipFile(input_apk) as z:
        dex_data = z.read("classes.dex")
        dex_path = work_dir / "classes.dex"
        dex_path.write_bytes(dex_data)
    print(f"  MD5: {hashlib.md5(dex_data).hexdigest()} ({len(dex_data)} bytes)")

    # 4. 反编译
    print("[4/7] 反编译 classes.dex...")
    classes_out = work_dir / "classes_out"
    subprocess.run(
        [java, "-jar", str(baksmali_jar),
         "disassemble", str(dex_path), "-o", str(classes_out)],
        check=True, capture_output=True,
    )

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
    qihei_files = find_qihei_loading_files(classes_out)
    if not qihei_files:
        print("  ⚠ 未找到候选栏 qihei 加载文件")
    else:
        for f in qihei_files:
            desc = f"自动检测: {f.name}"
            if not patch_qihei_direct_loads(f, desc):
                print(f"警告：修补 {desc} 失败，继续构建")

    # 6. 汇编
    print("[6/7] 汇编 classes.dex...")
    dex_new_path = work_dir / "classes_new.dex"
    subprocess.run(
        [java, "-jar", str(smali_jar),
         "assemble", str(classes_out), "-o", str(dex_new_path)],
        check=True, capture_output=True,
    )
    dex_new_size = dex_new_path.stat().st_size
    print(f"  DEX 大小: {dex_new_size} bytes (原始: {len(dex_data)})")
    if dex_new_size < 1000000:
        print(f"错误：新的 DEX 太小 ({dex_new_size})")
        return False

    # 7. 构建 APK
    print("[7/7] 构建 APK...")
    patched_apk = work_dir / "patched.apk"
    with zipfile.ZipFile(input_apk, "r") as zin:
        with zipfile.ZipFile(patched_apk, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename.startswith("META-INF/"):
                    continue
                if item.filename == "classes.dex":
                    continue
                zout.writestr(item, zin.read(item.filename))
            zout.write(dex_new_path, "classes.dex")

    # 签名
    print("  签名 APK...")
    ensure_debug_keystore(keystore_path)

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
        print("  ✓ apksigner 签名完成（V1+V2+V3）")
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
