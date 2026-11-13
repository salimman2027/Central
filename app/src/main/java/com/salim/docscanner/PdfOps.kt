package com.salim.docscanner

import android.content.Context
import com.tom_roush.pdfbox.android.PDFBoxResourceLoader
import com.tom_roush.pdfbox.pdmodel.PDDocument
import java.io.File

/** A page taken from a source PDF, with extra rotation added by the user. */
data class PageRef(val file: File, val index: Int, var extraRotation: Int = 0)

object PdfOps {

    fun init(context: Context) = PDFBoxResourceLoader.init(context.applicationContext)

    fun pageCount(f: File): Int = PDDocument.load(f).use { it.numberOfPages }

    fun pagesOf(f: File): List<PageRef> = (0 until pageCount(f)).map { PageRef(f, it) }

    /** Writes [pages] (in order) into a new PDF at [out]. Safe even if [out] is one of the sources. */
    fun build(pages: List<PageRef>, out: File) {
        val docs = HashMap<String, PDDocument>()
        try {
            buildWith(docs, pages, out)
        } finally {
            docs.values.forEach { runCatching { it.close() } }
        }
    }

    private fun buildWith(docs: MutableMap<String, PDDocument>, pages: List<PageRef>, out: File) {
        require(pages.isNotEmpty()) { "لا توجد صفحات" }
        val tmp = File(out.parentFile, ".${out.name}.tmp")
        val result = PDDocument()
        try {
            for (p in pages) {
                val src = docs.getOrPut(p.file.path) { PDDocument.load(p.file) }
                val sp = src.getPage(p.index)
                val np = result.importPage(sp)
                // copy inherited attributes explicitly so pages keep their size/content
                np.mediaBox = sp.mediaBox
                np.cropBox = sp.cropBox
                np.resources = sp.resources
                np.rotation = (((sp.rotation + p.extraRotation) % 360) + 360) % 360
            }
            result.save(tmp)
        } finally {
            result.close()
        }
        if (out.exists()) out.delete()
        if (!tmp.renameTo(out)) {
            tmp.copyTo(out, overwrite = true)
            tmp.delete()
        }
    }

    fun merge(files: List<File>, out: File) = build(files.flatMap { pagesOf(it) }, out)

    /** Every page into its own file. Returns created files. */
    fun splitEach(f: File, dir: File, base: String): List<File> {
        val docs = HashMap<String, PDDocument>()
        val created = mutableListOf<File>()
        try {
            val n = docs.getOrPut(f.path) { PDDocument.load(f) }.numberOfPages
            for (i in 0 until n) {
                val out = uniqueFile(dir, "${base}_صفحة_${i + 1}")
                buildWith(docs, listOf(PageRef(f, i)), out)
                created += out
            }
        } finally {
            docs.values.forEach { runCatching { it.close() } }
        }
        return created
    }

    /** Splits into chunks of [size] pages. */
    fun splitEvery(f: File, size: Int, dir: File, base: String): List<File> {
        val all = pagesOf(f)
        val docs = HashMap<String, PDDocument>()
        val created = mutableListOf<File>()
        try {
            all.chunked(size).forEachIndexed { i, chunk ->
                val out = uniqueFile(dir, "${base}_جزء_${i + 1}")
                buildWith(docs, chunk, out)
                created += out
            }
        } finally {
            docs.values.forEach { runCatching { it.close() } }
        }
        return created
    }

    /** Parses "1-3, 5, 8-6" into zero-based page indexes (keeps order, allows reverse ranges). */
    fun parseRanges(text: String, pageCount: Int): List<Int> {
        val result = mutableListOf<Int>()
        val cleaned = text.replace('،', ',').replace('–', '-').replace(" ", "")
            .map { c -> if (c in '٠'..'٩') ('0' + (c - '٠')) else c }.joinToString("")
        for (part in cleaned.split(',').filter { it.isNotEmpty() }) {
            val bounds = part.split('-')
            val a = bounds[0].toIntOrNull() ?: throw IllegalArgumentException("صيغة غير صحيحة: $part")
            val b = if (bounds.size > 1) bounds[1].toIntOrNull()
                ?: throw IllegalArgumentException("صيغة غير صحيحة: $part") else a
            if (a !in 1..pageCount || b !in 1..pageCount)
                throw IllegalArgumentException("الملف فيه $pageCount صفحة فقط")
            val range = if (a <= b) a..b else a downTo b
            range.forEach { result += it - 1 }
        }
        if (result.isEmpty()) throw IllegalArgumentException("اكتب أرقام الصفحات")
        return result
    }

    fun uniqueFile(dir: File, base: String): File {
        val safe = base.replace(Regex("[\\\\/:*?\"<>|]"), "_").ifBlank { "مستند" }
        var f = File(dir, "$safe.pdf")
        var n = 2
        while (f.exists()) f = File(dir, "$safe ($n).pdf").also { n++ }
        return f
    }
}
