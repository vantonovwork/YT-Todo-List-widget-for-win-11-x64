package app.ytwidget.todo

import android.content.Intent
import android.net.Uri
import android.os.SystemClock
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.dp

/**
 * Текст с разметкой Markdown (описание, комментарии). maxBlocks — для краткого просмотра:
 * показываем первые блоки и «…».
 */
@Composable
fun MarkdownText(text: String, baseUrl: String, style: TextStyle, modifier: Modifier = Modifier, maxBlocks: Int = Int.MAX_VALUE,
                 rich: Boolean = true) {
    val ctx = LocalContext.current
    // ошибка разбора не роняет экран: пишем в журнал и показываем текст без оформления
    val all = remember(text, rich) {
        if (!rich) null
        else try {
            Markdown.parse(text)
        } catch (e: Throwable) {
            AppLog.error(ctx, "Разбор разметки (${text.length} симв.)", e)
            null
        }
    }
    if (all == null) {
        val plain = Model.cleanMarkdown(text, if (maxBlocks == Int.MAX_VALUE) Int.MAX_VALUE else 700)
        Text(plain.ifEmpty { "—" }, style = style, modifier = modifier)
        return
    }
    SideEffect { AppLog.markdownShown = SystemClock.uptimeMillis() }
    if (all.isEmpty()) {
        Text("—", style = style, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = modifier)
        return
    }
    Column(modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Blocks(all.take(maxBlocks), baseUrl, style)
        if (all.size > maxBlocks) Text("…", style = style, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun Blocks(blocks: List<MdBlock>, baseUrl: String, style: TextStyle) {
    val t = MaterialTheme.typography
    for (b in blocks) when (b) {
        is MdBlock.Heading -> Text(annotated(b.text, baseUrl), fontWeight = FontWeight.Bold,
            style = when (b.level) { 1 -> t.titleLarge; 2 -> t.titleMedium; else -> t.titleSmall },
            modifier = Modifier.padding(top = 4.dp))
        is MdBlock.Paragraph -> Text(annotated(b.text, baseUrl), style = style)
        is MdBlock.Code -> Box(Modifier.fillMaxWidth().background(codeBg(), RoundedCornerShape(6.dp))
            .horizontalScroll(rememberScrollState()).padding(10.dp)) {
            Text(b.text, style = style.copy(fontFamily = FontFamily.Monospace), softWrap = false)
        }
        is MdBlock.Quote -> Row(Modifier.height(IntrinsicSize.Min)) {
            Box(Modifier.width(3.dp).fillMaxHeight().background(MaterialTheme.colorScheme.outlineVariant))
            Column(Modifier.padding(start = 10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Blocks(b.blocks, baseUrl, style.copy(color = MaterialTheme.colorScheme.onSurfaceVariant))
            }
        }
        is MdBlock.Bullets -> Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
            b.items.forEach { item ->
                Row(Modifier.padding(start = (item.depth * 18).dp)) {
                    val mark = when (item.checked) { true -> "☑"; false -> "☐"; null -> if (item.marker == "•") listOf("•", "◦", "▪")[item.depth % 3] else item.marker }
                    Text(mark, style = style, modifier = Modifier.widthIn(min = 22.dp).padding(end = 6.dp))
                    Text(annotated(item.text, baseUrl), style = style,
                        textDecoration = if (item.checked == true) TextDecoration.LineThrough else null)
                }
            }
        }
        is MdBlock.Table -> TableView(b, baseUrl, style)
        MdBlock.Rule -> HorizontalDivider(Modifier.padding(vertical = 4.dp))
    }
}

@Composable
private fun codeBg() = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.7f)

@Composable
private fun annotated(spans: List<MdSpan>, baseUrl: String): AnnotatedString {
    val ctx = LocalContext.current
    val link = MaterialTheme.colorScheme.primary
    val bg = codeBg()
    return buildAnnotatedString {
        for (sp in spans) {
            val deco = listOfNotNull(if (sp.strike) TextDecoration.LineThrough else null, if (sp.url != null) TextDecoration.Underline else null)
            val style = SpanStyle(
                fontWeight = if (sp.bold) FontWeight.Bold else null,
                fontStyle = if (sp.italic) FontStyle.Italic else null,
                fontFamily = if (sp.code) FontFamily.Monospace else null,
                background = if (sp.code) bg else Color.Unspecified,
                color = if (sp.url != null) link else Color.Unspecified,
                textDecoration = if (deco.isEmpty()) null else TextDecoration.combine(deco),
            )
            val text = if (sp.image) "🖼 ${sp.text}" else sp.text
            val url = sp.url
            if (url != null) {
                val abs = Markdown.absoluteUrl(url, baseUrl)
                // неоткрываемая ссылка (относительный путь вложения и т.п.) не должна ронять приложение
                withLink(LinkAnnotation.Url(abs, TextLinkStyles(style)) {
                    runCatching {
                        ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(abs)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    }.onFailure { AppLog.warn(ctx, "Ссылка", "не открылась: ${abs.take(200)} — ${it.javaClass.simpleName}") }
                }) { append(text) }
            }
            else withStyle(style) { append(text) }
        }
    }
}

/** Таблица: столбцы по ширине содержимого (не шире 280 dp), при нехватке места — прокрутка вбок. */
@Composable
private fun TableView(t: MdBlock.Table, baseUrl: String, style: TextStyle) {
    val cols = t.header.size
    if (cols == 0) return
    val line = MaterialTheme.colorScheme.outlineVariant
    val headBg = MaterialTheme.colorScheme.surfaceVariant
    val rows = listOf(t.header) + t.rows
    Box(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())) {
        Layout(
            content = {
                rows.forEachIndexed { r, row ->
                    row.forEachIndexed { c, cell ->
                        Box(Modifier.border(0.5.dp, line).background(if (r == 0) headBg else Color.Transparent)
                            .padding(horizontal = 8.dp, vertical = 6.dp),
                            contentAlignment = Alignment.TopStart) {
                            Text(annotated(cell, baseUrl), style = style,
                                fontWeight = if (r == 0) FontWeight.Bold else null,
                                textAlign = when (t.align.getOrNull(c)) { MdAlign.CENTER -> TextAlign.Center; MdAlign.END -> TextAlign.End; else -> TextAlign.Start },
                                modifier = Modifier.fillMaxWidth())
                        }
                    }
                }
            },
            modifier = Modifier.border(1.dp, line),
        ) { ms, _ ->
            val maxCell = 280.dp.roundToPx()
            val minCell = 48.dp.roundToPx()
            val widths = IntArray(cols) { minCell }
            ms.forEachIndexed { i, m -> widths[i % cols] = maxOf(widths[i % cols], minOf(m.maxIntrinsicWidth(Constraints.Infinity), maxCell)) }
            val nRows = ms.size / cols
            val heights = IntArray(nRows)
            ms.forEachIndexed { i, m -> heights[i / cols] = maxOf(heights[i / cols], m.minIntrinsicHeight(widths[i % cols])) }
            val ps = ms.mapIndexed { i, m -> m.measure(Constraints.fixed(widths[i % cols], heights[i / cols])) }
            layout(widths.sum(), heights.sum()) {
                var y = 0
                for (r in 0 until nRows) {
                    var x = 0
                    for (c in 0 until cols) {
                        ps[r * cols + c].place(x, y)
                        x += widths[c]
                    }
                    y += heights[r]
                }
            }
        }
    }
}
