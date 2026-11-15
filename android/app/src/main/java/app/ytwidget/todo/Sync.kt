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
                val own = issues.map { it.idReadable }.toSet()
                // задачи под наблюдением: новые комментарии и смена статуса (ошибка не мешает основному списку)
                var watchEvents: List<Event> = emptyList()
                var watchedIds: Set<String> = emptySet()
                val wq = cfg.watchQuery.trim()
                if (wq.isEmpty()) store.resetWatch()
                else runCatching { api.issuesJson(wq, 100) }.onSuccess { wraw ->
                    val watched = Model.parseIssues(wraw, cfg.fieldNames)
                    val (wev, wstate) = Model.watchEvents(watched, store.watchState, own, seen, store.me)
                    watchedIds = watched.map { it.idReadable }.toSet()
                    watched.forEach { if (it.idReadable !in newSeen) newSeen[it.idReadable] = it.lastCommentTime }
                    store.watchState = wstate
                    store.watchError = null
                    watchEvents = wev.mapNotNull { ev ->
                        if (ev !is Event.StateChanged) return@mapNotNull ev
                        // кто сменил статус: свои изменения не показываем; история недоступна — уведомляем без автора
                        val field = ev.issue.stateFieldName ?: cfg.stateField
                        val who = runCatching { Model.whoChanged(api.fieldChanges(ev.issue), field, ev.to) }.getOrNull()
                        when {
                            who == null -> ev
                            who.login.equals(store.me, ignoreCase = true) -> null
                            else -> ev.copy(author = who.author)
                        }
                    }
                }.onFailure { store.watchError = it.message ?: "ошибка"; AppLog.error(context, "Запрос наблюдения", it) }
                // «Нужно внимание»: задачи с упоминаниями (ошибка здесь не мешает основному списку).
                // Упоминания в своих и наблюдаемых задачах приходят как комментарии с @вами — не дублируем.
                var mentionEvents: List<Event> = emptyList()
                val mq = cfg.mentionsQuery.trim()
                if (mq.isEmpty()) store.resetMentions()
                else runCatching { api.issuesJson(mq, 100) }.onSuccess { mraw ->
                    val mentions = Model.parseIssues(mraw, cfg.fieldNames)
                    val (mev, mseen) = Model.mentionEvents(mentions, own + watchedIds, store.mentionSeen, store.me)
                    mentions.forEach { if (it.idReadable !in newSeen) newSeen[it.idReadable] = it.lastCommentTime }
                    store.mentionSeen = mseen
                    store.mentionsJson = mraw
                    mentionEvents = mev
                }.onFailure { AppLog.error(context, "Запрос упоминаний", it) }
                store.seen = newSeen
                store.notifyState = state
                store.issuesJson = raw
                store.lastOk = System.currentTimeMillis()
                store.lastError = null
                if (notify && cfg.notify) {
                    val all = (events + watchEvents + mentionEvents).filter { cfg.allows(Model.kindOf(it, store.me)) }
                    Notifier.show(context, all, cfg.baseUrl, store.me)
                }
                runCatching { TasksWidget().updateAll(context) }
                Result.success(issues)
            } catch (e: YTException) {
                store.lastError = e.message
                AppLog.warn(context, "Синхронизация", e.message.orEmpty())
                Result.failure(e)
            } catch (e: Exception) {
                store.lastError = "Ошибка: ${e.message}"
                AppLog.error(context, "Синхронизация", e)
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
        runCatching { Sync.run(applicationContext) }.onFailure { AppLog.error(applicationContext, "Фоновая проверка", it) }
        return Result.success()  // ошибки сети не повод для повторов — следующая проверка через 15 минут
    }
}

/** Уведомления: упоминания, комментарии, смена статуса, назначенные задачи; нажатие открывает задачу в браузере. */
object Notifier {
    private const val OLD_CHANNEL = "events"  // до 1.4.0 был один общий канал

    fun issueUrl(baseUrl: String, issue: Issue) = baseUrl.trim().trimEnd('/') + "/issue/" + issue.idReadable

    /** Свой канал на каждый тип — звук и важность можно настроить в системе отдельно. */
    private fun channels(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(OLD_CHANNEL) != null) nm.deleteNotificationChannel(OLD_CHANNEL)
        for (k in NotifyKind.entries) {
            if (nm.getNotificationChannel(k.channel) == null) {
                nm.createNotificationChannel(NotificationChannel(k.channel, k.title,
                    if (k.important) NotificationManager.IMPORTANCE_HIGH else NotificationManager.IMPORTANCE_DEFAULT))
            }
        }
    }

    private fun allowed(context: Context): Boolean =
        Build.VERSION.SDK_INT < 33 ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    fun show(context: Context, events: List<Event>, baseUrl: String, me: String = "") {
        if (events.isEmpty() || !allowed(context)) return
        channels(context)
        val kinds = events.map { Model.kindOf(it, me) }
        if (events.size > 4) {  // много событий сразу — одно сводное уведомление
            val nComments = events.zip(kinds).filter { it.second == NotifyKind.COMMENT }
                .sumOf { (it.first as? Event.NewComments)?.comments?.size ?: 0 }
            val parts = listOfNotNull(
                kinds.count { it == NotifyKind.MENTION }.takeIf { it > 0 }?.let { "упоминаний: $it" },
                kinds.count { it == NotifyKind.ASSIGNED }.takeIf { it > 0 }?.let { "новых задач: $it" },
                nComments.takeIf { it > 0 }?.let { "комментариев: $it" },
                kinds.count { it == NotifyKind.STATUS }.takeIf { it > 0 }?.let { "смен статуса: $it" },
            )
            val ids = events.map { issueOf(it).idReadable }.distinct()
            post(context, kinds.minOf { it }, 1, "YouTrack: " + parts.joinToString(", "),
                ids.take(6).joinToString(", ") + if (ids.size > 6) "…" else "", baseUrl.trim().trimEnd('/') + "/issues")
            return
        }
        events.zip(kinds).forEach { (ev, kind) ->
            val issue = issueOf(ev)
            val id = (issue.idReadable + kind.channel).hashCode()
            val url = issueUrl(baseUrl, issue)
            when (ev) {
                is Event.NewIssue -> post(context, kind, id, "Назначена задача · ${issue.idReadable}",
                    issue.summary + (issue.priority?.let { "\nПриоритет: ${it.text}" } ?: ""), url)
                is Event.NewComments -> {
                    val c = ev.comments.lastOrNull { kind != NotifyKind.MENTION || Model.mentions(it.text, me) } ?: ev.comments.last()
                    val more = if (ev.comments.size > 1) " (+${ev.comments.size - 1})" else ""
                    val title = if (kind == NotifyKind.MENTION) "Вас упомянули" else "Комментарий"
                    post(context, kind, id, "$title · ${issue.idReadable}$more",
                        "${c.author}: ${Model.commentSnippet(c)}\n${issue.summary}", url)
                }
                is Event.Mentioned -> post(context, kind, id, "Вас упомянули · ${issue.idReadable}",
                    "${ev.mention.author}: ${Model.commentSnippet(Comment(ev.mention.time, "", ev.mention.author, ev.mention.text))}\n${issue.summary}",
                    url)
                is Event.StateChanged -> post(context, kind, id, "Статус: ${ev.to} · ${issue.idReadable}",
                    (if (ev.author.isNotEmpty()) "${ev.author}: " else "") + "${ev.from} → ${ev.to}\n${issue.summary}", url)
            }
        }
    }

    private fun issueOf(ev: Event) = when (ev) {
        is Event.NewIssue -> ev.issue
        is Event.NewComments -> ev.issue
        is Event.Mentioned -> ev.issue
        is Event.StateChanged -> ev.issue
    }

    /** Проверочное уведомление из настроек; возвращает текст для экрана. */
    fun sample(context: Context): String {
        if (!allowed(context) || !NotificationManagerCompat.from(context).areNotificationsEnabled())
            return "✗ Уведомления приложения запрещены в Android"
        channels(context)
        post(context, NotifyKind.MENTION, 2, "Проверка · YouTrack", "Так выглядят уведомления о событиях в задачах.", "")
        return "✓ Проверочное уведомление отправлено"
    }

    @Suppress("MissingPermission")  // разрешение проверено в allowed()
    private fun post(context: Context, kind: NotifyKind, id: Int, title: String, text: String, url: String) {
        val intent = if (url.isEmpty()) context.packageManager.getLaunchIntentForPackage(context.packageName) ?: Intent()
            else Intent(Intent.ACTION_VIEW, Uri.parse(url))
        val pi = PendingIntent.getActivity(context, id, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = NotificationCompat.Builder(context, kind.channel)
            .setSmallIcon(R.drawable.ic_stat_yt)
            .setContentTitle(title)
            .setContentText(text.lineSequence().first())
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setCategory(if (kind == NotifyKind.MENTION || kind == NotifyKind.COMMENT) NotificationCompat.CATEGORY_MESSAGE
                else NotificationCompat.CATEGORY_EVENT)
            .setPriority(if (kind.important) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT)
            .setContentIntent(pi)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(context).notify(id, n)
    }
}
