package app.ytwidget.todo

import android.app.Application
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.SystemClock
import android.util.Log
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Приложение: журнал ошибок подключается до любого экрана, фоновой проверки и виджета. */
class YtApp : Application() {
    override fun onCreate() {
        super.onCreate()
        AppLog.install(this)
    }
}

/**
 * Журнал ошибок на устройстве (files/errors.log, не больше 256 КБ — старое отрезается).
 * Падения перехватываются: трасса записывается, при следующем запуске предлагаем отправить отчёт.
 * Токен и адрес YouTrack в журнал не пишем.
 */
object AppLog {
    private const val TAG = "YtWidget"
    private const val FILE = "errors.log"
    private const val MAX_BYTES = 256 * 1024
    private const val PREFS = "ytw"
    const val KEY_CRASH = "crashPending"
    const val KEY_MD_AUTO_OFF = "markdownAutoOff"
    const val KEY_MARKDOWN = "markdown"
    /** Когда последний раз отрисовали текст с разметкой (uptime, мс) — падение сразу после этого списываем на разметку. */
    @Volatile var markdownShown = 0L

    fun install(app: Context) {
        val prev = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { t, e ->
            runCatching {
                write(app, "CRASH", "поток ${t.name}", e)
                val ed = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY_CRASH, summary(e))
                // упали при отрисовке разметки — дальше показываем текст без оформления, чтобы задачи открывались
                val byMarkdown = e.stackTraceToString().contains("Markdown") ||
                    (markdownShown > 0 && SystemClock.uptimeMillis() - markdownShown < 5_000)
                if (byMarkdown) ed.putBoolean(KEY_MARKDOWN, false).putBoolean(KEY_MD_AUTO_OFF, true)
                ed.commit()  // синхронно: процесс сейчас завершится
            }
            prev?.uncaughtException(t, e)
        }
    }

    fun error(ctx: Context, where: String, e: Throwable) = write(ctx, "ERROR", where, e)
    fun warn(ctx: Context, where: String, message: String) = write(ctx, "WARN", where, null, message)

    private fun summary(e: Throwable): String {
        var root = e
        while (root.cause != null && root.cause !== root) root = root.cause!!
        return "${root.javaClass.simpleName}: ${root.message.orEmpty()}".take(300)
    }

    @Synchronized
    private fun write(ctx: Context, level: String, where: String, e: Throwable?, message: String? = null) {
        if (e != null) Log.e(TAG, where, e) else Log.w(TAG, "$where: $message")
        runCatching {
            val f = File(ctx.applicationContext.filesDir, FILE)
            val time = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())
            val entry = buildString {
                append("=== $time $level · $where · ${version(ctx)}\n")
                if (message != null) append(message).append('\n')
                if (e != null) append(e.stackTraceToString().lineSequence().take(60).joinToString("\n")).append('\n')
            }
            f.appendText(entry)
            if (f.length() > MAX_BYTES) {
                val text = f.readText()
                val cut = text.indexOf("\n=== ", text.length / 2)
                f.writeText(if (cut > 0) text.substring(cut + 1) else text.takeLast(MAX_BYTES / 2))
            }
        }
    }

    fun read(ctx: Context): String = runCatching { File(ctx.filesDir, FILE).readText() }.getOrDefault("")

    /** Записи новыми сверху (для экрана журнала). */
    fun entries(ctx: Context): List<String> =
        read(ctx).split(Regex("(?m)^(?==== )")).map { it.trim() }.filter { it.isNotEmpty() }.reversed()

    fun clear(ctx: Context) {
        runCatching { File(ctx.filesDir, FILE).delete() }
    }

    fun version(ctx: Context): String = runCatching {
        val p = ctx.packageManager.getPackageInfo(ctx.packageName, 0)
        val code = if (Build.VERSION.SDK_INT >= 28) p.longVersionCode else @Suppress("DEPRECATION") p.versionCode.toLong()
        "${p.versionName} ($code)"
    }.getOrDefault("?")

    fun header(ctx: Context) = "YouTrack задачи ${version(ctx)}\n" +
        "Устройство: ${Build.MANUFACTURER} ${Build.MODEL}, Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})\n"

    /** Отправить журнал (Telegram, почта и т.п. — выбирает пользователь). Последние ~60 КБ — ограничение Android на размер. */
    fun share(ctx: Context) {
        val body = header(ctx) + "\n" + read(ctx).ifEmpty { "Журнал пуст" }.takeLast(60_000)
        val send = Intent(Intent.ACTION_SEND).setType("text/plain")
            .putExtra(Intent.EXTRA_SUBJECT, "YouTrack задачи — журнал ошибок ${version(ctx)}")
            .putExtra(Intent.EXTRA_TEXT, body)
        ctx.startActivity(Intent.createChooser(send, "Отправить журнал").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    /** Отчёт о падении, ожидающий показа (текст ошибки), или null. */
    fun pendingCrash(ctx: Context): String? =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_CRASH, null)

    /** Падение выключило оформление разметки — сказать об этом в окне отчёта. */
    fun markdownAutoOff(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(KEY_MD_AUTO_OFF, false)

    fun clearPendingCrash(ctx: Context) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(KEY_CRASH).remove(KEY_MD_AUTO_OFF).apply()
    }
}
