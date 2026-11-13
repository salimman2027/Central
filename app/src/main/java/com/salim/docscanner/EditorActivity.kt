package com.salim.docscanner

import android.app.AlertDialog
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.pdf.PdfRenderer
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.ParcelFileDescriptor
import android.util.LruCache
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.BaseAdapter
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import com.google.mlkit.vision.documentscanner.GmsDocumentScannerOptions
import com.google.mlkit.vision.documentscanner.GmsDocumentScanning
import com.google.mlkit.vision.documentscanner.GmsDocumentScanningResult
import java.io.File
import java.util.concurrent.Executors

class EditorActivity : ComponentActivity() {

    companion object {
        const val EXTRA_PATH = "path"
    }

    private val green = Color.parseColor("#1E6F5C")
    private lateinit var file: File
    private val pages = mutableListOf<PageRef>()
    private var dirty = false
    @Volatile private var gen = 0

    private lateinit var listView: ListView
    private lateinit var titleView: TextView
    private val adapter = PagesAdapter()

    private val renderExec = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private val renderers = HashMap<String, Pair<ParcelFileDescriptor, PdfRenderer>>()
    private val thumbs = object : LruCache<String, Bitmap>(24 * 1024 * 1024) {
        override fun sizeOf(key: String, value: Bitmap) = value.byteCount
    }

    private val tmpDir: File get() = File(cacheDir, "edit").apply { mkdirs() }

    private val scanLauncher =
        registerForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { result ->
            if (result.resultCode == RESULT_OK) {
                GmsDocumentScanningResult.fromActivityResultIntent(result.data)?.pdf?.let {
                    appendFromUri(it.uri)
                }
            }
        }

    private val pickPdf =
        registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            if (uri != null) appendFromUri(uri)
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        PdfOps.init(this)
        file = File(intent.getStringExtra(EXTRA_PATH) ?: run { finish(); return })
        setContentView(buildUi())

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (!dirty) { finish(); return }
                AlertDialog.Builder(this@EditorActivity)
                    .setTitle("حفظ التعديلات؟")
                    .setPositiveButton("حفظ") { _, _ -> save(asCopy = false, thenFinish = true) }
                    .setNegativeButton("تجاهل") { _, _ -> finish() }
                    .setNeutralButton("إلغاء", null)
                    .show()
            }
        })

        busy("جارٍ فتح الملف…", { PdfOps.pagesOf(file) }) { list ->
            pages.clear(); pages.addAll(list); refresh()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        renderExec.execute { closeRenderers() }
        renderExec.shutdown()
        tmpDir.listFiles()?.forEach { it.delete() }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    // ---------- UI ----------

    private fun buildUi(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.parseColor("#F4F7F6"))
        }
        val top = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(green)
            setPadding(dp(16), dp(10), dp(8), dp(10))
        }
        titleView = TextView(this).apply {
            textSize = 18f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            maxLines = 1
        }
        top.addView(titleView, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        top.addView(pill("💾 حفظ", Color.WHITE, green) { save(asCopy = false, thenFinish = false) })
        root.addView(top)

        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(dp(8), dp(8), dp(8), dp(8))
        }
        bar.addView(pill("📷 إضافة بالمسح", green, Color.WHITE) { scanMore() })
        bar.addView(pill("📄 إضافة من ملف", green, Color.WHITE) { pickPdf.launch(arrayOf("application/pdf")) })
        bar.addView(pill("📑 حفظ كنسخة", green, Color.WHITE) { save(asCopy = true, thenFinish = false) })
        root.addView(HorizontalScrollView(this).apply {
            isHorizontalScrollBarEnabled = false
            addView(bar)
        })

        listView = ListView(this).apply {
            adapter = this@EditorActivity.adapter
            divider = null
        }
        root.addView(listView, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        return root
    }

    private fun pill(text: String, bg: Int, fg: Int, onClick: () -> Unit) = TextView(this).apply {
        this.text = text
        textSize = 14f
        setTextColor(fg)
        setTypeface(typeface, Typeface.BOLD)
        setPadding(dp(14), dp(8), dp(14), dp(8))
        background = GradientDrawable().apply { cornerRadius = dp(20).toFloat(); setColor(bg) }
        setOnClickListener { onClick() }
        layoutParams = LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ).apply { setMargins(dp(4), 0, dp(4), 0) }
    }

    private fun refresh() {
        titleView.text = "${file.nameWithoutExtension}  (${pages.size} صفحة)" + if (dirty) " •" else ""
        adapter.notifyDataSetChanged()
    }

    private inner class PagesAdapter : BaseAdapter() {
        override fun getCount() = pages.size
        override fun getItem(position: Int) = pages[position]
        override fun getItemId(position: Int) = position.toLong()

        override fun getView(position: Int, convertView: View?, parent: ViewGroup?): View {
            val p = pages[position]
            val card = LinearLayout(this@EditorActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(dp(10), dp(10), dp(10), dp(10))
                background = GradientDrawable().apply { cornerRadius = dp(12).toFloat(); setColor(Color.WHITE) }
            }

            val frame = FrameLayout(this@EditorActivity).apply { setBackgroundColor(Color.parseColor("#E9EEEC")) }
            val img = ImageView(this@EditorActivity).apply {
                scaleType = ImageView.ScaleType.FIT_CENTER
                rotation = p.extraRotation.toFloat()
            }
            frame.addView(img, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
            card.addView(frame, LinearLayout.LayoutParams(dp(110), dp(110)))
            loadThumb(p, img)

            val side = LinearLayout(this@EditorActivity).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(12), 0, 0, 0)
            }
            side.addView(TextView(this@EditorActivity).apply {
                text = "صفحة ${position + 1}"
                textSize = 16f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(Color.BLACK)
            })
            val actions = LinearLayout(this@EditorActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                setPadding(0, dp(8), 0, 0)
            }
            actions.addView(iconBtn("⟳") { p.extraRotation = (p.extraRotation + 90) % 360; changed() })
            actions.addView(iconBtn("▲") { move(position, -1) })
            actions.addView(iconBtn("▼") { move(position, +1) })
            actions.addView(iconBtn("🗑") { deletePage(position) })
            side.addView(actions)
            card.addView(side, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))

            return LinearLayout(this@EditorActivity).apply {
                setPadding(dp(12), dp(5), dp(12), dp(5))
                addView(card, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
            }
        }
    }

    private fun iconBtn(label: String, onClick: () -> Unit) = TextView(this).apply {
        text = label
        textSize = 18f
        gravity = Gravity.CENTER
        setTextColor(green)
        background = GradientDrawable().apply {
            cornerRadius = dp(10).toFloat()
            setColor(Color.parseColor("#E6F1EE"))
        }
        setOnClickListener { onClick() }
        layoutParams = LinearLayout.LayoutParams(dp(44), dp(44)).apply { setMargins(0, 0, dp(8), 0) }
    }

    private fun changed() { dirty = true; refresh() }

    private fun move(pos: Int, delta: Int) {
        val to = pos + delta
        if (to !in pages.indices) return
        val p = pages.removeAt(pos)
        pages.add(to, p)
        changed()
    }

    private fun deletePage(pos: Int) {
        if (pages.size == 1) { toast("لا يمكن حذف آخر صفحة — احذف الملف من القائمة بدلاً من ذلك"); return }
        pages.removeAt(pos)
        changed()
    }

    // ---------- Thumbnails ----------

    private fun loadThumb(p: PageRef, img: ImageView) {
        val key = "$gen:${p.file.path}#${p.index}"
        img.tag = key
        thumbs.get(key)?.let { img.setImageBitmap(it); return }
        img.setImageDrawable(null)
        renderExec.execute {
            val bmp = try { render(p) } catch (e: Exception) { null } ?: return@execute
            thumbs.put(key, bmp)
            main.post { if (img.tag == key) img.setImageBitmap(bmp) }
        }
    }

    private fun render(p: PageRef): Bitmap {
        val r = renderers.getOrPut(p.file.path) {
            val fd = ParcelFileDescriptor.open(p.file, ParcelFileDescriptor.MODE_READ_ONLY)
            fd to PdfRenderer(fd)
        }.second
        r.openPage(p.index).use { page ->
            val w = 320
            val h = (w.toFloat() * page.height / page.width).toInt().coerceIn(1, 1200)
            val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
            bmp.eraseColor(Color.WHITE)
            page.render(bmp, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
            return bmp
        }
    }

    private fun closeRenderers() {
        renderers.values.forEach { (fd, r) -> runCatching { r.close() }; runCatching { fd.close() } }
        renderers.clear()
    }

    // ---------- Adding pages ----------

    private fun scanMore() {
        val options = GmsDocumentScannerOptions.Builder()
            .setGalleryImportAllowed(true)
            .setPageLimit(100)
            .setResultFormats(GmsDocumentScannerOptions.RESULT_FORMAT_PDF)
            .setScannerMode(GmsDocumentScannerOptions.SCANNER_MODE_FULL)
            .build()
        GmsDocumentScanning.getClient(options).getStartScanIntent(this)
            .addOnSuccessListener { scanLauncher.launch(IntentSenderRequest.Builder(it).build()) }
            .addOnFailureListener { toast("تعذّر تشغيل الماسح: ${it.message}") }
    }

    private fun appendFromUri(uri: Uri) {
        busy("جارٍ إضافة الصفحات…", {
            val f = File(tmpDir, "src_${System.nanoTime()}.pdf")
            contentResolver.openInputStream(uri)!!.use { input -> f.outputStream().use { input.copyTo(it) } }
            PdfOps.pagesOf(f)
        }) { added ->
            pages.addAll(added)
            changed()
            listView.post { listView.setSelection(pages.size - 1) }
            toast("تمت إضافة ${added.size} صفحة")
        }
    }

    // ---------- Saving ----------

    private fun save(asCopy: Boolean, thenFinish: Boolean) {
        if (pages.isEmpty()) { toast("لا توجد صفحات"); return }
        if (asCopy) {
            val target = PdfOps.uniqueFile(file.parentFile!!, file.nameWithoutExtension + " - معدّل")
            busy("جارٍ الحفظ…", { PdfOps.build(pages.toList(), target) }) {
                toast("تم الحفظ باسم: ${target.nameWithoutExtension}")
                if (thenFinish) finish()
            }
            return
        }
        busy("جارٍ الحفظ…", {
            PdfOps.build(pages.toList(), file)
            PdfOps.pagesOf(file)
        }) { fresh ->
            // the file changed on disk: reopen thumbnails from the saved file
            gen++
            renderExec.execute { closeRenderers() }
            thumbs.evictAll()
            pages.clear(); pages.addAll(fresh)
            dirty = false
            refresh()
            toast("تم حفظ التعديلات")
            if (thenFinish) finish()
        }
    }

    private fun <T> busy(msg: String, work: () -> T, done: (T) -> Unit) {
        val dialog = AlertDialog.Builder(this).setMessage(msg).setCancelable(false).show()
        Thread {
            val res = runCatching(work)
            main.post {
                dialog.dismiss()
                res.onSuccess(done).onFailure { e ->
                    AlertDialog.Builder(this).setTitle("حدث خطأ")
                        .setMessage(e.message ?: e.toString())
                        .setPositiveButton("حسناً", null).show()
                }
            }
        }.start()
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}
