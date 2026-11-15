package app.ytwidget.todo

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.glance.appwidget.updateAll
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.util.concurrent.TimeUnit

/** Загрузка задач — общая для приложения, фоновой проверки и виджета на главном экране. */
object Sync {
    private val lock = Mutex()

    suspend fun run(context: Context, notify: Boolean = true): Result<List<Issue>> = lock.withLock {
        withContext(Dispatchers.IO) {
            val store = Store(context)
            val cfg = store.config
            val token = store.token
            if (cfg.baseUrl.isBlank() || token.isBlank()) return@withContext Result.failure(YTException("Укажите адрес YouTrack и токен"))
            try {
                val api = YouTrackApi(cfg.baseUrl, token)
                if (store.me.isEmpty()) store.me = runCatching { api.me() }.getOrDefault("")
                val raw = api.issuesJson(cfg.query, cfg.maxIssues)
                val issues = Model.parseIssues(raw, cfg.fieldNames)
                val seen = store.seen
                val (events, state) = Model.computeEvents(issues, store.notifyState, seen, store.me)
                // задачи, которые видим впервые: уже существующие комментарии считаем прочитанными
                val newSeen = seen.toMutableMap()
                issues.forEach { if (it.idReadable !in newSeen) newSeen[it.idReadable] = it.lastCommentTime }
                // «Нужно внимание»: задачи с упоминаниями (ошибка здесь не мешает основному списку)
                var mentionEvents: List<Event> = emptyList()
                val mq = cfg.mentionsQuery.trim()
                if (mq.isEmpty()) store.resetMentions()
                else runCatching { api.issuesJson(mq, 100) }.onSuccess { mraw ->
                    val mentions = Model.parseIssues(mraw, cfg.fieldNames)
                    val (mev, mseen) = Model.mentionEvents(mentions, issues.map { it.idReadable }.toSet(), store.mentionSeen, store.me)
                    mentions.forEach { if (it.idReadable !in newSeen) newSeen[it.idReadable] = it.lastCommentTime }
                    store.mentionSeen = mseen
                    store.mentionsJson = mraw
                    mentionEvents = mev
                }
                store.seen = newSeen
                store.notifyState = state
                store.issuesJson = raw
                store.lastOk = System.currentTimeMillis()
                store.lastError = null
                if (notify && cfg.notify) Notifier.show(context, events + mentionEvents, cfg.baseUrl, store.me)
                runCatching { TasksWidget().updateAll(context) }
                Result.success(issues)
            } catch (e: YTException) {
                store.lastError = e.message
                Result.failure(e)
            } catch (e: Exception) {
                store.lastError = "Ошибка: ${e.message}"
                Result.failure(e)
            }
        }
    }

    /** Фоновая проверка раз в 15 минут (минимум Android); только при наличии сети. */
    fun schedule(context: Context) {
        val req = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork("ytw-sync", ExistingPeriodicWorkPolicy.KEEP, req)
    }

    fun refreshNow(context: Context) {
        WorkManager.getInstance(context).enqueue(OneTimeWorkRequestBuilder<SyncWorker>().build())
    }
}

class SyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        Sync.run(applicationContext)
        return Result.success()  // ошибки сети не повод для повторов — следующая проверка через 15 минут
    }
}

/** Уведомления о новых задачах и комментариях; нажатие открывает задачу в браузере. */
object Notifier {
    private const val CHANNEL = "events"

    fun issueUrl(baseUrl: String, issue: Issue) = baseUrl.trim().trimEnd('/') + "/issue/" + issue.idReadable

    private fun channel(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Новые задачи, комментарии и упоминания", NotificationManager.IMPORTANCE_DEFAULT))
        }
    }

    private fun allowed(context: Context): Boolean =
        Build.VERSION.SDK_INT < 33 ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    fun show(context: Context, events: List<Event>, baseUrl: String, me: String = "") {
        if (events.isEmpty() || !allowed(context)) return
        channel(context)
        if (events.size > 3) {  // много событий сразу — одно сводное уведомление
            val nNew = events.count { it is Event.NewIssue }
            val nCom = events.filterIsInstance<Event.NewComments>().sumOf { it.comments.size }
            val nMen = events.count { it is Event.Mentioned }
            val parts = listOfNotNull(if (nNew > 0) "новых задач: $nNew" else null, if (nCom > 0) "новых комментариев: $nCom" else null,
                if (nMen > 0) "упоминаний: $nMen" else null)
            post(context, 1, "YouTrack: " + parts.joinToString(", "),
                events.take(6).joinToString(", ") { ev -> issueOf(ev).idReadable } + if (events.size > 6) "…" else "",
                baseUrl.trim().trimEnd('/') + "/issues")
            return
        }
        for (ev in events) {
            val issue = issueOf(ev)
            when (ev) {
                is Event.NewIssue -> post(context, issue.idReadable.hashCode(), "Новая задача · ${issue.idReadable}",
                    issue.summary + (issue.priority?.let { "\nПриоритет: ${it.text}" } ?: ""), issueUrl(baseUrl, issue))
                is Event.NewComments -> {
                    val c = ev.comments.last()
                    val more = if (ev.comments.size > 1) " (+${ev.comments.size - 1})" else ""
                    val title = if (ev.comments.any { Model.mentions(it.text, me) }) "Вас упомянули" else "Комментарий"
                    post(context, issue.idReadable.hashCode(), "$title · ${issue.idReadable}$more",
                        "${c.author}: ${Model.commentSnippet(c)}\n${issue.summary}", issueUrl(baseUrl, issue))
                }
                is Event.Mentioned -> post(context, issue.idReadable.hashCode(), "Вас упомянули · ${issue.idReadable}",
                    "${ev.mention.author}: ${Model.commentSnippet(Comment(ev.mention.time, "", ev.mention.author, ev.mention.text))}\n${issue.summary}",
                    issueUrl(baseUrl, issue))
            }
        }
    }

    private fun issueOf(ev: Event) = when (ev) {
        is Event.NewIssue -> ev.issue
        is Event.NewComments -> ev.issue
        is Event.Mentioned -> ev.issue
    }

    @Suppress("MissingPermission")  // разрешение проверено в allowed()
    private fun post(context: Context, id: Int, title: String, text: String, url: String) {
        val pi = PendingIntent.getActivity(context, id, Intent(Intent.ACTION_VIEW, Uri.parse(url)),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_yt)
            .setContentTitle(title)
            .setContentText(text.lineSequence().first())
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(pi)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(context).notify(id, n)
    }
}
