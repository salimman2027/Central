package android.print

import android.os.CancellationSignal
import android.os.ParcelFileDescriptor
import java.io.File

/** بيحوّل صفحة WebView لملف PDF (الكلاسات دي package-private في android.print) */
class PdfPrint(private val attrs: PrintAttributes) {
    fun print(adapter: PrintDocumentAdapter, out: File, done: (Boolean) -> Unit) {
        adapter.onLayout(null, attrs, null, object : PrintDocumentAdapter.LayoutResultCallback() {
            override fun onLayoutFinished(info: PrintDocumentInfo?, changed: Boolean) {
                val fd = ParcelFileDescriptor.open(out, ParcelFileDescriptor.MODE_CREATE or ParcelFileDescriptor.MODE_TRUNCATE or ParcelFileDescriptor.MODE_READ_WRITE)
                adapter.onWrite(arrayOf(PageRange.ALL_PAGES), fd, CancellationSignal(), object : PrintDocumentAdapter.WriteResultCallback() {
                    override fun onWriteFinished(pages: Array<out PageRange>?) { fd.close(); done(true) }
                    override fun onWriteFailed(error: CharSequence?) { fd.close(); done(false) }
                })
            }
            override fun onLayoutFailed(error: CharSequence?) { done(false) }
        }, null)
    }
}
