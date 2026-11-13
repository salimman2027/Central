package com.salim.docscanner

import android.app.AlertDialog
import android.content.ContentValues
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.MediaStore
import android.text.InputType
import android.text.format.Formatter
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.BaseAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.FileProvider
import com.google.mlkit.vision.documentscanner.GmsDocumentScannerOptions
import com.google.mlkit.vision.documentscanner.GmsDocumentScanning
import com.google.mlkit.vision.documentscanner.GmsDocumentScanningResult
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : ComponentActivity() {

    private val green = Color.parseColor("#1E6F5C")
    private lateinit var listView: ListView
    private lateinit var emptyView: TextView
    private val files = mutableListOf<File>()
    private val adapter = FilesAdapter()

    private val scansDir: File
        get() = File(filesDir, "scans").apply { mkdirs() }

    private val scanLauncher =
        registerForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { result ->
            if (result.resultCode == RESULT_OK) {
                val scan = GmsDocumentScanningResult.fromActivityResultIntent(result.data)
                val pdf = scan?.pdf
                if (pdf != null) savePdf(pdf.uri, pdf.pageCount)
                else toast("لم يتم إنشاء ملف")
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(buildUi())
        refresh()
    }

    // ---------- UI ----------

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun buildUi(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.parseColor("#F4F7F6"))
        }

        val header = TextView(this).apply {
            text = getString(R.string.app_name)
            textSize = 22f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            setBackgroundColor(green)
            setPadding(dp(20), dp(18), dp(20), dp(18))
        }
        root.addView(header)

        val scanBtn = Button(this).apply {
            text = "📷  مسح مستند جديد"
            textSize = 18f
            setTextColor(Color.WHITE)
            isAllCaps = false
            background = GradientDrawable().apply {
                cornerRadius = dp(14).toFloat()
                setColor(green)
            }
            setOnClickListener { startScan() }
        }
        root.addView(scanBtn, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(64)
        ).apply { setMargins(dp(16), dp(16), dp(16), dp(8)) })

        val hint = TextView(this).apply {
            text = "اضغط على الملف لفتحه، واضغط مطوّلاً لخيارات أكثر"
            textSize = 13f
            setTextColor(Color.GRAY)
            setPadding(dp(20), 0, dp(20), dp(4))
        }
        root.addView(hint)

        emptyView = TextView(this).apply {
            text = "لا توجد مستندات بعد\nابدأ بمسح أول مستند"
            textSize = 16f
            gravity = Gravity.CENTER
            setTextColor(Color.GRAY)
        }
        listView = ListView(this).apply {
            adapter = this@MainActivity.adapter
            divider = null
            setOnItemClickListener { _, _, pos, _ -> openFile(files[pos]) }
            setOnItemLongClickListener { _, _, pos, _ -> showOptions(files[pos]); true }
        }
        val lp = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f)
        root.addView(emptyView, lp)
        root.addView(listView, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        return root
    }

    private inner class FilesAdapter : BaseAdapter() {
        override fun getCount() = files.size
        override fun getItem(position: Int) = files[position]
        override fun getItemId(position: Int) = position.toLong()

        override fun getView(position: Int, convertView: View?, parent: ViewGroup?): View {
            val f = files[position]
            val card = LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(dp(14), dp(12), dp(14), dp(12))
                background = GradientDrawable().apply {
                    cornerRadius = dp(12).toFloat()
                    setColor(Color.WHITE)
                }
            }
            val icon = TextView(this@MainActivity).apply {
                text = "PDF"
                textSize = 12f
                setTypeface(typeface, Typeface.BOLD)
                setTextColor(Color.WHITE)
                gravity = Gravity.CENTER
                background = GradientDrawable().apply {
                    cornerRadius = dp(8).toFloat()
                    setColor(Color.parseColor("#D9534F"))
                }
            }
            card.addView(icon, LinearLayout.LayoutParams(dp(44), dp(44)))
            val texts = LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(12), 0, dp(12), 0)
            }
            texts.addView(TextView(this@MainActivity).apply {
                text = f.nameWithoutExtension
                textSize = 16f
                setTextColor(Color.BLACK)
                maxLines = 1
            })
            texts.addView(TextView(this@MainActivity).apply {
                val date = SimpleDateFormat("yyyy/MM/dd  HH:mm", Locale.getDefault())
                    .format(Date(f.lastModified()))
                text = "$date  •  ${Formatter.formatShortFileSize(this@MainActivity, f.length())}"
                textSize = 12f
                setTextColor(Color.GRAY)
            })
            card.addView(texts, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))

            return LinearLayout(this@MainActivity).apply {
                setPadding(dp(16), dp(6), dp(16), dp(6))
                addView(card, LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
            }
        }
    }

    private fun refresh() {
        files.clear()
        files.addAll(
            scansDir.listFiles { f -> f.extension.equals("pdf", true) }
                ?.sortedByDescending { it.lastModified() } ?: emptyList()
        )
        adapter.notifyDataSetChanged()
        emptyView.visibility = if (files.isEmpty()) View.VISIBLE else View.GONE
        listView.visibility = if (files.isEmpty()) View.GONE else View.VISIBLE
    }

    // ---------- Scanning ----------

    private fun startScan() {
        val options = GmsDocumentScannerOptions.Builder()
            .setGalleryImportAllowed(true)
            .setPageLimit(100)
            .setResultFormats(GmsDocumentScannerOptions.RESULT_FORMAT_PDF)
            .setScannerMode(GmsDocumentScannerOptions.SCANNER_MODE_FULL)
            .build()
        GmsDocumentScanning.getClient(options)
            .getStartScanIntent(this)
            .addOnSuccessListener { sender ->
                scanLauncher.launch(IntentSenderRequest.Builder(sender).build())
            }
            .addOnFailureListener { e ->
                AlertDialog.Builder(this)
                    .setTitle("تعذّر تشغيل الماسح")
                    .setMessage("تأكد من وجود خدمات Google Play وتحديثها، ومن اتصالك بالإنترنت أول مرة.\n\n${e.message}")
                    .setPositiveButton("حسناً", null)
                    .show()
            }
    }

    private fun savePdf(src: Uri, pages: Int) {
        val stamp = SimpleDateFormat("yyyy-MM-dd_HH-mm-ss", Locale.US).format(Date())
        val out = File(scansDir, "مستند_$stamp.pdf")
        try {
            contentResolver.openInputStream(src)!!.use { input ->
                out.outputStream().use { input.copyTo(it) }
            }
            val savedToDownloads = copyToDownloads(out)
            refresh()
            toast(
                "تم حفظ المستند ($pages صفحة)" +
                    if (savedToDownloads) "\nونسخة في التنزيلات/DocScanner" else ""
            )
        } catch (e: Exception) {
            toast("فشل الحفظ: ${e.message}")
        }
    }

    // ---------- File actions ----------

    private fun uriFor(f: File): Uri =
        FileProvider.getUriForFile(this, "$packageName.files", f)

    private fun openFile(f: File) {
        val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uriFor(f), "application/pdf")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        try {
            startActivity(intent)
        } catch (e: Exception) {
            toast("لا يوجد تطبيق لعرض ملفات PDF")
        }
    }

    private fun shareFile(f: File) {
        val intent = Intent(Intent.ACTION_SEND)
            .setType("application/pdf")
            .putExtra(Intent.EXTRA_STREAM, uriFor(f))
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        startActivity(Intent.createChooser(intent, "مشاركة المستند"))
    }

    private fun copyToDownloads(f: File): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return false
        return try {
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, f.name)
                put(MediaStore.Downloads.MIME_TYPE, "application/pdf")
                put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/DocScanner")
            }
            val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                ?: return false
            contentResolver.openOutputStream(uri)!!.use { out ->
                f.inputStream().use { it.copyTo(out) }
            }
            true
        } catch (e: Exception) {
            false
        }
    }

    private fun showOptions(f: File) {
        val items = arrayOf("فتح", "مشاركة", "حفظ نسخة في التنزيلات", "إعادة تسمية", "حذف")
        AlertDialog.Builder(this)
            .setTitle(f.nameWithoutExtension)
            .setItems(items) { _, which ->
                when (which) {
                    0 -> openFile(f)
                    1 -> shareFile(f)
                    2 -> toast(
                        if (copyToDownloads(f)) "تم الحفظ في التنزيلات/DocScanner"
                        else "غير مدعوم على هذا الإصدار، استخدم المشاركة"
                    )
                    3 -> rename(f)
                    4 -> confirmDelete(f)
                }
            }
            .show()
    }

    private fun rename(f: File) {
        val input = EditText(this).apply {
            setText(f.nameWithoutExtension)
            inputType = InputType.TYPE_CLASS_TEXT
            setSelectAllOnFocus(true)
        }
        val box = LinearLayout(this).apply {
            setPadding(dp(20), dp(8), dp(20), 0)
            addView(input, LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        AlertDialog.Builder(this)
            .setTitle("إعادة تسمية")
            .setView(box)
            .setPositiveButton("حفظ") { _, _ ->
                val name = input.text.toString().trim().replace(Regex("[\\\\/:*?\"<>|]"), "_")
                if (name.isEmpty()) return@setPositiveButton
                val target = File(scansDir, "$name.pdf")
                if (target.exists()) toast("يوجد ملف بنفس الاسم")
                else if (f.renameTo(target)) refresh()
                else toast("تعذّرت إعادة التسمية")
            }
            .setNegativeButton("إلغاء", null)
            .show()
    }

    private fun confirmDelete(f: File) {
        AlertDialog.Builder(this)
            .setTitle("حذف المستند؟")
            .setMessage(f.nameWithoutExtension)
            .setPositiveButton("حذف") { _, _ -> f.delete(); refresh() }
            .setNegativeButton("إلغاء", null)
            .show()
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}
