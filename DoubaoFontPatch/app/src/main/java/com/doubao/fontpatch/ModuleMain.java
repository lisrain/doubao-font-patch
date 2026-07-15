package com.doubao.fontpatch;

import android.content.Context;
import android.graphics.Typeface;
import android.util.Log;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.Enumeration;
import java.util.Locale;

import dalvik.system.DexFile;
import io.github.libxposed.api.XposedModule;

/**
 * 豆包输入法系统字体补丁 - LSPosed 模块
 *
 * 核心策略：hook Typeface 的缓存机制 + ResourcesCompat.getFont
 * 当缓存中返回 qihei 字体时，替换为系统字体。
 */
public final class ModuleMain extends XposedModule {

    private static final String TAG = "DoubaoFontPatch";

    private static final Typeface SYSTEM_TYPEFACE = Typeface.create("sans-serif", Typeface.NORMAL);

    private String mTargetPackage = null;
    private ClassLoader mTargetLoader = null;

    @Override
    public void onModuleLoaded(ModuleLoadedParam param) {
        log(Log.INFO, TAG, String.format(Locale.getDefault(),
                "模块已加载: %s API %d", getFrameworkName(), getApiVersion()));
    }

    @Override
    public void onPackageLoaded(PackageLoadedParam param) {
        mTargetPackage = param.getPackageName();
        mTargetLoader = param.getDefaultClassLoader();
        log(Log.INFO, TAG, "包已加载: " + mTargetPackage);
    }

    @Override
    public void onPackageReady(PackageReadyParam param) {
        log(Log.INFO, TAG, "开始安装 Hook...");

        // 方案 1：Hook Typeface.create(String, int) - 拦截字体创建
        hookTypefaceCreate();

        // 方案 2：Hook ResourcesCompat.getFont - 拦截资源字体加载
        hookResourcesCompatFontAll();

        // 方案 3：Hook Typeface 构造函数 - 拦截字体对象创建
        hookTypefaceConstructor();

        // 方案 4：动态发现候选栏字体
        hookCandidateLazyProviders();

        log(Log.INFO, TAG, "所有 Hook 已安装");
    }

    /**
     * Hook Typeface.create(String, int)
     * 当 fontName 是 qihei/misans 时返回系统字体
     */
    private void hookTypefaceCreate() {
        try {
            Class<?> tfClass = Typeface.class;

            for (Method m : tfClass.getDeclaredMethods()) {
                if (!"create".equals(m.getName())) continue;
                Class<?>[] params = m.getParameterTypes();
                if (params.length == 2 && params[0] == String.class && params[1] == int.class) {
                    hook(m).intercept(chain -> {
                        String familyName = (String) chain.getArg(0);
                        if (familyName != null) {
                            String lower = familyName.toLowerCase(Locale.ROOT);
                            if (lower.contains("qihei") || lower.contains("misans")) {
                                log(Log.INFO, TAG, "Typeface.create 拦截: " + familyName);
                                return SYSTEM_TYPEFACE;
                            }
                        }
                        return chain.proceed();
                    });
                    log(Log.INFO, TAG, "已 Hook Typeface.create(String,int)");
                    break;
                }
            }
        } catch (Throwable t) {
            log(Log.ERROR, TAG, "Hook Typeface.create 失败", t);
        }
    }

    /**
     * Hook ResourcesCompat.getFont 的所有重载版本
     */
    private void hookResourcesCompatFontAll() {
        try {
            Class<?> clazz = Class.forName(
                    "androidx.core.content.res.ResourcesCompat", false, mTargetLoader);

            for (Method m : clazz.getDeclaredMethods()) {
                if (!"getFont".equals(m.getName())) continue;
                Class<?>[] params = m.getParameterTypes();
                if (params.length < 2) continue;
                if (params[0] != Context.class) continue;
                if (m.getReturnType() != Typeface.class) continue;

                final int paramCount = params.length;
                hook(m).intercept(chain -> {
                    Context ctx = (Context) chain.getArg(0);
                    if (ctx == null || !mTargetPackage.equals(ctx.getPackageName())) {
                        return chain.proceed();
                    }

                    int resId = (int) chain.getArg(1);

                    try {
                        String typeName = ctx.getResources().getResourceTypeName(resId);
                        if ("font".equals(typeName)) {
                            String entryName = ctx.getResources().getResourceEntryName(resId);
                            if (!"roboto".equals(entryName)
                                    && !"noto_color_emoji".equals(entryName)) {
                                log(Log.INFO, TAG, "ResourcesCompat.getFont 拦截: "
                                        + entryName + " (0x" + Integer.toHexString(resId) + ")");
                                return SYSTEM_TYPEFACE;
                            }
                        }
                    } catch (Throwable ignored) {
                    }

                    return chain.proceed();
                });

                log(Log.INFO, TAG, "已 Hook ResourcesCompat.getFont (参数数: " + paramCount + ")");
            }
        } catch (Throwable t) {
            log(Log.ERROR, TAG, "Hook ResourcesCompat.getFont 失败", t);
        }
    }

    /**
     * Hook Typeface 构造函数
     * 在 Typeface 对象创建时检查并替换
     */
    private void hookTypefaceConstructor() {
        try {
            for (Constructor<?> ctor : Typeface.class.getDeclaredConstructors()) {
                // 找 Typeface(Typeface, int, int) 或类似构造函数
                hook(ctor).intercept(chain -> {
                    Typeface result = (Typeface) chain.proceed();
                    // 检查返回的 Typeface 是否可能是 qihei
                    // 通过检查其 family name
                    if (result != null) {
                        try {
                            Field familyField = Typeface.class.getDeclaredField("mFamily");
                            familyField.setAccessible(true);
                            String family = (String) familyField.get(result);
                            if (family != null && (family.contains("qihei") || family.contains("misans"))) {
                                log(Log.INFO, TAG, "Typeface 构造拦截: " + family);
                                return SYSTEM_TYPEFACE;
                            }
                        } catch (Throwable ignored) {
                        }
                    }
                    return result;
                });
            }
            log(Log.INFO, TAG, "已 Hook Typeface 构造函数");
        } catch (Throwable t) {
            log(Log.WARN, TAG, "Hook Typeface 构造函数失败: " + t.getMessage());
        }
    }

    /**
     * 动态发现候选栏 Lazy<Typeface> 内部类
     */
    private void hookCandidateLazyProviders() {
        try {
            Class<?> lazyInterface = Class.forName("kotlin.s.b.a", false, mTargetLoader);
            String pkg = "com.bytedance.common_biz.tool_bar.view.views";

            Object pathList = getDexPathList(mTargetLoader);
            if (pathList == null) return;

            DexFile[] dexFiles = getDexFiles(pathList);
            if (dexFiles == null) return;

            int hookedCount = 0;
            for (DexFile dex : dexFiles) {
                Enumeration<String> entries = dex.entries();
                while (entries.hasMoreElements()) {
                    String className = entries.nextElement();
                    if (!className.startsWith(pkg)) continue;

                    try {
                        Class<?> clazz = Class.forName(className, false, mTargetLoader);
                        if (!lazyInterface.isAssignableFrom(clazz)) continue;

                        for (Method m : clazz.getDeclaredMethods()) {
                            if ("invoke".equals(m.getName())
                                    && m.getParameterCount() == 0
                                    && m.getReturnType() == Typeface.class) {
                                hook(m).intercept(chain -> SYSTEM_TYPEFACE);
                                hookedCount++;
                                break;
                            }
                        }
                    } catch (Throwable ignored) {
                    }
                }
            }

            if (hookedCount > 0) {
                log(Log.INFO, TAG, "共 Hook " + hookedCount + " 个候选栏字体提供者");
            }
        } catch (Throwable t) {
            log(Log.WARN, TAG, "动态发现候选栏字体提供者失败: " + t.getMessage());
        }
    }

    @SuppressWarnings("JavaReflectionMemberAccess")
    private Object getDexPathList(ClassLoader loader) {
        try {
            Field f = loader.getClass().getDeclaredField("pathList");
            f.setAccessible(true);
            return f.get(loader);
        } catch (Throwable t) {
            return null;
        }
    }

    @SuppressWarnings("JavaReflectionMemberAccess")
    private DexFile[] getDexFiles(Object pathList) {
        try {
            Field f = pathList.getClass().getDeclaredField("dexElements");
            f.setAccessible(true);
            Object[] elements = (Object[]) f.get(pathList);
            if (elements == null) return null;

            Field dexField = elements[0].getClass().getDeclaredField("dexFile");
            dexField.setAccessible(true);

            DexFile[] result = new DexFile[elements.length];
            for (int i = 0; i < elements.length; i++) {
                result[i] = (DexFile) dexField.get(elements[i]);
            }
            return result;
        } catch (Throwable t) {
            return null;
        }
    }
}
