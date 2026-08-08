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
    KeyboardJni.updateIsSupportSystemFont();
    try {
        if (fontName.endsWith(".ttf")) {
            return Typeface.createFromAsset(assets, "skin/default/" + fontName);
        } else if ("oimeui2023".equalsIgnoreCase(fontName)) {
            return ResourcesCompat.getFont(context, R.font.oimeui2023);
        } else if ("oimeui2025".equalsIgnoreCase(fontName)) {
            return ResourcesCompat.getFont(context, R.font.oimeui2025);
        } else if (fontName.contains("_adaptive")) {
            if (KeyboardJni.isSupportSystemFont()) {
                return Typeface.create(fontName, 0);
            } else {
                return ResourcesCompat.getFont(context, R.font.qihei);
            }
        } else {
            return ResourcesCompat.getFont(context, R.font.qihei);
        }
    } catch (Exception e) {
        return Typeface.create(fontName, 0);
    }
}
```

### 修改后逻辑

```java
public static Typeface createTypeFace(String fontName, boolean bold, int weight) {
    if (fontName.endsWith(".ttf")) {
        return Typeface.createFromAsset(assets, "skin/default/" + fontName);
    } else if ("oimeui2023".equalsIgnoreCase(fontName)) {
        return ResourcesCompat.getFont(context, R.font.oimeui2023);
    } else if ("oimeui2025".equalsIgnoreCase(fontName)) {
        return ResourcesCompat.getFont(context, R.font.oimeui2025);
    } else {
        return Typeface.create(fontName, 0);
    }
}
```

### Font 定义

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
| `CandidateListView` (内部类，自动检测) | **拼音显示栏**（候选词上方） | 显示当前输入的拼音文字 |
| `CandidateIdleView` (内部类，自动检测) | 空闲状态候选栏 | 空闲时的候选栏字体 |
| `MoreCandidateSyllableAdapter` | 更多候选词弹窗音节列表 | 点击"更多"后的拼音音节 |

这些位置的 `R.font.qihei` 加载被替换为 `Typeface.create("sans-serif", Typeface.NORMAL)`，使拼音显示区域也跟随系统字体。

### 修改三：英文单词补全联想开关

在“设置 → 智能联想”页面额外添加“英文单词补全联想”开关，默认开启。开关使用豆包原有的标准单行 `ImeListItemView` 设置项组件，复用原生标题、开关、15dp 圆角、主题背景和垂直居中布局，并使用 14dp 上下内边距与原页面相同的 `ime_dp_6`（6dp）卡片间距。

关闭后，当输入法检测到当前为英文键盘时，补丁会同步关闭 native 引擎的 association 状态，并把光标更新参数中的 `SelectionUpdatedParams.need_association` 置为 `false`。这样既阻止光标停在不完整英文单词后自动触发补全联想，也避免 `claude-opus-` 一类已提交标识符在符号输入后重新作为整段候选出现并被重复追加。

本功能使用纯 DEX/Smali 修改，不重新编译资源文件，配置键为 `english_word_association_enabled`，并复用 `SettingsConfigNext` 的设置进程与输入法进程同步链路。

### 修改四：英文 composing 文本的数字/符号键盘切换提交

英文输入过程中，当前单词可能以带下划线的 composing/preedit 文本存在。原逻辑切换到数字或符号键盘时直接调用 native `switchKeyboard(5/3)`，没有先调用 `finishComposingText()`，导致返回英文键盘后下一次字母输入会覆盖之前的整个组合单词。

补丁在数字和符号键盘切换入口前增加提交逻辑，仅当当前是英文键盘且存在 preedit 时执行 `stopInputAndCommitPinyin()` 与 `finishPreedit(true)`，中文输入和无 composing 文本时保持原行为。同时跟踪英文 `UpdatePreedit()` 状态，在关闭英文联想开关时拦截 ASCII 字母、数字、连字符、下划线和点号组成的 `SetPreeditRange()`，并清理 `preEditStartPosition`，避免 `claude-opus` 等连续标识符在切换数字键盘后重新变成整段可覆盖的下划线编辑区。

## 修改方式

### 方案：Smali 级别修改（仅改 DEX）

使用 **baksmali → smali** 工具链，只修改 `classes.dex` 中的 smali 文件，完全不触碰 APK 中的任何资源文件（图片、XML 等）。

修改的 smali 文件：

| 文件 | 修改内容 |
|------|----------|
| `KeyboardView.smali` | `createTypeFace()` 方法：保留图标字体，其余用系统字体 |
| 候选栏内部类（自动检测） | qihei → 系统字体 |
| `MoreCandidateSyllableAdapter.smali` | 音节列表 qihei → 系统字体 |
| `SettingsConfigNext.smali` | 注册并同步英文单词补全配置 |
| `KeyboardJni$1.smali` | 过滤英文光标联想参数 |
| `IntelligentAssociationFragment.smali` | 挂载英文联想开关 |
| `InputViewRoot$F.smali` | 数字/符号键盘切换前提交英文 composing 文本 |
| `EnglishAssociationPatch*.smali` | 配置读取、UI 监听和过滤逻辑 |

**自动检测**：脚本通过搜索 `0x7f090003`（R.font.qihei）+ `ResourcesCompat;->getFont` 模式自动定位所有需要修补的文件，无需硬编码内部类名，兼容所有版本。

### 为什么不用 apktool

apktool 解码 APK 时会对资源文件进行重编译（PNG 重新压缩、XML 重新序列化），导致原版 APK 中的资源表被破坏，图标会显示为带问号的圆圈（tofu）。

## 构建流程

```
1. 从原版 APK 提取 classes.dex
2. baksmali 反编译 classes.dex → smali 文件
3. 修改 KeyboardView.smali 中的 createTypeFace 方法
4. 自动检测并修补所有 qihei 加载点
5. smali 汇编回 classes.dex
6. 用 ZIP 工具替换原版 APK 中的 classes.dex（去掉旧 META-INF）
7. 签名（V1+V2+V3 或 jarsigner V1）
```

## 工具依赖

- Java 8+
- [baksmali.jar](https://github.com/baksmali/smali/releases) / [smali.jar](https://github.com/baksmali/smali/releases)（CI 动态下载官方 v3.0.9 `fat-release` JAR，校验 SHA-256 后在临时目录中使用，构建结束即丢弃）
- **apksigner**（Android SDK Build Tools 自带，优先使用）
  - 没有 apksigner 时回退到 jarsigner（仅 V1 签名）
  - V1 签名在 Android 7.0+ 上可能安装失败，可用 MT 管理器重新签名
- Python 3

## 本地使用

```bash
# CI/跨平台版（推荐）
uv run --python 3.12 ci-patch.py <原版.apk> <输出.apk>

# 指定 keystore
uv run --python 3.12 ci-patch.py <原版.apk> <输出.apk> --keystore /path/to/debug.keystore
```

## 注意事项

1. **必须用原版 APK 为基础**，不要用任何已经修改过的版本
2. 构建时跳过 META-INF（旧签名）和旧的 classes.dex，其他文件原样保留
3. 签名使用 Android Debug 证书，如果不存在脚本会自动创建
4. 优先使用 apksigner 签名并验证 V2/V3，兼容 Android 7.0+ 设备
5. 候选栏内部类名在版本间会变化（如 `$h` → `$i`），脚本通过资源 ID 自动检测，无需手动更新
