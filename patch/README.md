# 豆包输入法 → 系统字体补丁

## 概述

将豆包输入法（com.bytedance.android.doubaoime）的自定义字体替换为系统字体，同时保留图标字体（oimeui2023/oimeui2025）保证图标正常显示。

## 修改原理

### 修改一：入口方法（KeyboardView.createTypeFace）

`com.bytedance.android.input.keyboard.KeyboardView.createTypeFace(String, boolean, int)`

这是一个 `public static` 方法，接收字体名称、是否粗体、字重三个参数，返回 `Typeface` 对象。

### 原始逻辑（Java 伪代码）

```java
public static Typeface createTypeFace(String fontName, boolean bold, int weight) {
    // 1. 从云端拉配置（决定 isSupportSystemFont）
    KeyboardJni.updateIsSupportSystemFont();
    
    try {
        if (fontName.endsWith(".ttf")) {
            // 用皮肤包里的 TTF 文件
            return Typeface.createFromAsset(assets, "skin/default/" + fontName);
        } else if ("oimeui2023".equalsIgnoreCase(fontName)) {
            // 图标字体 #1
            return ResourcesCompat.getFont(context, R.font.oimeui2023);
        } else if ("oimeui2025".equalsIgnoreCase(fontName)) {
            // 图标字体 #2
            return ResourcesCompat.getFont(context, R.font.oimeui2025);
        } else if (fontName.contains("_adaptive")) {
            if (KeyboardJni.isSupportSystemFont()) {
                return Typeface.create(fontName, 0);           // ← 仅这路用系统字体
            } else {
                return ResourcesCompat.getFont(context, R.font.qihei); // ← 还是 qihei
            }
        } else {
            // 默认所有非图标文本 → qihei 自定义字体
            return ResourcesCompat.getFont(context, R.font.qihei);
        }
    } catch (Exception e) {
        return Typeface.create(fontName, 0); // 异常回退到系统字体
    }
}
```

### 修改后逻辑

```java
public static Typeface createTypeFace(String fontName, boolean bold, int weight) {
    if (fontName.endsWith(".ttf")) {
        return Typeface.createFromAsset(assets, "skin/default/" + fontName);
    } else if ("oimeui2023".equalsIgnoreCase(fontName)) {
        return ResourcesCompat.getFont(context, R.font.oimeui2023);   // 图标字体保留
    } else if ("oimeui2025".equalsIgnoreCase(fontName)) {
        return ResourcesCompat.getFont(context, R.font.oimeui2025);   // 图标字体保留
    } else {
        return Typeface.create(fontName, 0);                          // ← 全部用系统字体
    }
}
```

### APK 内置字体资源

| 文件 | 用途 |
|------|------|
| `res/font/qihei.ttf` | 自定义中文/键盘标签字体（替换目标） |
| `res/font/oimeui2023.ttf` | 图标字体 #1（保留） |
| `res/font/oimeui2025.ttf` | 图标字体 #2（保留） |
| `res/font/noto_color_emoji.ttf` | Emoji 字体 |
| `res/font/roboto.xml` | Roboto 字体配置 |

### Font 定义（style.xml）

字体名称 → facename 映射关系：

| facename | 用途 | 修改 |
|----------|------|------|
| `oimeui2023` | 图标：删除键、中英切换、空格图标、工具栏等 | **保留** |
| `oimeui2025` | 图标（部分） | **保留** |
| `qihei` | 键盘字母标签、中文文本 | → 系统字体 |
| `misans_medium` | 英文标签、功能键文字 | → 系统字体 |
| `misans_medium_adaptive` | 候选词文字、设置界面文字 | → 系统字体 |
| `misans_regular_adaptive` | 设置文字 | → 系统字体 |

### 修改二：候选栏拼音显示区域字体

除了 `createTypeFace()` 中央字体工厂外，还有 **3 个位置** 直接通过 `ResourcesCompat.getFont(context, R.font.qihei)` 加载字体，绕过了 `createTypeFace()`：

| 文件 | 影响区域 | 说明 |
|------|----------|------|
| `CandidateListView` (内部类 `i`) | **拼音显示栏**（候选词上方） | 显示当前输入的拼音文字 |
| `CandidateIdleView` (内部类 `G`) | 空闲状态候选栏 | 空闲时的候选栏字体 |
| `MoreCandidateSyllableAdapter` | 更多候选词弹窗音节列表 | 点击"更多"后的拼音音节 |

这些位置的 `R.font.qihei` 加载被替换为 `Typeface.create("sans-serif", Typeface.NORMAL)`，使拼音显示区域也跟随系统字体。

## 修改方式

### 方案：Smali 级别修改（仅改 DEX）

使用 **baksmali → smali** 工具链，只修改 `classes.dex` 中的 smali 文件，完全不触碰 APK 中的任何资源文件（图片、XML 等）。

修改的 smali 文件：

| 文件 | 修改内容 |
|------|----------|
| `KeyboardView.smali` | `createTypeFace()` 方法：保留图标字体，其余用系统字体 |
| `CandidateListView.smali` | 拼音显示栏 qihei → 系统字体 |
| `CandidateIdleView.smali` | 空闲候选栏 qihei → 系统字体 |
| `MoreCandidateSyllableAdapter.smali` | 音节列表 qihei → 系统字体 |

### 为什么不用 apktool

apktool 解码 APK 时会对资源文件进行重编译（PNG 重新压缩、XML 重新序列化），导致原版 APK 中的资源表被破坏，图标会显示为带问号的圆圈（tofu）。

## 构建流程

```
1. 从原版 APK 提取 classes.dex
2. baksmali 反编译 classes.dex → smali 文件
3. 修改 KeyboardView.smali 中的 createTypeFace 方法
4. 修补 CandidateListView / CandidateIdleView / MoreCandidateSyllableAdapter 中的 qihei 加载
5. smali 汇编回 classes.dex
6. 用 ZIP 工具替换原版 APK 中的 classes.dex（去掉旧 META-INF）
7. apksigner 签名（V1+V2+V3）
```

### 工具依赖

- Java 21（或任意 JDK 8+）
- [baksmali.jar](https://github.com/t-mw/smali/releases)
- [smali.jar](https://github.com/t-mw/smali/releases)
- **apksigner**（Android SDK Build Tools 自带，建议安装）
  - 如果没有 apksigner，脚本会回退到 jarsigner（仅 V1 签名）
  - V1 签名在 Android 7.0+ 上安装会失败（错误码 33 "packageinfo is null"）
  - 此时可用 MT 管理器重新签名后安装
- Python 3（用于补丁脚本）

### 安装 Android SDK Build Tools（获取 apksigner）

```bash
# 方法 1：通过 Android Studio → SDK Manager → SDK Tools → Android SDK Build-Tools
# 方法 2：命令行（需要 sdkmanager）
sdkmanager "build-tools;35.0.0"
```

### 一键构建

```bash
# Windows Git Bash
python patch-font.py
```

脚本会自动完成：反编译 → 打补丁 → 汇编 → 构建 APK → 签名 → 输出到桌面。

## 注意事项

1. **必须用原版 APK 为基础**，不要用任何已经修改过的版本
2. 构建时跳过 META-INF（旧签名）和旧的 classes.dex，其他文件原样保留
3. 签名使用 Android Debug 证书（`~/.android/debug.keystore`），如果不存在脚本会自动创建
4. 优先使用 apksigner 签名（V1+V2+V3），兼容 Android 7.0+ 设备
5. 如果没有 apksigner，回退到 jarsigner（仅 V1），需要在 Android 7.0+ 上用 MT 管理器重新签名
6. 如果输入法界面无法拉起，检查 `Build$VERSION` 是否在 smali 中被错误转义（不要用 Node.js -e 内联脚本）
