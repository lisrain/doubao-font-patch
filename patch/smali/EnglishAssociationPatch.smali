.class public final Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;
.super Ljava/lang/Object;
.source "EnglishAssociationPatch.java"


# static fields
.field public static final KEY:Ljava/lang/String; = "english_word_association_enabled"

.field private static englishPreeditActive:Z


# direct methods
.method private constructor <init>()V
    .registers 1

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method
.method public static attach(Lcom/bytedance/android/input/fragment/settings/IntelligentAssociationFragment;Landroid/view/View;)V
    .registers 8

    invoke-static {p0, p1}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->attachStyled(Lcom/bytedance/android/input/fragment/settings/IntelligentAssociationFragment;Landroid/view/View;)V

    return-void
.end method
.method public static attachStyled(Lcom/bytedance/android/input/fragment/settings/IntelligentAssociationFragment;Landroid/view/View;)V
    .registers 9

    if-eqz p1, :return
    instance-of v0, p1, Landroid/view/ViewGroup;
    if-eqz v0, :return
    move-object v0, p1
    check-cast v0, Landroid/view/ViewGroup;
    const/4 v1, 0x1
    invoke-virtual {v0, v1}, Landroid/view/ViewGroup;->getChildAt(I)Landroid/view/View;
    move-result-object v2
    instance-of v3, v2, Landroid/view/ViewGroup;
    if-eqz v3, :return
    check-cast v2, Landroid/view/ViewGroup;
    const/4 v1, 0x0
    invoke-virtual {v2, v1}, Landroid/view/ViewGroup;->getChildAt(I)Landroid/view/View;
    move-result-object v2
    instance-of v3, v2, Landroid/view/ViewGroup;
    if-eqz v3, :return
    check-cast v2, Landroid/view/ViewGroup;
    const-string v1, "english_word_association_enabled"
    invoke-virtual {v2, v1}, Landroid/view/ViewGroup;->findViewWithTag(Ljava/lang/Object;)Landroid/view/View;
    move-result-object v3
    if-nez v3, :return

    new-instance v3, Lcom/bytedance/common_biz/ui/widget/ImeListItemView;
    invoke-virtual {p1}, Landroid/view/View;->getContext()Landroid/content/Context;
    move-result-object v4
    invoke-direct {v3, v4}, Lcom/bytedance/common_biz/ui/widget/ImeListItemView;-><init>(Landroid/content/Context;)V
    invoke-virtual {v3, v1}, Landroid/view/View;->setTag(Ljava/lang/Object;)V

    const v5, 0x7f0a0363
    invoke-virtual {v3, v5}, Landroid/view/View;->findViewById(I)Landroid/view/View;
    move-result-object v5
    if-eqz v5, :return
    check-cast v5, Landroid/widget/TextView;
    const-string v1, "英文单词补全联想"
    invoke-virtual {v5, v1}, Landroid/widget/TextView;->setText(Ljava/lang/CharSequence;)V

    new-instance v6, Landroid/graphics/drawable/GradientDrawable;
    invoke-direct {v6}, Landroid/graphics/drawable/GradientDrawable;-><init>()V
    invoke-virtual {p1}, Landroid/view/View;->getResources()Landroid/content/res/Resources;
    move-result-object v4
    const v5, 0x7f0703c3
    invoke-virtual {v4, v5}, Landroid/content/res/Resources;->getDimension(I)F
    move-result v5
    invoke-virtual {v6, v5}, Landroid/graphics/drawable/GradientDrawable;->setCornerRadius(F)V
    invoke-virtual {p1}, Landroid/view/View;->getContext()Landroid/content/Context;
    move-result-object v4
    const v5, 0x7f060253
    invoke-static {v4, v5}, Landroidx/core/content/ContextCompat;->getColor(Landroid/content/Context;I)I
    move-result v5
    invoke-virtual {v6, v5}, Landroid/graphics/drawable/GradientDrawable;->setColor(I)V
    invoke-virtual {v3, v6}, Landroid/view/View;->setBackground(Landroid/graphics/drawable/Drawable;)V

    const v5, 0x7f0a0629
    invoke-virtual {v3, v5}, Landroid/view/View;->findViewById(I)Landroid/view/View;
    move-result-object v5
    if-eqz v5, :return
    invoke-virtual {v5}, Landroid/view/View;->getPaddingLeft()I
    move-result v6
    invoke-virtual {v5}, Landroid/view/View;->getPaddingRight()I
    move-result v1
    invoke-virtual {p1}, Landroid/view/View;->getResources()Landroid/content/res/Resources;
    move-result-object v4
    const v0, 0x7f0703b8
    invoke-virtual {v4, v0}, Landroid/content/res/Resources;->getDimensionPixelSize(I)I
    move-result v0
    invoke-virtual {v5, v6, v0, v1, v0}, Landroid/view/View;->setPadding(IIII)V

    const v5, 0x7f0a0347
    invoke-virtual {v3, v5}, Landroid/view/View;->findViewById(I)Landroid/view/View;
    move-result-object v5
    if-eqz v5, :return
    const/4 v1, 0x0
    invoke-virtual {v5, v1}, Landroid/view/View;->setVisibility(I)V

    invoke-virtual {v3}, Lcom/bytedance/common_biz/ui/widget/ImeListItemView;->a()Landroidx/appcompat/widget/SwitchCompat;
    move-result-object v4
    if-eqz v4, :return
    invoke-virtual {v4, v1}, Landroid/view/View;->setVisibility(I)V
    invoke-virtual {v4, v1}, Landroid/view/View;->setHapticFeedbackEnabled(Z)V
    invoke-static {}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->isEnabled()Z
    move-result v1
    invoke-virtual {v4, v1}, Landroidx/appcompat/widget/SwitchCompat;->setChecked(Z)V
    new-instance v1, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch$1;
    invoke-direct {v1, v4}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch$1;-><init>(Landroidx/appcompat/widget/SwitchCompat;)V
    invoke-virtual {v4, v1}, Landroid/view/View;->setOnClickListener(Landroid/view/View$OnClickListener;)V
    new-instance v1, Landroid/widget/LinearLayout$LayoutParams;
    const/4 v4, -0x1
    const/4 v5, -0x2
    invoke-direct {v1, v4, v5}, Landroid/widget/LinearLayout$LayoutParams;-><init>(II)V
    invoke-virtual {p1}, Landroid/view/View;->getResources()Landroid/content/res/Resources;
    move-result-object v4
    const v5, 0x7f070491
    invoke-virtual {v4, v5}, Landroid/content/res/Resources;->getDimensionPixelSize(I)I
    move-result v4
    iput v4, v1, Landroid/view/ViewGroup$MarginLayoutParams;->topMargin:I
    invoke-virtual {v3, v1}, Landroid/view/View;->setLayoutParams(Landroid/view/ViewGroup$LayoutParams;)V
    invoke-virtual {v2, v3}, Landroid/view/ViewGroup;->addView(Landroid/view/View;)V

    :return
    return-void
.end method

.method public static commitEnglishPreeditBeforeBoardSwitch()V
    .registers 2

    invoke-static {}, Lcom/bytedance/android/doubaoime/KeyboardJni;->getKeyboardJni()Lcom/bytedance/android/doubaoime/KeyboardJni;
    move-result-object v0
    invoke-virtual {v0}, Lcom/bytedance/android/doubaoime/KeyboardJni;->IsEnglishKeyboard()Z
    move-result v1
    if-eqz v1, :return

    invoke-static {}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->isEnabled()Z
    move-result v1
    invoke-static {v1}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->syncNativeAssociationState(Z)V

    invoke-static {}, Lcom/bytedance/android/doubaoime/KeyboardJni;->hasPreedit()Z
    move-result v1
    if-eqz v1, :return

    invoke-virtual {v0}, Lcom/bytedance/android/doubaoime/KeyboardJni;->stopInputAndCommitPinyin()V
    const/4 v0, 0x1
    invoke-static {v0}, Lcom/bytedance/android/doubaoime/KeyboardJni;->finishPreedit(Z)V

    :return
    return-void
.end method

.method public static clearEnglishPreeditActive()V
    .registers 1

    const/4 v0, 0x0
    sput-boolean v0, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->englishPreeditActive:Z

    return-void
.end method

.method private static isAsciiIdentifier(Ljava/lang/CharSequence;)Z
    .registers 5

    if-eqz p0, :false
    invoke-interface {p0}, Ljava/lang/CharSequence;->length()I
    move-result v0
    if-lez v0, :false
    const/4 v1, 0x0

    :loop
    if-ge v1, v0, :true
    invoke-interface {p0, v1}, Ljava/lang/CharSequence;->charAt(I)C
    move-result v2

    const/16 v3, 0x30
    if-lt v2, v3, :check_upper
    const/16 v3, 0x39
    if-le v2, v3, :next

    :check_upper
    const/16 v3, 0x41
    if-lt v2, v3, :check_lower
    const/16 v3, 0x5a
    if-le v2, v3, :next

    :check_lower
    const/16 v3, 0x61
    if-lt v2, v3, :check_separator
    const/16 v3, 0x7a
    if-le v2, v3, :next

    :check_separator
    const/16 v3, 0x2d
    if-eq v2, v3, :next
    const/16 v3, 0x2e
    if-eq v2, v3, :next
    const/16 v3, 0x5f
    if-ne v2, v3, :false

    :next
    add-int/lit8 v1, v1, 0x1
    goto :loop

    :true
    const/4 v0, 0x1
    return v0

    :false
    const/4 v0, 0x0
    return v0
.end method

.method public static suppressAsciiPreeditRange(Lcom/bytedance/android/input/editor/a;II)Z
    .registers 7

    invoke-static {}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->isEnabled()Z
    move-result v0
    if-nez v0, :false

    sget-boolean v0, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->englishPreeditActive:Z
    if-eqz v0, :false
    if-lez p1, :false

    const/4 v0, 0x0
    invoke-virtual {p0, p1, v0}, Lcom/bytedance/android/input/editor/a;->getTextBeforeCursor(II)Ljava/lang/CharSequence;
    move-result-object v1
    invoke-static {v1}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->isAsciiIdentifier(Ljava/lang/CharSequence;)Z
    move-result v0
    if-eqz v0, :false

    if-lez p2, :suppress
    const/4 v0, 0x0
    invoke-virtual {p0, p2, v0}, Lcom/bytedance/android/input/editor/a;->getTextAfterCursor(II)Ljava/lang/CharSequence;
    move-result-object v1
    invoke-static {v1}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->isAsciiIdentifier(Ljava/lang/CharSequence;)Z
    move-result v0
    if-eqz v0, :false

    :suppress
    invoke-virtual {p0}, Lcom/bytedance/android/input/editor/a;->finishComposingText()Z
    invoke-static {}, Lcom/bytedance/android/doubaoime/KeyboardJni;->resetPreEditStartPosition()V
    invoke-static {}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->clearEnglishPreeditActive()V
    const/4 v0, 0x1
    return v0

    :false
    const/4 v0, 0x0
    return v0
.end method

.method public static trackEnglishPreedit(Ljava/lang/String;)V
    .registers 3

    if-eqz p0, :clear
    invoke-virtual {p0}, Ljava/lang/String;->isEmpty()Z
    move-result v0
    if-nez v0, :clear

    invoke-static {}, Lcom/bytedance/android/doubaoime/KeyboardJni;->getKeyboardJni()Lcom/bytedance/android/doubaoime/KeyboardJni;
    move-result-object v0
    invoke-virtual {v0}, Lcom/bytedance/android/doubaoime/KeyboardJni;->IsEnglishKeyboard()Z
    move-result v0
    if-eqz v0, :return

    invoke-static {}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->isEnabled()Z
    move-result v1
    if-nez v1, :mark_active
    invoke-static {v1}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->syncNativeAssociationState(Z)V

    :mark_active
    const/4 v0, 0x1
    sput-boolean v0, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->englishPreeditActive:Z
    return-void

    :clear
    invoke-static {}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->clearEnglishPreeditActive()V

    :return
    return-void
.end method

.method public static filterAssociationDisabled(Z)Z
    .registers 2

    if-nez p0, :blocked

    invoke-static {}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->isEnabled()Z

    move-result v0

    if-nez v0, :return

    invoke-static {}, Lcom/bytedance/android/doubaoime/KeyboardJni;->getKeyboardJni()Lcom/bytedance/android/doubaoime/KeyboardJni;

    move-result-object v0

    invoke-virtual {v0}, Lcom/bytedance/android/doubaoime/KeyboardJni;->IsEnglishKeyboard()Z

    move-result v0

    if-eqz v0, :return

    const/4 v0, 0x0

    invoke-static {v0}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->syncNativeAssociationState(Z)V

    :blocked
    const/4 p0, 0x1

    :return
    return p0
.end method

.method public static filterNeedAssociation(Z)Z
    .registers 2

    if-eqz p0, :return

    invoke-static {}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->isEnabled()Z

    move-result v0

    if-nez v0, :return

    invoke-static {}, Lcom/bytedance/android/doubaoime/KeyboardJni;->getKeyboardJni()Lcom/bytedance/android/doubaoime/KeyboardJni;

    move-result-object v0

    invoke-virtual {v0}, Lcom/bytedance/android/doubaoime/KeyboardJni;->IsEnglishKeyboard()Z

    move-result v0

    if-eqz v0, :return

    const/4 v0, 0x0

    invoke-static {v0}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->syncNativeAssociationState(Z)V

    const/4 p0, 0x0

    :return
    return p0
.end method

.method public static isEnabled()Z
    .registers 4

    sget-object v0, Lcom/bytedance/android/input/basic/IAppGlobals;->a:Lcom/bytedance/android/input/basic/IAppGlobals$a;

    invoke-virtual {v0}, Lcom/bytedance/android/input/basic/IAppGlobals$a;->getContext()Landroid/content/Context;

    move-result-object v0

    invoke-static {v0}, Landroidx/preference/PreferenceManager;->getDefaultSharedPreferences(Landroid/content/Context;)Landroid/content/SharedPreferences;

    move-result-object v0

    const-string v1, "english_word_association_enabled"

    const/4 v2, 0x1

    invoke-interface {v0, v1, v2}, Landroid/content/SharedPreferences;->getBoolean(Ljava/lang/String;Z)Z

    move-result v0

    return v0
.end method

.method public static isPatchKey(Ljava/lang/String;)Z
    .registers 2

    const-string v0, "english_word_association_enabled"

    invoke-virtual {v0, p0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result p0

    return p0
.end method

.method public static setEnabled(Z)V
    .registers 4

    move v2, p0

    sget-object v0, Lcom/bytedance/android/input/basic/IAppGlobals;->a:Lcom/bytedance/android/input/basic/IAppGlobals$a;

    invoke-virtual {v0}, Lcom/bytedance/android/input/basic/IAppGlobals$a;->getContext()Landroid/content/Context;

    move-result-object v0

    invoke-static {v0}, Landroidx/preference/PreferenceManager;->getDefaultSharedPreferences(Landroid/content/Context;)Landroid/content/SharedPreferences;

    move-result-object v0

    invoke-interface {v0}, Landroid/content/SharedPreferences;->edit()Landroid/content/SharedPreferences$Editor;

    move-result-object v0

    const-string v1, "english_word_association_enabled"

    invoke-interface {v0, v1, p0}, Landroid/content/SharedPreferences$Editor;->putBoolean(Ljava/lang/String;Z)Landroid/content/SharedPreferences$Editor;

    move-result-object p0

    invoke-interface {p0}, Landroid/content/SharedPreferences$Editor;->apply()V

    invoke-static {v2}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->syncNativeAssociationState(Z)V

    return-void
.end method

.method private static syncNativeAssociationState(Z)V
    .registers 3

    :try_start
    invoke-static {}, Lcom/bytedance/android/doubaoime/KeyboardJni;->getKeyboardJni()Lcom/bytedance/android/doubaoime/KeyboardJni;
    move-result-object v0
    invoke-virtual {v0}, Lcom/bytedance/android/doubaoime/KeyboardJni;->IsEnglishKeyboard()Z
    move-result v1
    if-eqz v1, :return
    invoke-virtual {v0, p0}, Lcom/bytedance/android/doubaoime/KeyboardJni;->setAssociationEnabled(Z)V
    :try_end
    .catch Ljava/lang/Throwable; {:try_start .. :try_end} :catch

    goto :return

    :catch
    move-exception v0

    :return
    return-void
.end method

.method public static setFromString(Ljava/lang/String;)V
    .registers 2

    invoke-static {p0}, Ljava/lang/Boolean;->parseBoolean(Ljava/lang/String;)Z

    move-result v0

    invoke-static {v0}, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch;->setEnabled(Z)V

    return-void
.end method
