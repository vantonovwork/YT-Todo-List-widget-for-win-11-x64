package app.ytwidget.todo

import android.annotation.SuppressLint
import android.content.Intent
import android.graphics.Color as AColor
import android.net.Uri
import android.view.ViewGroup
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.viewinterop.AndroidView
import java.io.ByteArrayInputStream
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Описание и комментарии в оформлении самого YouTrack (HTML с сервера) — выглядит как в браузере:
 * заголовки, списки, таблицы, код, цитаты, упоминания, ссылки на задачи, картинки.
 * JavaScript выключен; ссылки открываются в браузере; картинки своего сервера грузятся с токеном.
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun YouTrackHtml(content: RenderedIssue, baseUrl: String, load: (String) -> Pair<String, ByteArray>?, modifier: Modifier = Modifier) {
    val c = MaterialTheme.colorScheme
    val doc = page(content, Palette(c.onSurface, c.onSurfaceVariant, c.primary, c.outlineVariant, c.surfaceVariant, c.surfaceContainer))
    val base = baseUrl.trim().trimEnd('/') + "/"
    AndroidView(
        modifier = modifier,
        factory = { ctx ->
            WebView(ctx).apply {
                layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                setBackgroundColor(AColor.TRANSPARENT)
                isVerticalScrollBarEnabled = false
                settings.javaScriptEnabled = false
                settings.allowFileAccess = false
                settings.allowContentAccess = false
                settings.domStorageEnabled = false
                webViewClient = object : WebViewClient() {
                    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                        val url = request.url.toString()
                        runCatching {
                            view.context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                        }.onFailure { AppLog.warn(view.context, "Ссылка", "не открылась: ${url.take(200)}") }
                        return true
                    }

                    override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                        if (request.method != "GET") return null
                        val (mime, bytes) = load(request.url.toString()) ?: return null
                        return WebResourceResponse(mime, null, ByteArrayInputStream(bytes))
                    }
                }
            }
        },
        update = { wv ->
            if (wv.tag != doc) {
                wv.tag = doc
                wv.loadDataWithBaseURL(base, doc, "text/html", "utf-8", null)
            }
        },
    )
}

private data class Palette(val fg: Color, val muted: Color, val link: Color, val line: Color, val code: Color, val card: Color)

private fun css(c: Color): String = String.format("#%06X", c.toArgb() and 0xFFFFFF)

private fun esc(s: String) = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;")

private fun page(r: RenderedIssue, p: Palette): String {
    val fmt = SimpleDateFormat("dd.MM.yyyy HH:mm", Locale.getDefault())
    val body = buildString {
        append("<h2 class=\"section\">Описание</h2>")
        append(if (r.description.isBlank()) "<p class=\"muted\">Нет описания</p>" else "<div class=\"desc\">${r.description}</div>")
        append("<h2 class=\"section\">")
        append(if (r.comments.isEmpty()) "Комментариев нет" else "Комментарии (${r.comments.size})")
        append("</h2>")
        r.comments.reversed().forEach { cm ->  // новые сверху
            append("<div class=\"comment\"><div class=\"meta\"><b>${esc(cm.author)}</b> &nbsp;${fmt.format(Date(cm.created))}</div>")
            append(cm.html.ifBlank { "<p class=\"muted\">—</p>" })
            append("</div>")
        }
    }
    return """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
html,body{margin:0;padding:0;background:transparent}
body{font-family:sans-serif;font-size:16px;line-height:1.5;color:${css(p.fg)};overflow-wrap:anywhere;word-wrap:break-word}
a{color:${css(p.link)}}
p{margin:0 0 8px}
h1,h2,h3,h4,h5,h6{margin:14px 0 6px;line-height:1.3}
h1{font-size:22px}h2{font-size:19px}h3{font-size:17px}h4,h5,h6{font-size:16px}
h2.section{font-size:14px;font-weight:500;color:${css(p.muted)};margin:20px 0 6px}
.muted{color:${css(p.muted)}}
code,pre,kbd{font-family:monospace;font-size:14px;background:${css(p.code)};border-radius:4px}
code{padding:1px 4px}
pre{padding:10px;overflow-x:auto;white-space:pre}
pre code{padding:0;background:none}
blockquote{margin:6px 0;padding:0 0 0 10px;border-left:3px solid ${css(p.line)};color:${css(p.muted)}}
table{border-collapse:collapse;display:block;overflow-x:auto;max-width:100%;margin:6px 0}
th,td{border:1px solid ${css(p.line)};padding:6px 8px;vertical-align:top;text-align:left}
th{background:${css(p.code)}}
img{max-width:100%;height:auto}
ul,ol{padding-left:22px;margin:4px 0 8px}
li>p{margin:0}
hr{border:none;border-top:1px solid ${css(p.line)};margin:12px 0}
input[type=checkbox]{margin:0 6px 0 0}
.comment{margin:8px 0;padding:12px;border-radius:12px;background:${css(p.card)}}
.comment>:last-child,.desc>:last-child{margin-bottom:0}
.meta{font-size:13px;color:${css(p.muted)};margin-bottom:6px}
.meta b{color:${css(p.fg)}}
</style></head><body>$body</body></html>"""
}
