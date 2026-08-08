.class final Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch$1;
.super Ljava/lang/Object;
.source "EnglishAssociationPatch.java"

# interfaces
.implements Landroid/view/View$OnClickListener;


# instance fields
.field private final switchView:Landroidx/appcompat/widget/SwitchCompat;


# direct methods
.method constructor <init>(Landroidx/appcompat/widget/SwitchCompat;)V
    .registers 2

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    iput-object p1, p0, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch$1;->switchView:Landroidx/appcompat/widget/SwitchCompat;

    return-void
.end method


# virtual methods
.method public onClick(Landroid/view/View;)V
    .registers 3

    iget-object p1, p0, Lcom/bytedance/android/input/fragment/settings/EnglishAssociationPatch$1;->switchView:Landroidx/appcompat/widget/SwitchCompat;

    invoke-virtual {p1}, Landroidx/appcompat/widget/SwitchCompat;->isChecked()Z

    move-result p1

    invoke-static {p1}, Ljava/lang/Boolean;->valueOf(Z)Ljava/lang/Boolean;

    move-result-object p1

    const-string v0, "english_word_association_enabled"

    invoke-static {v0, p1}, Lcom/bytedance/android/input/common/SettingsConfigNext;->l(Ljava/lang/String;Ljava/lang/Object;)V

    return-void
.end method
