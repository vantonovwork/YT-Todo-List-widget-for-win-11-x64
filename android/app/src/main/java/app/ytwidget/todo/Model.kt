package app.ytwidget.todo

import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.temporal.ChronoUnit

/** Плашка: текст + цвета ARGB из YouTrack. null-цвета — нейтральная плашка в цветах темы. */
data class Chip(val text: String, val bg: Long? = null, val fg: Long? = null)
data class Sprint(val name: String, val active: Boolean)
data class Due(val label: String, val bg: Long?, val fg: Long?, val hot: Boolean)
data class Comment(val created: Long, val login: String, val author: String, val text: String)

/** Поле задачи в YouTrack: id нужен для запроса вариантов, имя — для команды. */
data class FieldRef(val id: String, val name: String, val type: String)

/** Что меняем кликом по плашке карточки. */
enum class FieldKind(val what: String) { STATE("статус"), PRIORITY("приоритет"), SPRINT("спринт") }

/** Вариант выпадающего списка: подпись, команда YouTrack, выбран ли сейчас. */
data class FieldOption(val label: String, val command: String, val checked: Boolean)
data class FieldOptions(val field: String, val current: List<String>, val options: List<FieldOption>)

/** Упоминание @login в комментариях задачи (вкладка «Нужно внимание»). */
data class Mention(val time: Long, val author: String, val text: String, val count: Int, val replied: Boolean)

data class Issue(
    val id: String,
    val idReadable: String,
    val summary: String,
    val state: Chip?,
    val stateField: FieldRef?,
    val priority: Chip?,
    val dueMillis: Long?,
    val sprints: List<Sprint>,
    val comments: List<Comment>,
    val priorityField: FieldRef? = null,
    /** Поле спринта (есть, даже если спринт не задан) — чтобы назначить спринт с карточки. */
    val sprintField: FieldRef? = null,
) {
    val stateFieldId: String? get() = stateField?.id
    val stateFieldName: String? get() = stateField?.name
    val lastCommentTime: Long get() = comments.maxOfOrNull { it.created } ?: 0L
    /** Цвет полоски слева на карточке — по приоритету. */
    val barColor: Long? get() = priority?.bg
}

/** Имена полей из настроек; если такого поля нет — ищем «по смыслу» (у всех YouTrack поля названы по-разному). */
data class FieldNames(
    val state: String = "State",
    val priority: String = "Priority",
    val due: String = "",
    val sprint: String = "",
)

sealed class Event {
    data class NewIssue(val issue: Issue) : Event()
    data class NewComments(val issue: Issue, val comments: List<Comment>) : Event()
    data class Mentioned(val issue: Issue, val mention: Mention) : Event()
    /** Статус задачи под наблюдением сменился; author — кто сменил (из истории задачи, может быть неизвестен). */
    data class StateChanged(val issue: Issue, val from: String, val to: String, val author: String = "") : Event()
}

/** Тип уведомления: отдельный переключатель в настройках и отдельный канал Android. */
enum class NotifyKind(val channel: String, val title: String, val important: Boolean) {
    MENTION("mention", "Упоминания", true),
    ASSIGNED("assigned", "Назначенные задачи", true),
    COMMENT("comment", "Новые комментарии", false),
    STATUS("status", "Смена статуса", false),
}

/** Задачи под наблюдением: последний известный статус и время последнего комментария по каждой. */
data class WatchState(val states: Map<String, String>, val comments: Map<String, Long>)

/** Запись истории задачи о смене поля. */
data class FieldChange(val time: Long, val login: String, val author: String, val field: String, val added: String)

/** Своя срочная задача: хранится только в приложении, в YouTrack не попадает (как в десктопной версии). */
data class UrgentTask(
    val id: String,
    val title: String,
    val text: String,
    val created: Long,
    val done: Boolean = false,
    val doneAt: Long = 0L,
)

object Urgent {
    const val TITLE_MAX = 100
    const val TEXT_MAX = 1000

    /** Разбор сохранённого списка; длина полей ограничивается и при чтении, пустые/битые записи пропускаются. */
    fun parse(json: String?): List<UrgentTask> {
        val arr = runCatching { JSONArray(json ?: return emptyList()) }.getOrNull() ?: return emptyList()
        return (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }.mapNotNull { o ->
            val title = o.optString("title").trim().take(TITLE_MAX)
            val id = o.optString("id")
            if (title.isEmpty() || id.isEmpty()) null
            else UrgentTask(id, title, o.optString("text").take(TEXT_MAX), o.optLong("created"),
                o.optBoolean("done"), o.optLong("done_at"))
        }
    }

    /** Формат файла совпадает с десктопным urgent.json. */
    fun toJson(items: List<UrgentTask>): String = JSONArray(items.map {
        JSONObject().put("id", it.id).put("title", it.title).put("text", it.text).put("created", it.created)
            .put("done", it.done).put("done_at", it.doneAt)
    }).toString()

    fun add(items: List<UrgentTask>, title: String, text: String, now: Long, id: String): List<UrgentTask> {
        val t = title.trim().take(TITLE_MAX)
        if (t.isEmpty()) return items
        return items + UrgentTask(id, t, text.trim().take(TEXT_MAX), now)
    }

    fun update(items: List<UrgentTask>, id: String, title: String, text: String): List<UrgentTask> {
        val t = title.trim().take(TITLE_MAX)
        if (t.isEmpty()) return items
        return items.map { if (it.id == id) it.copy(title = t, text = text.trim().take(TEXT_MAX)) else it }
    }

    fun toggleDone(items: List<UrgentTask>, id: String, now: Long): List<UrgentTask> =
        items.map { if (it.id == id) it.copy(done = !it.done, doneAt = if (it.done) 0L else now) else it }

    fun delete(items: List<UrgentTask>, id: String) = items.filter { it.id != id }
    fun clearDone(items: List<UrgentTask>) = items.filter { !it.done }

    /** Невыполненные — новые сверху, выполненные — в конце (недавно выполненные выше). */
    fun ordered(items: List<UrgentTask>): List<UrgentTask> =
        items.sortedWith(compareBy<UrgentTask> { it.done }.thenByDescending { if (it.done) it.doneAt else it.created })
}

/** Что уже показано в уведомлениях: время последнего комментария по задаче + задачи прошлой загрузки. */
data class NotifyState(val notified: Map<String, Long>, val known: Set<String>)

object Model {
    const val ISSUE_FIELDS = "id,idReadable,summary," +
        "customFields(id,name,\$type,value(name,fullName,login,archived,released,releaseDate," +
        "color(background,foreground)))," +
        "comments(created,deleted,text,author(login,fullName))"

    private val FALLBACK_PRIORITY = mapOf(
        "show-stopper" to 0xFFD32F2F, "критический" to 0xFFD32F2F, "critical" to 0xFFE53935,
        "неотложный" to 0xFFB71C1C,
        "major" to 0xFFFB8C00, "высокий" to 0xFFFB8C00, "high" to 0xFFFB8C00,
        "normal" to 0xFF1E88E5, "обычный" to 0xFF1E88E5, "medium" to 0xFF1E88E5, "средний" to 0xFF1E88E5,
        "minor" to 0xFF757575, "низкий" to 0xFF757575, "low" to 0xFF757575,
    )
    private val DUE_HINTS = listOf("due", "deadline", "срок", "дедлайн", "окончан")
    private val WEEKDAYS = listOf("пн", "вт", "ср", "чт", "пт", "сб", "вс")

    // ---------------------------------------------------------------- разбор задач
    fun parseIssues(json: String, names: FieldNames, now: Long = System.currentTimeMillis()): List<Issue> {
        val arr = JSONArray(json)
        return (0 until arr.length()).map { parseIssue(arr.getJSONObject(it), names, now) }
    }

    fun parseIssue(o: JSONObject, names: FieldNames, now: Long = System.currentTimeMillis()): Issue {
        val cfs = o.optJSONArray("customFields") ?: JSONArray()
        val stateF = findField(cfs, names.state, "state")
        val prioF = findField(cfs, names.priority, "priority")
        val comments = mutableListOf<Comment>()
        o.optJSONArray("comments")?.let { arr ->
            for (i in 0 until arr.length()) {
                val c = arr.getJSONObject(i)
                if (c.optBoolean("deleted")) continue
                comments += parseComment(c)
            }
        }
        val sprintF = sprintField(cfs, names.sprint)
        return Issue(
            id = o.optString("id"),
            idReadable = o.optString("idReadable"),
            summary = o.optString("summary").ifBlank { "(без названия)" },
            state = stateF?.let { chipOf(it) },
            stateField = stateF?.let { refOf(it) },
            priority = prioF?.let { chipOf(it) },
            dueMillis = dueOf(cfs, names.due),
            sprints = sprintsOf(sprintF, now),
            comments = comments,
            priorityField = prioF?.let { refOf(it) },
            sprintField = sprintF?.let { refOf(it) },
        )
    }

    private fun refOf(f: JSONObject): FieldRef? {
        val id = f.optString("id")
        return if (id.isEmpty()) null else FieldRef(id, f.optString("name"), f.optString("\$type"))
    }

    /** Комментарий из списка задач или из ответа на создание комментария. */
    fun parseComment(c: JSONObject): Comment {
        val a = c.optJSONObject("author")
        return Comment(
            created = c.optLong("created"),
            login = a?.optString("login").orEmpty(),
            author = a?.optString("fullName")?.takeIf { it.isNotEmpty() } ?: a?.optString("login").orEmpty(),
            text = c.optString("text").takeUnless { c.isNull("text") }.orEmpty(),
        )
    }

    // ---------------------------------------------------------------- выпадающие списки на плашках
    /**
     * Варианты для плашки статуса / приоритета / спринта по ответу
     * /api/issues/{id}/customFields/{fid}?fields=possibleEvents(..),projectCustomField(bundle(values(..))).
     * Статус — только разрешённые переходы workflow (или все значения обычного поля), приоритет — все значения,
     * спринт — активные спринты: выбор переносит задачу, выбор текущего — убирает из спринта.
     * Команды YouTrack пишутся БЕЗ фигурных скобок: «Приоритет Высокий», «remove Спринт 26.20 add Спринт 26.22».
     */
    fun fieldOptions(kind: FieldKind, field: FieldRef, current: List<String>, data: JSONObject, now: Long): FieldOptions {
        val name = field.name
        val values = data.optJSONObject("projectCustomField")?.optJSONObject("bundle")?.optJSONArray("values") ?: JSONArray()
        val vals = (0 until values.length()).mapNotNull { values.optJSONObject(it) }.filter { it.optString("name").isNotEmpty() }
        val options = when (kind) {
            FieldKind.STATE -> {
                val events = data.optJSONArray("possibleEvents") ?: JSONArray()
                if (events.length() > 0 || field.type == "StateMachineIssueCustomField") {
                    (0 until events.length()).mapNotNull { events.optJSONObject(it) }.filter { it.optString("id").isNotEmpty() }
                        .map { FieldOption(it.optString("presentation").ifEmpty { it.optString("id") }, "$name ${it.optString("id")}", false) }
                } else {
                    vals.filter { !it.optBoolean("archived") && it.optString("name") !in current }
                        .map { FieldOption(it.optString("name"), "$name ${it.optString("name")}", false) }
                }
            }
            FieldKind.PRIORITY -> vals.filter { !it.optBoolean("archived") || it.optString("name") in current }
                .map { FieldOption(it.optString("name"), "$name ${it.optString("name")}", it.optString("name") in current) }
            FieldKind.SPRINT -> {
                fun release(v: JSONObject) = if (v.has("releaseDate") && !v.isNull("releaseDate")) v.optLong("releaseDate") else 0L
                val active = vals.filter { !it.optBoolean("archived") && !it.optBoolean("released") &&
                    (release(it).takeIf { r -> r > 0 } ?: now) >= now }.sortedBy { release(it) }
                val drop = current.joinToString(" ") { "remove $name $it" }
                (current + active.map { it.optString("name") }.filter { it !in current }).map { n ->
                    if (n in current) FieldOption(n, "remove $name $n", true)        // текущий — убрать из спринта
                    else FieldOption(n, "$drop add $name $n".trim(), false)          // другой — перенести
                }
            }
        }
        return FieldOptions(name, current, options)
    }

    /** Текущие значения поля задачи (для отметки «✓» в списке). */
    fun currentValues(issue: Issue, kind: FieldKind): List<String> = when (kind) {
        FieldKind.STATE -> listOfNotNull(issue.state?.text)
        FieldKind.PRIORITY -> listOfNotNull(issue.priority?.text)
        FieldKind.SPRINT -> issue.sprints.map { it.name }
    }

    // ---------------------------------------------------------------- упоминания (вкладка «Нужно внимание»)
    private fun mentionRegex(login: String) =
        Regex("@" + Regex.escape(login) + "(?![\\p{L}\\p{N}_.-])", RegexOption.IGNORE_CASE)

    fun mentions(text: String, login: String) = login.isNotBlank() && mentionRegex(login).containsMatchIn(text)

    /** Последнее упоминание @login в комментариях; replied — после него вы сами написали комментарий. */
    fun mentionInfo(issue: Issue, login: String): Mention? {
        if (login.isBlank()) return null
        val pat = mentionRegex(login)
        val hits = issue.comments.filter { pat.containsMatchIn(it.text) }
        if (hits.isEmpty()) return null
        val last = hits.maxBy { it.created }
        val replied = issue.comments.any { it.created > last.created && it.login.equals(login, ignoreCase = true) }
        return Mention(last.created, last.author, last.text, hits.size, replied)
    }

    /** Упоминание ждёт ответа: вы не ответили комментарием и не отметили вручную «отвечено» (handled — время отметки). */
    fun attnWaiting(m: Mention?, handled: Long?) = m != null && !m.replied && (handled ?: 0L) < m.time

    /**
     * Уведомления «Вас упомянули» — о новых упоминаниях в чужих задачах (о своих сообщит уведомление о комментарии).
     * seen == null — первая загрузка: только запоминаем. Возвращает события и новое состояние.
     */
    fun mentionEvents(mentions: List<Issue>, own: Set<String>, seen: Map<String, Long>?, login: String):
        Pair<List<Event.Mentioned>, Map<String, Long>> {
        val next = seen?.toMutableMap() ?: mutableMapOf()
        val events = mutableListOf<Event.Mentioned>()
        for (issue in mentions) {
            val id = issue.idReadable
            if (id.isEmpty()) continue
            val m = mentionInfo(issue, login)
            val t = m?.time ?: 0L
            if (seen != null && m != null && t > (seen[id] ?: 0L) && id !in own && !m.replied) events += Event.Mentioned(issue, m)
            next[id] = maxOf(next[id] ?: 0L, t)
        }
        return events to next
    }

    fun findField(cfs: JSONArray, name: String, kind: String): JSONObject? {
        val fields = (0 until cfs.length()).map { cfs.getJSONObject(it) }
        fields.firstOrNull { it.optString("name").trim().equals(name.trim(), ignoreCase = true) }?.let { return it }
        return fields.firstOrNull {
            val n = it.optString("name").lowercase()
            val t = it.optString("\$type")
            when (kind) {
                "state" -> t == "StateMachineIssueCustomField" || t == "StateIssueCustomField" ||
                    n in setOf("state", "status", "статус", "состояние")
                "priority" -> "priorit" in n || "приоритет" in n
                else -> false
            }
        }
    }

    /** Текст значения поля: одно значение, список или пусто. */
    fun valueText(f: JSONObject): String? {
        if (!f.has("value") || f.isNull("value")) return null
        return when (val v = f.get("value")) {
            is JSONArray -> (0 until v.length()).mapNotNull { v.optJSONObject(it)?.optString("name") }
                .filter { it.isNotEmpty() }.joinToString(", ").ifEmpty { null }
            is JSONObject -> listOf("name", "fullName", "login").map { v.optString(it) }.firstOrNull { it.isNotEmpty() }
            else -> v.toString()
        }
    }

    private fun chipOf(f: JSONObject): Chip? {
        val text = valueText(f) ?: return null
        val color = (f.opt("value") as? JSONObject)?.optJSONObject("color")
        return chipColors(text, color?.optString("background"), color?.optString("foreground"))
    }

    /** Цвета плашки: из YouTrack, но слишком светлые фоны заменяем нейтральным (как в десктопной версии). */
    fun chipColors(text: String, bg: String?, fg: String?): Chip {
        val rgb = parseHex(bg)
        if (rgb != null) {
            val lum = luminance(rgb)
            if (lum < 0.88) {
                val fore = parseHex(fg) ?: if (lum < 0.6) 0xFFFFFFFF else 0xFF111111
                return Chip(text, rgb, fore)
            }
        }
        FALLBACK_PRIORITY[text.lowercase()]?.let { return Chip(text, it, 0xFFFFFFFF) }
        return Chip(text)
    }

    fun parseHex(c: String?): Long? {
        var s = c?.trim()?.removePrefix("#") ?: return null
        if (s.length == 3) s = s.map { "$it$it" }.joinToString("")
        if (s.length != 6) return null
        return s.toLongOrNull(16)?.let { 0xFF000000 or it }
    }

    fun luminance(argb: Long): Double {
        val r = (argb shr 16) and 0xFF
        val g = (argb shr 8) and 0xFF
        val b = argb and 0xFF
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    }

    // ---------------------------------------------------------------- сроки
    private fun dueOf(cfs: JSONArray, dueField: String): Long? {
        val fields = (0 until cfs.length()).map { cfs.getJSONObject(it) }
        val f = (if (dueField.isNotBlank()) fields.firstOrNull { it.optString("name").equals(dueField, true) } else null)
            ?: fields.firstOrNull {
                it.optString("\$type") in setOf("DateIssueCustomField", "DateTimeIssueCustomField") &&
                    DUE_HINTS.any { h -> h in it.optString("name").lowercase() }
            }
        val v = f?.opt("value")
        return if (v is Number) v.toLong() else null
    }

    fun dueInfo(millis: Long?, today: LocalDate = LocalDate.now(), zone: ZoneId = ZoneId.systemDefault()): Due? {
        if (millis == null) return null
        val d = Instant.ofEpochMilli(millis).atZone(zone).toLocalDate()
        val delta = ChronoUnit.DAYS.between(today, d)
        val label = "%s, %02d.%02d".format(WEEKDAYS[d.dayOfWeek.value - 1], d.dayOfMonth, d.monthValue) +
            if (d.year == today.year) "" else ".%02d".format(d.year % 100)
        return when {
            delta < 0 -> Due("просрочено · $label", 0xFFD32F2F, 0xFFFFFFFF, true)
            delta == 0L -> Due("сегодня", 0xFFEF6C00, 0xFFFFFFFF, true)
            delta == 1L -> Due("завтра", 0xFFF9A825, 0xFF1B1B1B, true)
            else -> Due("до $label", null, null, false)
        }
    }

    // ---------------------------------------------------------------- спринт
    /** Поле спринта: по имени из настроек или по смыслу (поле-версия со словом «спринт»/«sprint»). */
    private fun sprintField(cfs: JSONArray, sprintField: String): JSONObject? {
        val fields = (0 until cfs.length()).map { cfs.getJSONObject(it) }
        return (if (sprintField.isNotBlank()) fields.firstOrNull { it.optString("name").equals(sprintField, true) } else null)
            ?: fields.firstOrNull {
                "Version" in it.optString("\$type") &&
                    listOf("спринт", "sprint").any { h -> h in it.optString("name").lowercase() }
            }
    }

    private fun sprintsOf(f: JSONObject?, now: Long): List<Sprint> {
        if (f == null) return emptyList()
        val v = f.opt("value")
        val items = when (v) {
            is JSONArray -> (0 until v.length()).mapNotNull { v.optJSONObject(it) }
            is JSONObject -> listOf(v)
            else -> emptyList()
        }
        return items.filter { it.optString("name").isNotEmpty() }.map {
            val release = if (it.has("releaseDate") && !it.isNull("releaseDate")) it.optLong("releaseDate") else 0L
            val active = !it.optBoolean("archived") && !it.optBoolean("released") && (if (release == 0L) now else release) >= now
            Triple(it.optString("name"), active, release)
        }.sortedWith(compareBy<Triple<String, Boolean, Long>> { !it.second }.thenByDescending { it.third })
            .map { Sprint(it.first, it.second) }
    }

    // ---------------------------------------------------------------- текст
    /** Убрать разметку YouTrack (жирный, ссылки, код, цитаты), сохранив абзацы; обрезать до limit. */
    fun cleanMarkdown(text: String?, limit: Int): String {
        var t = text ?: return ""
        t = Regex("""!\[[^\]]*]\([^)]*\)""").replace(t, "[картинка]")      // картинки — до ссылок
        t = Regex("""\[([^\]]*)]\([^)]*\)""").replace(t, "$1")
        t = Regex("```.*?```", RegexOption.DOT_MATCHES_ALL).replace(t, "[код]")
        t = Regex("[*_`#>]+").replace(t, "")
        t = Regex("[ \\t]+").replace(t, " ")
        t = t.split("\n").joinToString("\n") { it.trim() }
        t = Regex("\\n\\s*\\n+").replace(t, "\n").trim()
        return if (t.length <= limit) t else t.substring(0, limit - 1).trimEnd() + "…"
    }

    fun commentSnippet(c: Comment, limit: Int = 140): String =
        cleanMarkdown(c.text, 10_000).split(Regex("\\s+")).joinToString(" ").let {
            if (it.length <= limit) it else it.substring(0, limit - 1) + "…"
        }

    // ---------------------------------------------------------------- новые комментарии и уведомления
    /** Комментарии коллег после последнего просмотра задачи (свои не считаются). */
    fun newComments(issue: Issue, seenBase: Long?, me: String): Int {
        if (seenBase == null) return 0
        return issue.comments.count { it.created > seenBase && !it.login.equals(me, ignoreCase = true) }
    }

    /**
     * События для уведомлений. state == null — первая загрузка: ни о чём не уведомляем, только запоминаем.
     * Возвращает события и новое состояние.
     */
    fun computeEvents(issues: List<Issue>, state: NotifyState?, seen: Map<String, Long>, me: String): Pair<List<Event>, NotifyState> {
        val notified = state?.notified?.toMutableMap() ?: mutableMapOf()
        val events = mutableListOf<Event>()
        for (issue in issues) {
            val id = issue.idReadable
            if (id.isEmpty()) continue
            val last = issue.lastCommentTime
            if (state == null || id !in state.known) {
                if (state != null) events += Event.NewIssue(issue)
                notified[id] = last
                continue
            }
            val base = maxOf(notified[id] ?: 0L, seen[id] ?: 0L)
            val fresh = issue.comments.filter { it.created > base && !it.login.equals(me, ignoreCase = true) }
            if (fresh.isNotEmpty()) events += Event.NewComments(issue, fresh)
            notified[id] = maxOf(notified[id] ?: 0L, last)
        }
        return events to NotifyState(notified, issues.map { it.idReadable }.filter { it.isNotEmpty() }.toSet())
    }

    /**
     * События по задачам под наблюдением: новые комментарии коллег и смена статуса.
     * prev == null — первая загрузка, только запоминаем. Задачи из основного списка (own) о комментариях
     * не сообщают — это уже делает computeEvents. Задача, впервые попавшая под наблюдение, событий не даёт.
     */
    fun watchEvents(watched: List<Issue>, prev: WatchState?, own: Set<String>, seen: Map<String, Long>, me: String):
        Pair<List<Event>, WatchState> {
        val states = mutableMapOf<String, String>()
        val comments = mutableMapOf<String, Long>()
        val events = mutableListOf<Event>()
        for (issue in watched) {
            val id = issue.idReadable
            if (id.isEmpty()) continue
            val state = issue.state?.text.orEmpty()
            states[id] = state
            val known = prev != null && (id in prev.states || id in prev.comments)
            val base = maxOf(prev?.comments?.get(id) ?: 0L, seen[id] ?: 0L)
            comments[id] = maxOf(base, issue.lastCommentTime)
            if (!known) continue
            val old = prev!!.states[id].orEmpty()
            if (old.isNotEmpty() && state.isNotEmpty() && old != state) events += Event.StateChanged(issue, old, state)
            if (id !in own) {
                val fresh = issue.comments.filter { it.created > base && !it.login.equals(me, ignoreCase = true) }
                if (fresh.isNotEmpty()) events += Event.NewComments(issue, fresh)
            }
        }
        return events to WatchState(states, comments)
    }

    /** История задачи (/activities, CustomFieldCategory) → смены полей, новые первыми. */
    fun parseFieldChanges(json: String): List<FieldChange> {
        val arr = runCatching { JSONArray(json) }.getOrNull() ?: return emptyList()
        return (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }.map { a ->
            val author = a.optJSONObject("author")
            val added = a.opt("added")
            val addedText = when (added) {
                is JSONArray -> (0 until added.length()).mapNotNull { added.optJSONObject(it)?.optString("name") }.joinToString(", ")
                is JSONObject -> added.optString("name")
                else -> ""
            }
            FieldChange(a.optLong("timestamp"), author?.optString("login").orEmpty(),
                author?.optString("fullName").orEmpty().ifEmpty { author?.optString("login").orEmpty() },
                a.optJSONObject("field")?.optString("name").orEmpty(), addedText)
        }.sortedByDescending { it.time }
    }

    /** Кто последним поставил полю field значение value; null — не нашли в истории. */
    fun whoChanged(changes: List<FieldChange>, field: String, value: String): FieldChange? =
        changes.firstOrNull { it.field.equals(field, ignoreCase = true) && (value.isEmpty() || it.added.equals(value, ignoreCase = true)) }

    /** Тип уведомления; комментарий с @вами — это упоминание. */
    fun kindOf(ev: Event, me: String): NotifyKind = when (ev) {
        is Event.Mentioned -> NotifyKind.MENTION
        is Event.NewComments -> if (ev.comments.any { mentions(it.text, me) }) NotifyKind.MENTION else NotifyKind.COMMENT
        is Event.StateChanged -> NotifyKind.STATUS
        is Event.NewIssue -> NotifyKind.ASSIGNED
    }

    /** Порядок плана на день: как расставил пользователь, выполненные — в конце. */
    fun planOrder(issues: List<Issue>, plan: List<String>, done: Set<String>): List<Issue> {
        val pos = plan.withIndex().associate { it.value to it.index }
        return issues.filter { it.idReadable in pos }
            .sortedWith(compareBy<Issue> { it.idReadable in done }.thenBy { pos[it.idReadable] ?: 0 })
    }
}
