package sa.nq.phone

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** بعد ريستارت الموبايل أو تحديث التطبيق: نشغّل الخدمة ونطلّع إشعار يفتح التطبيق بدوسة
 *  (أندرويد 10+ مابيسمحش نفتح الشاشة لوحدنا من الخلفية) */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        try { PhoneService.send(ctx, PhoneService.ACTION_BOOT) } catch (e: Exception) {}
    }
}
