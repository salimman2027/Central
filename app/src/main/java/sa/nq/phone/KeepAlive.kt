package sa.nq.phone

import android.app.Activity
import android.app.AlertDialog
import android.app.AppOpsManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings

/**
 * شاومي / ريدمي / بوكو (MIUI و HyperOS) بيقفلوا التطبيقات في الخلفية حتى لو فيها خدمة دايمة.
 * هنا بنفتح للموظف الشاشات اللي لازم تتظبط مرة واحدة عند التسطيب:
 *  1) تجاهل تحسين البطارية (أندرويد العادي)
 *  2) التشغيل التلقائي (Autostart) — شاومي
 *  3) توفير البطارية = بدون قيود — شاومي
 *  4) الظهور على شاشة القفل + الفتح من الخلفية — شاومي
 * وبعدها نقوله يقفل التطبيق في قايمة التطبيقات الأخيرة (القفل 🔒).
 */
object KeepAlive {
    private const val PREF = "nq_keepalive"

    val isXiaomi: Boolean get() {
        val m = (Build.MANUFACTURER + " " + Build.BRAND).lowercase()
        return m.contains("xiaomi") || m.contains("redmi") || m.contains("poco")
    }

    fun batteryOk(ctx: Context): Boolean =
        (ctx.getSystemService(Context.POWER_SERVICE) as PowerManager).isIgnoringBatteryOptimizations(ctx.packageName)

    /** حالة الـ Autostart في شاومي (AppOps 10008) — null = مش معروف */
    fun autostartOk(ctx: Context): Boolean? = try {
        val ops = ctx.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val m = AppOpsManager::class.java.getMethod("checkOpNoThrow", Int::class.javaPrimitiveType, Int::class.javaPrimitiveType, String::class.java)
        (m.invoke(ops, 10008, ctx.applicationInfo.uid, ctx.packageName) as Int) == AppOpsManager.MODE_ALLOWED
    } catch (e: Exception) { null }

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE)
    fun done(ctx: Context) = prefs(ctx).getBoolean("done", false)
    private fun setDone(ctx: Context) = prefs(ctx).edit().putBoolean("done", true).apply()

    /** أول تسطيب (أو لو حاجة اتقفلت تاني) — بيطلع مرة لحد ما الموظف يخلّص */
    fun maybeShow(a: Activity) {
        val need = !batteryOk(a) || (isXiaomi && (autostartOk(a) == false || !done(a)))
        if (need) a.window.decorView.postDelayed({ show(a) }, 1800)
    }

    fun show(a: Activity) {
        if (a.isFinishing) return
        val bat = batteryOk(a)
        val auto = autostartOk(a)
        val steps = mutableListOf<Pair<String, () -> Unit>>()
        steps += ((if (bat) "✅ " else "1️⃣ ") + "البطارية: سيبه يشتغل من غير قيود" to { openBattery(a) })
        if (isXiaomi) {
            steps += ((if (auto == true) "✅ " else "2️⃣ ") + "التشغيل التلقائي (Autostart): فعّله" to { openAutostart(a) })
            steps += ("3️⃣ توفير البطارية: اختار «بدون قيود»" to { openMiuiBattery(a) })
            steps += ("4️⃣ الصلاحيات: «إظهار على شاشة القفل» و«فتح نوافذ من الخلفية»" to { openMiuiPerms(a) })
        }
        val msg = buildString {
            append("عشان المكالمات الواردة توصلك والموبايل مقفول، والتطبيق مايتقفلش لوحده")
            if (isXiaomi) append(" (شاومي بيقفل التطبيقات في الخلفية)")
            append(":\n\nدوس على كل خطوة وفعّلها، وارجع للتطبيق.")
            if (isXiaomi) append("\n\n5️⃣ في الآخر: افتح التطبيقات الأخيرة، دوس مطوّل على NQ Phone واختار 🔒 قفل.")
        }
        AlertDialog.Builder(a)
            .setTitle("🔋 خلّي التطبيق شغّال دايمًا")
            .setMessage(msg)
            .setItems(steps.map { it.first }.toTypedArray()) { _, i -> steps[i].second() }
            .setPositiveButton("خلّصت") { _, _ -> setDone(a) }
            .setNegativeButton("بعدين", null)
            .show()
    }

    private fun start(a: Activity, vararg intents: Intent): Boolean {
        for (i in intents) try { a.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); return true } catch (e: Exception) {}
        return false
    }

    private fun appDetails(a: Activity) =
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + a.packageName))

    fun openBattery(a: Activity) {
        if (!batteryOk(a)) start(a, Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + a.packageName)))
        else start(a, Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS), appDetails(a))
    }

    fun openAutostart(a: Activity) = start(a,
        Intent().setComponent(ComponentName("com.miui.securitycenter", "com.miui.permcenter.autostart.AutoStartManagementActivity")),
        Intent("miui.intent.action.OP_AUTO_START").addCategory(Intent.CATEGORY_DEFAULT),
        appDetails(a))

    fun openMiuiBattery(a: Activity) = start(a,
        Intent().setComponent(ComponentName("com.miui.powerkeeper", "com.miui.powerkeeper.ui.HiddenAppsConfigActivity"))
            .putExtra("package_name", a.packageName).putExtra("package_label", "NQ Phone"),
        appDetails(a))

    fun openMiuiPerms(a: Activity) = start(a,
        Intent("miui.intent.action.APP_PERM_EDITOR")
            .setClassName("com.miui.securitycenter", "com.miui.permcenter.permissions.PermissionsEditorActivity")
            .putExtra("extra_pkgname", a.packageName),
        Intent("miui.intent.action.APP_PERM_EDITOR").putExtra("extra_pkgname", a.packageName),
        appDetails(a))
}
