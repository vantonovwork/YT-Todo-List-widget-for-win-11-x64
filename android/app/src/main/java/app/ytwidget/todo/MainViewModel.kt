package app.ytwidget.todo

import android.app.Application
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainViewModel(app: Application) : AndroidViewModel(app) {
    private val store = Store(app)

    var config by mutableStateOf(store.config); private set
    var configured by mutableStateOf(store.configured); private set
    var issues by mutableStateOf(store.issues()); private set
    var loading by mutableStateOf(false); private set
    var error by mutableStateOf(store.lastError); private set
    var lastOk by mutableStateOf(store.lastOk); private set
    var seen by mutableStateOf(store.seen); private set
    var me by mutableStateOf(store.me); private set

    var tab by mutableStateOf(store.tab); private set
    var search by mutableStateOf(store.search); private set
    var hotOnly by mutableStateOf(store.hotOnly); private set
    var prioFilter by mutableStateOf(store.prioFilter); private set
    var sprintFilter by mutableStateOf(store.sprintFilter); private set
    var plan by mutableStateOf(store.plan); private set
    var done by mutableStateOf(store.done); private set
    var planGroups by mutableStateOf(store.planGroups); private set
    var planGroupOf by mutableStateOf(store.planGroupOf); private set
    /** Открыто окно настройки блоков плана на день. */
    var editingGroups by mutableStateOf(false)

    var urgent by mutableStateOf(store.urgent); private set
    var mentions by mutableStateOf(store.mentions()); private set
    var attn by mutableStateOf(store.attn); private set
    var drafts by mutableStateOf(store.drafts); private set
    /** Задача, для которой открыто окно комментария (idReadable), или null. */
    var commentFor by mutableStateOf<String?>(null)
    /** Задача, открытая на экране полного просмотра (idReadable), или null. */
    var openedId by mutableStateOf<String?>(null); private set

    var expanded by mutableStateOf(emptySet<String>()); private set
    /** idReadable → описание; null — загружается; значение начинается с «\u0000» — ошибка. */
    val details = mutableStateMapOf<String, String?>()
    var message by mutableStateOf<String?>(null)
    /** Прошлый запуск завершился падением: текст ошибки для окна «Отправить отчёт». */
    var crash by mutableStateOf(AppLog.pendingCrash(app)); private set

    fun dismissCrash() {
        AppLog.clearPendingCrash(getApplication())
        crash = null
        config = store.config  // падение могло выключить оформление разметки
    }

    fun setMarkdown(on: Boolean) {
        store.config = store.config.copy(markdown = on)
        config = store.config
    }

    private fun log(where: String, e: Throwable) = AppLog.error(getApplication(), where, e)

    init {
        if (configured) refresh()
    }

    // ---------------------------------------------------------------- загрузка
    fun refresh() {
        if (loading) return
        loading = true
        viewModelScope.launch {
            val r = Sync.run(getApplication())
            loading = false
            reloadFromStore()
            r.exceptionOrNull()?.let { error = it.message }
        }
    }

    private fun reloadFromStore() {
        issues = store.issues()
        mentions = store.mentions()
        error = store.lastError
        lastOk = store.lastOk
        seen = store.seen
        me = store.me
        done = store.done  // в полночь отметки «сделано» сбрасываются
    }

    // ---------------------------------------------------------------- фильтры и вкладки
    fun updateSearch(v: String) { search = v; store.search = v }
    fun selectTab(v: String) { tab = v; store.tab = v }
    fun toggleHot() { hotOnly = !hotOnly; store.hotOnly = hotOnly }
    fun togglePrio(p: String) { prioFilter = prioFilter.toggle(p); store.prioFilter = prioFilter }
    fun toggleSprint(s: String) { sprintFilter = sprintFilter.toggle(s); store.sprintFilter = sprintFilter }
    fun resetFilters() {
        hotOnly = false; prioFilter = emptySet(); sprintFilter = emptySet()
        store.hotOnly = false; store.prioFilter = emptySet(); store.sprintFilter = emptySet()
    }

    val filtersActive get() = search.isNotBlank() || hotOnly || prioFilter.isNotEmpty() || sprintFilter.isNotEmpty()

    fun priorities(): List<String> {
        val list = issues.map { it.priority?.text ?: NO_PRIO }.distinct()
        return list.filter { it != NO_PRIO } + if (NO_PRIO in list) listOf(NO_PRIO) else emptyList()
    }

    fun sprints(): List<Sprint> {
        val all = linkedMapOf<String, Boolean>()
        issues.flatMap { it.sprints }.forEach { all[it.name] = (all[it.name] ?: false) || it.active }
        val list = all.map { Sprint(it.key, it.value) }.sortedWith(compareBy<Sprint> { !it.active }.thenBy { it.name })
        return if (issues.any { it.sprints.isEmpty() }) list + Sprint(NO_SPRINT, false) else list
    }

    fun visible(): List<Issue> {
        val base = when (tab) {
            "day" -> Model.planOrder(allIssues(), plan, done, planGroups, planGroupOf)
            "attention" -> mentions.sortedWith(compareBy<Issue> { !attnWaiting(it) }.thenByDescending { mention(it)?.time ?: 0L })
            else -> issues
        }
        val q = search.trim().lowercase()
        return base.filter { i ->
            val due = Model.dueInfo(i.dueMillis)
            val m = if (tab == "attention") mention(i) else null
            (q.isEmpty() || listOfNotNull(i.idReadable, i.summary, i.state?.text, i.priority?.text, due?.label, m?.author, m?.text)
                .plus(i.sprints.map { it.name }).joinToString(" ").lowercase().contains(q)) &&
                (prioFilter.isEmpty() || (i.priority?.text ?: NO_PRIO) in prioFilter) &&
                (!hotOnly || due?.hot == true) &&
                (sprintFilter.isEmpty() || (i.sprints.map { it.name }.ifEmpty { listOf(NO_SPRINT) }).any { it in sprintFilter })
        }
    }

    /** Свои задачи + задачи с упоминаниями без повторов — для плана на день и полного просмотра. */
    fun allIssues(): List<Issue> {
        val mine = issues.map { it.idReadable }.toSet()
        return issues + mentions.filter { it.idReadable !in mine }
    }

    fun dayCount() = allIssues().count { it.idReadable in plan }

    // ---------------------------------------------------------------- «Нужно внимание»
    fun mention(issue: Issue) = Model.mentionInfo(issue, me)
    fun attnWaiting(issue: Issue) = Model.attnWaiting(mention(issue), attn[issue.idReadable])
    fun attnCount() = mentions.count { attnWaiting(it) }

    /** «ждёт ответа» ↔ «отвечено» вручную. */
    fun toggleAttn(issue: Issue) {
        val m = mention(issue) ?: return
        attn = if (attnWaiting(issue)) attn + (issue.idReadable to m.time) else attn - issue.idReadable
        store.attn = attn
    }
    fun urgentCount() = urgent.count { !it.done }

    // ---------------------------------------------------------------- свои срочные задачи
    /** На вкладке «Срочные» работает только поиск (по названию и тексту). */
    fun visibleUrgent(): List<UrgentTask> {
        val q = search.trim().lowercase()
        return Urgent.ordered(urgent).filter { q.isEmpty() || "${it.title} ${it.text}".lowercase().contains(q) }
    }

    private fun saveUrgent(list: List<UrgentTask>) { urgent = list; store.urgent = list }
    fun addUrgent(title: String, text: String) =
        saveUrgent(Urgent.add(urgent, title, text, System.currentTimeMillis(), "U-" + java.util.UUID.randomUUID().toString().take(8)))
    fun updateUrgent(id: String, title: String, text: String) = saveUrgent(Urgent.update(urgent, id, title, text))
    fun toggleUrgentDone(id: String) = saveUrgent(Urgent.toggleDone(urgent, id, System.currentTimeMillis()))
    fun deleteUrgent(id: String) = saveUrgent(Urgent.delete(urgent, id))
    fun clearDoneUrgent() = saveUrgent(Urgent.clearDone(urgent))

    // ---------------------------------------------------------------- полный просмотр задачи
    fun openedIssue(): Issue? = openedId?.let { id -> allIssues().firstOrNull { it.idReadable == id } }

    fun openFull(issue: Issue) {
        openedId = issue.idReadable
        markRead(issue)
        loadDescription(issue)
        loadRendered(issue, force = true)  // каждый раз свежие комментарии
    }

    /** idReadable → описание и комментарии в оформлении YouTrack; null — загружается; ошибка — нет записи + сообщение в журнале. */
    val rendered = mutableStateMapOf<String, RenderedIssue?>()

    fun loadRendered(issue: Issue, force: Boolean = false) {
        val id = issue.idReadable
        if (!force && id in rendered) return  // уже есть или грузится
        if (rendered[id] == null) rendered[id] = null  // при обновлении старый текст остаётся на экране
        renderFailed = renderFailed - id
        viewModelScope.launch {
            try {
                rendered[id] = withContext(Dispatchers.IO) { YouTrackApi(config.baseUrl, store.token).rendered(issue) }
            } catch (e: Exception) {
                log("Оформленный текст ${issue.idReadable}", e)
                if (rendered[id] == null) {
                    rendered.remove(id)
                    renderFailed = renderFailed + id
                }
            }
        }
    }

    /** Задачи, для которых оформленный текст не загрузился — показываем свой разбор разметки. */
    var renderFailed by mutableStateOf(emptySet<String>()); private set

    /** Картинки и вложения для встроенного просмотра (вызывается из фонового потока WebView). */
    fun resource(url: String): Pair<String, ByteArray>? =
        runCatching { YouTrackApi(config.baseUrl, store.token).resource(url) }.getOrNull()

    fun closeFull() { openedId = null }
    fun newCount(issue: Issue) = Model.newComments(issue, seen[issue.idReadable], me)
    fun totalNew() = issues.sumOf { newCount(it) }

    // ---------------------------------------------------------------- план на день
    fun toggleStar(issue: Issue) {
        val id = issue.idReadable
        plan = if (id in plan) plan - id else plan + id
        if (id !in plan) { done = done - id; planGroupOf = planGroupOf - id; store.planGroupOf = planGroupOf }
        store.plan = plan; store.done = done
        refreshWidget()
    }

    fun toggleDone(issue: Issue) {
        done = done.toggle(issue.idReadable)
        store.done = done
        refreshWidget()
    }

    private fun dayOrder() = Model.planOrder(allIssues(), plan, done, planGroups, planGroupOf).map { it.idReadable }

    private fun applyOrder(order: List<String>, of: Map<String, String>) {
        plan = order + plan.filter { it !in order }  // задачи плана, которых сейчас нет в списке, — в конце
        planGroupOf = of.filterKeys { it in plan }
        store.plan = plan; store.planGroupOf = planGroupOf
        refreshWidget()
    }

    fun groupOf(id: String) = PlanGroups.groupOf(id, planGroups, planGroupOf)

    /** Сдвинуть задачу в плане (вверх −1, вниз +1); на границе блока — в соседний блок. */
    fun move(issue: Issue, delta: Int) {
        PlanGroups.step(dayOrder(), planGroups, planGroupOf, issue.idReadable, delta)?.let { applyOrder(it.first, it.second) }
    }

    fun canMove(issue: Issue, delta: Int) = PlanGroups.step(dayOrder(), planGroups, planGroupOf, issue.idReadable, delta) != null

    /** Перетаскивание на вкладке «День»: в блок [gid] перед задачей [before] (null — в конец блока). */
    fun drop(id: String, gid: String, before: String?) {
        val (order, of) = PlanGroups.drop(dayOrder(), planGroups, planGroupOf, id, gid, before)
        applyOrder(order, of)
    }

    /** «Переместить в блок»: задачи нет в плане — добавится. */
    fun moveToGroup(issue: Issue, gid: String) {
        if (planGroups.none { it.id == gid }) return
        val id = issue.idReadable
        if (id !in plan) plan = plan + id
        planGroupOf = planGroupOf + (id to gid)
        store.plan = plan; store.planGroupOf = planGroupOf
        refreshWidget()
    }

    /** Новый список блоков из редактора. Задачи удалённых блоков переходят в первый блок. */
    fun setGroups(groups: List<PlanGroup>) {
        val (g, of) = PlanGroups.normalize(groups, planGroupOf)
        planGroups = g; planGroupOf = of
        store.planGroups = g; store.planGroupOf = of
        refreshWidget()
    }

    fun clearPlan() {
        plan = emptyList(); done = emptySet(); planGroupOf = emptyMap()
        store.plan = plan; store.done = done; store.planGroupOf = planGroupOf
        refreshWidget()
    }

    private fun refreshWidget() {
        viewModelScope.launch { runCatching { TasksWidget().updateAllSafe(getApplication()) } }
    }

    // ---------------------------------------------------------------- открыть, быстрый просмотр
    fun issueUrl(issue: Issue) = Notifier.issueUrl(config.baseUrl, issue)

    fun open(context: Context, issue: Issue) {
        runCatching {
            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(issueUrl(issue))).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }.onFailure { message = "Не удалось открыть браузер" }
        markRead(issue)
    }

    private fun markRead(issue: Issue) {
        store.markRead(issue)
        seen = store.seen
    }

    fun toggleDetails(issue: Issue) {
        val id = issue.idReadable
        if (id in expanded) {
            expanded = expanded - id
            return
        }
        expanded = expanded + id
        markRead(issue)  // комментарии посмотрели прямо в приложении
        loadDescription(issue)
    }

    /** Загрузить описание задачи (один раз; при ошибке — повторить при следующем открытии). */
    private fun loadDescription(issue: Issue) {
        val id = issue.idReadable
        val cur = details[id]
        if (id !in details || cur?.startsWith(ERR) == true) {
            details[id] = null
            viewModelScope.launch {
                details[id] = try {
                    withContext(Dispatchers.IO) { YouTrackApi(config.baseUrl, store.token).description(issue) }
                } catch (e: Exception) {
                    log("Описание ${issue.idReadable}", e)
                    ERR + (e.message ?: "ошибка")
                }
            }
        }
    }

    // ---------------------------------------------------------------- смена статуса, приоритета, спринта
    suspend fun fieldOptions(issue: Issue, kind: FieldKind): FieldOptions = try {
        withContext(Dispatchers.IO) { YouTrackApi(config.baseUrl, store.token).fieldOptions(issue, kind) }
    } catch (e: Exception) {
        log("Варианты поля ${issue.idReadable} (${kind.what})", e)
        throw e
    }

    fun changeField(issue: Issue, kind: FieldKind, label: String, command: String) {
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { YouTrackApi(config.baseUrl, store.token).applyCommand(issue, command) }
                message = "✓ ${issue.idReadable}: ${kind.what} → $label"
                refresh()  // задача могла уйти из списка, если статус её закрывает
            } catch (e: Exception) {
                log("Смена поля ${issue.idReadable}: $command", e)
                message = "${issue.idReadable}: не удалось изменить ${kind.what} на «$label». ${e.message}"
            }
        }
    }

    // ---------------------------------------------------------------- комментарии
    fun saveDraft(issue: Issue, text: String) {
        drafts = if (text.isBlank()) drafts - issue.idReadable else drafts + (issue.idReadable to text)
        store.drafts = drafts
    }

    /** Отправить комментарий. null — успешно, иначе текст ошибки (текст в окне не теряется). */
    suspend fun sendComment(issue: Issue, text: String): String? {
        if (text.isBlank()) return "Введите текст комментария"
        return try {
            val c = withContext(Dispatchers.IO) { YouTrackApi(config.baseUrl, store.token).postComment(issue, text) }
            saveDraft(issue, "")
            commentAdded(issue, c.copy(login = c.login.ifEmpty { me }))
            loadRendered(issue, force = true)
            message = "${issue.idReadable}: комментарий добавлен"
            null
        } catch (e: Exception) {
            log("Комментарий ${issue.idReadable}", e)
            "Не отправлено: ${e.message ?: "ошибка"}"
        }
    }

    /** Комментарий виден сразу, не считается новым и не вызывает уведомление. */
    private fun commentAdded(issue: Issue, c: Comment) {
        fun add(list: List<Issue>) = list.map { if (it.idReadable == issue.idReadable) it.copy(comments = it.comments + c) else it }
        issues = add(issues)
        mentions = add(mentions)
        store.seen = store.seen + (issue.idReadable to maxOf(c.created, store.seen[issue.idReadable] ?: 0L))
        seen = store.seen
        store.notifyState?.let { s -> store.notifyState = s.copy(notified = s.notified + (issue.idReadable to maxOf(c.created, s.notified[issue.idReadable] ?: 0L))) }
    }

    // ---------------------------------------------------------------- настройки
    fun hasToken() = store.token.isNotBlank()
    fun watchError(): String? = store.watchError

    suspend fun testConnection(url: String, token: String, query: String): String = withContext(Dispatchers.IO) {
        try {
            val t = token.ifBlank { store.token }
            if (url.isBlank() || t.isBlank()) return@withContext "Укажите адрес и токен"
            val n = org.json.JSONArray(YouTrackApi(url, t).issuesJson(query, 100)).length()
            "✓ Подключено. Найдено задач по запросу: $n" + if (n >= 100) " (показаны первые 100)" else ""
        } catch (e: Exception) {
            log("Проверка подключения", e)
            "✗ " + (e.message ?: "ошибка")
        }
    }

    fun saveSettings(newCfg: Config, newToken: String) {
        val url = newCfg.baseUrl.trim().let { if (it.isNotEmpty() && !it.startsWith("http")) "https://$it" else it }
        val c = newCfg.copy(baseUrl = url, query = newCfg.query.ifBlank { Config.DEFAULT_QUERY })
        val old = store.config
        if (old.baseUrl != c.baseUrl || old.query != c.query) store.resetData()  // иначе «старые» задачи станут новыми
        if (old.mentionsQuery != c.mentionsQuery) store.resetMentions()
        if (old.watchQuery != c.watchQuery) store.resetWatch()
        store.config = c
        if (newToken.isNotBlank()) store.token = newToken.trim()
        config = c
        configured = store.configured
        details.clear()
        reloadFromStore()
        if (configured) refresh()
    }

    companion object {
        const val NO_PRIO = "(без приоритета)"
        const val NO_SPRINT = "(без спринта)"
        const val ERR = "\u0000"
    }
}

private fun Set<String>.toggle(v: String) = if (v in this) this - v else this + v
