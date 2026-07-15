# 豆包输入法 - 系统字体补丁

将豆包输入法的自定义字体（qihei、misans）替换为系统字体，让输入法与系统风格统一。

## 下载

前往 [Releases](https://github.com/lisrain/doubao-font-patch/releases) 页面下载最新版本。

文件名格式：`doubaoime-{版本号}-fontpatch.apk`

## 自动构建

本项目配置了 GitHub Actions，每天北京时间 **04:00** 自动检查豆包输入法是否有新版本：

- **有新版** → 自动下载 → 打补丁 → 签名 → 发布 Release + Artifact
- **无新版** → 跳过

也可以在 Actions 页面手动触发构建。

## 补丁内容

| 修改 | 说明 |
|------|------|
| 键盘字母/中文标签 | qihei → 系统字体 |
| 英文标签/功能键 | misans → 系统字体 |
| 候选词文字 | misans_adaptive → 系统字体 |
| 拼音显示栏 | qihei → 系统字体 |
| 图标字体 | **保留**（oimeui2023/oimeui2025） |
| 皮肤字体 | **保留**（.ttf 文件） |

## 安装

1. 卸载原版豆包输入法（或直接覆盖安装签名相同的版本）
2. 安装补丁版 APK
3. 重新设置输入法

## 签名说明

使用 Android Debug 证书签名（V1+V2+V3），可直接安装。

如安装失败，请用 MT 管理器等工具重新签名后安装。

## 技术细节

详见 [patch/README.md](patch/README.md)。
