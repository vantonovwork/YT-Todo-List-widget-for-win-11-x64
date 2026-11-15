package app.ytwidget.todo

/**
 * Разбор Markdown YouTrack (описания и комментарии) без сторонних библиотек.
 * Блоки: заголовки, абзацы, списки (вложенные, чекбоксы), цитаты, блоки кода, таблицы, разделители.
 * Внутри строки: **жирный**, *курсив*, ~~зачёркнутый~~, `код`, [ссылки](url), автоссылки, картинки (как подпись).
 */
sealed class MdBlock {
    data class Heading(val level: Int, val text: List<MdSpan>) : MdBlock()
    data class Paragraph(val text: List<MdSpan>) : MdBlock()
    data class Code(val lang: String, val text: String) : MdBlock()
    data class Quote(val blocks: List<MdBlock>) : MdBlock()
    data class ListItem(val text: List<MdSpan>, val depth: Int, val marker: String, val checked: Boolean?)
    data class Bullets(val items: List<ListItem>) : MdBlock()
    data class Table(val header: List<List<MdSpan>>, val align: List<MdAlign>, val rows: List<List<List<MdSpan>>>) : MdBlock()
    data object Rule : MdBlock()
}

enum class MdAlign { START, CENTER, END }

/** Кусок текста с оформлением. url — ссылка; image — картинка (показываем подписью). */
data class MdSpan(
    val text: String,
    val bold: Boolean = false,
    val italic: Boolean = false,
    val strike: Boolean = false,
    val code: Boolean = false,
    val url: String? = null,
    val image: Boolean = false,
)

object Markdown {
    private val FENCE = Regex("""^\s*(```|~~~)\s*([\w+#.-]*).*$""")
    private val HEADING = Regex("""^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$""")
    private val LIST = Regex("""^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$""")
    private val TASK = Regex("""^\[([ xX])]\s+(.*)$""")
    private val QUOTE = Regex("""^\s{0,3}>\s?(.*)$""")
    private val BR = Regex("""<br\s*/?>""", RegexOption.IGNORE_CASE)
    private val TAG = Regex("""</?[a-zA-Z][a-zA-Z0-9-]*(\s[^<>]*)?/?>""")

    fun parse(text: String?): List<MdBlock> {
        if (text.isNullOrBlank()) return emptyList()
        val src = text.replace("\r\n", "\n").replace('\r', '\n')
        return blocks(src.split("\n"))
    }

    // Разделитель и строку-разделитель таблицы проверяем вручную: регулярки с повтором групп
    // вида (\s*\1){2,} на длинной строке «_____…» переполняют стек и роняют приложение.
    /** --- *** ___ (можно с пробелами), не меньше трёх одинаковых знаков. */
    private fun isRule(line: String): Boolean {
        val t = line.filter { !it.isWhitespace() }
        return t.length >= 3 && t[0] in "-*_" && t.all { it == t[0] } && line.length - line.trimStart().length <= 3
    }

    /** |---|:---:| — только «|», «-», «:» и пробелы, хотя бы одно «-». */
    private fun isTableSep(line: String) =
        '-' in line && line.all { it == '|' || it == '-' || it == ':' || it == ' ' || it == '\t' }

    private fun isTableStart(lines: List<String>, i: Int) =
        i + 1 < lines.size && '|' in lines[i] && isTableSep(lines[i + 1])

    private fun startsBlock(lines: List<String>, i: Int): Boolean {
        val l = lines[i]
        return FENCE.matches(l) || HEADING.matches(l) || isRule(l) || QUOTE.matches(l) || LIST.matches(l) || isTableStart(lines, i)
    }

    private fun blocks(lines: List<String>): List<MdBlock> {
        val out = mutableListOf<MdBlock>()
        var i = 0
        while (i < lines.size) {
            val line = lines[i]
            when {
                line.isBlank() -> i++
                FENCE.matches(line) -> {
                    val m = FENCE.find(line)!!
                    val fence = m.groupValues[1]
                    val body = mutableListOf<String>()
                    i++
                    while (i < lines.size && !lines[i].trim().startsWith(fence)) body += lines[i++]
                    i++  // закрывающая ``` (или конец текста)
                    out += MdBlock.Code(m.groupValues[2], body.joinToString("\n").trimEnd())
                }
                HEADING.matches(line) -> {
                    val m = HEADING.find(line)!!
                    out += MdBlock.Heading(m.groupValues[1].length, inline(m.groupValues[2]))
                    i++
                }
                isRule(line) ->{ out += MdBlock.Rule; i++ }
                isTableStart(lines, i) -> {
                    val header = cells(line)
                    val align = cells(lines[i + 1]).map {
                        val c = it.trim()
                        when {
                            c.startsWith(":") && c.endsWith(":") -> MdAlign.CENTER
                            c.endsWith(":") -> MdAlign.END
                            else -> MdAlign.START
                        }
                    }
                    i += 2
                    val rows = mutableListOf<List<String>>()
                    while (i < lines.size && lines[i].isNotBlank() && '|' in lines[i]) rows += cells(lines[i++])
                    val n = maxOf(header.size, rows.maxOfOrNull { it.size } ?: 0)
                    fun pad(r: List<String>) = (r + List(maxOf(0, n - r.size)) { "" }).map { inline(it.trim()) }
                    out += MdBlock.Table(pad(header), (align + List(maxOf(0, n - align.size)) { MdAlign.START }).take(n), rows.map(::pad))
                }
                QUOTE.matches(line) -> {
                    val body = mutableListOf<String>()
                    while (i < lines.size && QUOTE.matches(lines[i])) body += QUOTE.find(lines[i++])!!.groupValues[1]
                    out += MdBlock.Quote(blocks(body))
                }
                LIST.matches(line) -> {
                    val items = mutableListOf<MdBlock.ListItem>()
                    val base = LIST.find(line)!!.groupValues[1].replace("\t", "    ").length
                    while (i < lines.size) {
                        val l = lines[i]
                        val m = LIST.find(l)
                        if (m != null) {
                            val indent = m.groupValues[1].replace("\t", "    ").length
                            var body = m.groupValues[3]
                            var checked: Boolean? = null
                            TASK.find(body)?.let { checked = it.groupValues[1] != " "; body = it.groupValues[2] }
                            val marker = m.groupValues[2].let { if (it[0].isDigit()) it.dropLast(1) + "." else "•" }
                            items += MdBlock.ListItem(inline(body), maxOf(0, (indent - base) / 2).coerceAtMost(4), marker, checked)
                            i++
                        } else if (l.isNotBlank() && l.startsWith(" ") && items.isNotEmpty() && !startsBlock(lines, i)) {
                            // продолжение пункта на следующей строке
                            val last = items.removeAt(items.lastIndex)
                            items += last.copy(text = last.text + MdSpan("\n") + inline(l.trim()))
                            i++
                        } else break
                    }
                    out += MdBlock.Bullets(items)
                }
                else -> {
                    val body = mutableListOf(line)
                    i++
                    while (i < lines.size && lines[i].isNotBlank() && !startsBlock(lines, i)) body += lines[i++]
                    // YouTrack показывает перенос строки как перенос — сохраняем
                    out += MdBlock.Paragraph(inline(body.joinToString("\n") { it.trim() }))
                }
            }
        }
        return out
    }

    /** Ячейки строки таблицы: | a | b | → [a, b]; \| внутри ячейки — обычный символ. */
    private fun cells(line: String): List<String> {
        var s = line.trim()
        if (s.startsWith("|")) s = s.substring(1)
        if (s.endsWith("|") && !s.endsWith("\\|")) s = s.dropLast(1)
        val out = mutableListOf<String>()
        val cur = StringBuilder()
        var k = 0
        while (k < s.length) {
            val ch = s[k]
            if (ch == '\\' && k + 1 < s.length && s[k + 1] == '|') { cur.append('|'); k += 2; continue }
            if (ch == '|') { out += cur.toString(); cur.clear() } else cur.append(ch)
            k++
        }
        out += cur.toString()
        return out
    }

    // ---------------------------------------------------------------- внутри строки
    private data class Style(val bold: Boolean = false, val italic: Boolean = false, val strike: Boolean = false, val url: String? = null)

    // Без вложенных повторов групп: на длинном тексте такие регулярки переполняют стек.
    // Сопоставляем с позиции i (region + lookingAt), не копируя хвост строки.
    private val TARGET = Regex("""\(\s*<?([^)\s>]*)>?(?:\s+"[^"\n]*")?\s*\)(\{[^}\n]*})?""").toPattern()
    private val AUTOLINK = Regex("""<?(https?://[^\s<>]+?)>?(?=[\s,;!?)]|[.:](?:\s|$)|$)""").toPattern()

    private class Hit(val text: String, val url: String, val end: Int)

    /** [текст](адрес) или ![подпись](адрес) с позиции i (на «[»): ищем парную «]» вручную, вложенность до одного уровня. */
    private fun bracket(s: String, i: Int): Hit? {
        var depth = 0
        var k = i
        while (k < s.length && k - i < 2000) {  // длинные ссылки не ищем — без квадратичного перебора
            when (s[k]) {
                '\\' -> k++
                '[' -> depth++
                ']' -> { depth--; if (depth == 0) break }
                '\n' -> if (k + 1 < s.length && s[k + 1] == '\n') return null  // ссылка не переходит через абзац
            }
            k++
        }
        if (k + 1 >= s.length || s[k] != ']' || s[k + 1] != '(') return null
        val m = TARGET.matcher(s).region(k + 1, s.length)
        if (!m.lookingAt()) return null
        return Hit(s.substring(i + 1, k), m.group(1).orEmpty(), m.end())
    }

    private fun autolink(s: String, i: Int): Hit? {
        val m = AUTOLINK.matcher(s).region(i, s.length)
        if (!m.lookingAt()) return null
        val u = m.group(1).orEmpty()
        return Hit(u, u, m.end())
    }

    fun inline(text: String): List<MdSpan> {
        val clean = TAG.replace(BR.replace(text, "\n"), "")
            .replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&amp;", "&")
        val out = mutableListOf<MdSpan>()
        spans(clean, Style(), out)
        return merge(out)
    }

    private fun spans(s: String, st: Style, out: MutableList<MdSpan>) {
        val buf = StringBuilder()
        fun flush() {
            if (buf.isNotEmpty()) out += MdSpan(buf.toString(), st.bold, st.italic, st.strike, url = st.url)
            buf.clear()
        }
        var i = 0
        while (i < s.length) {
            val ch = s[i]
            when {
                ch == '\\' && i + 1 < s.length && s[i + 1] in "\\`*_{}[]()#+-.!|~<>" -> { buf.append(s[i + 1]); i += 2 }
                ch == '`' -> {
                    var nt = 0
                    while (i + nt < s.length && s[i + nt] == '`') nt++
                    val ticks = "`".repeat(nt)
                    val end = s.indexOf(ticks, i + ticks.length)
                    if (end < 0) { buf.append(ticks); i += ticks.length }
                    else {
                        flush()
                        out += MdSpan(s.substring(i + ticks.length, end).trim(), st.bold, st.italic, st.strike, code = true, url = st.url)
                        i = end + ticks.length
                    }
                }
                ch == '!' && s.startsWith("[", i + 1) && bracket(s, i + 1) != null -> {
                    val h = bracket(s, i + 1)!!
                    flush()
                    val alt = h.text.ifBlank { h.url.substringAfterLast('/').ifBlank { "картинка" } }
                    out += MdSpan(alt, st.bold, st.italic, st.strike, url = h.url.ifBlank { null }, image = true)
                    i = h.end
                }
                ch == '[' && st.url == null && bracket(s, i) != null -> {
                    val h = bracket(s, i)!!
                    flush()
                    spans(h.text.ifEmpty { h.url }, st.copy(url = h.url.ifBlank { null }), out)
                    i = h.end
                }
                (ch == 'h' || ch == '<') && st.url == null && (i == 0 || !s[i - 1].isLetterOrDigit()) && autolink(s, i) != null -> {
                    val h = autolink(s, i)!!
                    flush()
                    out += MdSpan(h.text, st.bold, st.italic, st.strike, url = h.url)
                    i = h.end
                }
                ch == '~' && s.startsWith("~~", i) -> i = emphasis(s, i, "~~", st.copy(strike = true), out, buf, ::flush)
                (ch == '*' || ch == '_') && i + 1 < s.length && s[i + 1] == ch ->
                    i = emphasis(s, i, "$ch$ch", st.copy(bold = true), out, buf, ::flush)
                ch == '*' || ch == '_' -> i = emphasis(s, i, ch.toString(), st.copy(italic = true), out, buf, ::flush)
                else -> { buf.append(ch); i++ }
            }
        }
        flush()
    }

    /** Пара маркеров (**, _, ~~): содержимое — рекурсивно с новым стилем; без пары — обычный текст. */
    private fun emphasis(s: String, i: Int, mark: String, inner: Style, out: MutableList<MdSpan>, buf: StringBuilder,
                         flush: () -> Unit): Int {
        val start = i + mark.length
        val wordMark = mark[0] == '_'
        // открывающий маркер: за ним не пробел; _ внутри слова (snake_case) — не разметка
        val opens = start < s.length && !s[start].isWhitespace() && !(wordMark && i > 0 && s[i - 1].isLetterOrDigit())
        var end = if (opens) s.indexOf(mark, start) else -1
        while (end > 0) {
            val closes = !s[end - 1].isWhitespace() &&
                !(wordMark && end + mark.length < s.length && s[end + mark.length].isLetterOrDigit()) &&
                !(mark.length == 1 && ((end + 1 < s.length && s[end + 1] == mark[0]) || s[end - 1] == mark[0]))  // * — не часть **
            if (closes && end > start) break
            end = s.indexOf(mark, end + 1)
        }
        if (end < 0) { buf.append(mark); return start }
        flush()
        spans(s.substring(start, end), inner, out)
        return end + mark.length
    }

    /** Склеить соседние куски с одинаковым оформлением. */
    private fun merge(spans: List<MdSpan>): List<MdSpan> {
        val out = mutableListOf<MdSpan>()
        for (sp in spans) {
            if (sp.text.isEmpty()) continue
            val last = out.lastOrNull()
            if (last != null && !sp.image && !last.image && last.copy(text = "") == sp.copy(text = "")) out[out.lastIndex] = last.copy(text = last.text + sp.text)
            else out += sp
        }
        return out
    }

    /** Абсолютная ссылка: относительные (/issue/OE-1) — от адреса YouTrack. */
    fun absoluteUrl(url: String, baseUrl: String): String = when {
        url.startsWith("http://") || url.startsWith("https://") || url.startsWith("mailto:") -> url
        url.startsWith("/") -> baseUrl.trim().trimEnd('/') + url
        else -> url
    }
}
