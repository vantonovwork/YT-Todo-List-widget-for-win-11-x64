package app.ytwidget.todo

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.security.KeyStore
import java.time.LocalDate
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class Config(
    val baseUrl: String = "",
    val query: String = DEFAULT_QUERY,
    val stateField: String = "State",
    val priorityField: String = "Priority",
    val maxIssues: Int = 100,
    val notify: Boolean = true,
    /** Вкладка «Нужно внимание»: задачи, где вас упомянули; пусто — вкладка выключена. */
    val mentionsQuery: String = DEFAULT_MENTIONS,
    /** Задачи под наблюдением: уведомления о новых комментариях и смене статуса; пусто — выключено. */
    val watchQuery: String = DEFAULT_WATCH,
    val notifyMentions: Boolean = true,
    val notifyComments: Boolean = true,
    val notifyStatus: Boolean = true,
    val notifyAssigned: Boolean = true,
    /** Оформление Markdown в описании и комментариях; выключается само, если отрисовка разметки уронила приложение. */
    val markdown: Boolean = true,
) {
    fun allows(kind: NotifyKind) = notify && when (kind) {
        NotifyKind.MENTION -> notifyMentions
        NotifyKind.COMMENT -> notifyComments
        NotifyKind.STATUS -> notifyStatus
        NotifyKind.ASSIGNED -> notifyAssigned
    }

    val fieldNames get() = FieldNames(state = stateField, priority = priorityField)

    companion object {
        const val DEFAULT_QUERY = "Assignee: me #Unresolved sort by: Приоритет"
        const val DEFAULT_MENTIONS = "mentions: me #Unresolved"
        /** «Звёздочка» в YouTrack = подписка на задачу. Без #Unresolved — чтобы заметить и закрытие задачи. */
        const val DEFAULT_WATCH = "tag: Star sort by: updated"
    }
}

/** Всё, что приложение хранит на планшете. Токен — зашифрован ключом из Android Keystore. */
class Store(context: Context) {
    private val p: SharedPreferences = context.applicationContext.getSharedPreferences("ytw", Context.MODE_PRIVATE)

    // ---------------------------------------------------------------- настройки
    var config: Config
        get() = Config(
            baseUrl = p.getString("baseUrl", "").orEmpty(),
            query = p.getString("query", Config.DEFAULT_QUERY).orEmpty().ifBlank { Config.DEFAULT_QUERY },
            stateField = p.getString("stateField", "State").orEmpty(),
            priorityField = p.getString("priorityField", "Priority").orEmpty(),
            maxIssues = p.getInt("maxIssues", 100),
            notify = p.getBoolean("notify", true),
            mentionsQuery = p.getString("mentionsQuery", Config.DEFAULT_MENTIONS).orEmpty(),
            watchQuery = p.getString("watchQuery", Config.DEFAULT_WATCH).orEmpty(),
            notifyMentions = p.getBoolean("notifyMentions", true),
            notifyComments = p.getBoolean("notifyComments", true),
            notifyStatus = p.getBoolean("notifyStatus", true),
            notifyAssigned = p.getBoolean("notifyAssigned", true),
            markdown = p.getBoolean(AppLog.KEY_MARKDOWN, true),
        )
        set(c) = p.edit().putString("baseUrl", c.baseUrl).putString("query", c.query)
            .putString("stateField", c.stateField).putString("priorityField", c.priorityField)
            .putInt("maxIssues", c.maxIssues).putBoolean("notify", c.notify)
            .putString("mentionsQuery", c.mentionsQuery).putString("watchQuery", c.watchQuery)
            .putBoolean("notifyMentions", c.notifyMentions).putBoolean("notifyComments", c.notifyComments)
            .putBoolean("notifyStatus", c.notifyStatus).putBoolean("notifyAssigned", c.notifyAssigned)
            .putBoolean(AppLog.KEY_MARKDOWN, c.markdown).apply()

    var token: String
        get() = p.getString("token", null)?.let { runCatching { decrypt(it) }.getOrNull() }.orEmpty()
        set(v) = p.edit().putString("token", if (v.isEmpty()) null else encrypt(v)).apply()

    val configured: Boolean get() = config.baseUrl.isNotBlank() && token.isNotBlank()

    // ---------------------------------------------------------------- фильтры (запоминаются)
    var search: String
        get() = p.getString("search", "").orEmpty()
        set(v) = p.edit().putString("search", v).apply()
    var tab: String
        get() = p.getString("tab", "all").orEmpty()
        set(v) = p.edit().putString("tab", v).apply()
    var hotOnly: Boolean
        get() = p.getBoolean("hotOnly", false)
        set(v) = p.edit().putBoolean("hotOnly", v).apply()
    var prioFilter: Set<String>
        get() = p.getStringSet("prioFilter", emptySet()).orEmpty().toSet()
        set(v) = p.edit().putStringSet("prioFilter", v).apply()
    var sprintFilter: Set<String>
        get() = p.getStringSet("sprintFilter", emptySet()).orEmpty().toSet()
        set(v) = p.edit().putStringSet("sprintFilter", v).apply()

    // ---------------------------------------------------------------- прочитанные комментарии
    var seen: Map<String, Long>
        get() = p.getString("seen", null)?.let { s ->
            val o = JSONObject(s)
            o.keys().asSequence().associateWith { o.optLong(it) }
        } ?: emptyMap()
        set(m) = p.edit().putString("seen", JSONObject(m as Map<*, *>).toString()).apply()

    fun markRead(issue: Issue) {
        seen = seen + (issue.idReadable to maxOf(issue.lastCommentTime, seen[issue.idReadable] ?: 0L))
    }

    // ---------------------------------------------------------------- план на день
    var plan: List<String>
        get() = p.getString("plan", null)?.let { s -> JSONArray(s).let { a -> (0 until a.length()).map { a.getString(it) } } }
            ?: emptyList()
        set(v) = p.edit().putString("plan", JSONArray(v).toString()).apply()

    /** Отметки «сделано» действуют до конца дня. */
    var done: Set<String>
        get() {
            if (p.getString("doneDate", "") != LocalDate.now().toString()) return emptySet()
            return p.getStringSet("done", emptySet()).orEmpty().toSet()
        }
        set(v) = p.edit().putStringSet("done", v).putString("doneDate", LocalDate.now().toString()).apply()

    // ---------------------------------------------------------------- свои срочные задачи
    var urgent: List<UrgentTask>
        get() = Urgent.parse(p.getString("urgent", null))
        set(v) = p.edit().putString("urgent", Urgent.toJson(v)).apply()

    // ---------------------------------------------------------------- «Нужно внимание»
    var mentionsJson: String?
        get() = p.getString("mentionsJson", null)
        set(v) = p.edit().putString("mentionsJson", v).apply()

    /** idReadable → время упоминания, отмеченного вручную «отвечено» (как attention.json на десктопе). */
    var attn: Map<String, Long>
        get() = longMap("attn") ?: emptyMap()
        set(m) = p.edit().putString("attn", JSONObject(m as Map<*, *>).toString()).apply()

    /** idReadable → время последнего упоминания, о котором уже уведомили; null — ещё не загружали. */
    var mentionSeen: Map<String, Long>?
        get() = longMap("mentionSeen")
        set(m) = p.edit().putString("mentionSeen", m?.let { JSONObject(it as Map<*, *>).toString() }).apply()

    fun mentions(): List<Issue> =
        mentionsJson?.let { runCatching { Model.parseIssues(it, config.fieldNames) }.getOrNull() } ?: emptyList()

    /** Черновики комментариев: idReadable → текст. */
    var drafts: Map<String, String>
        get() = p.getString("drafts", null)?.let { s -> runCatching { JSONObject(s).let { o -> o.keys().asSequence().associateWith { o.optString(it) } } }.getOrNull() } ?: emptyMap()
        set(m) = p.edit().putString("drafts", JSONObject(m as Map<*, *>).toString()).apply()

    private fun longMap(key: String): Map<String, Long>? = p.getString(key, null)?.let { s ->
        runCatching { JSONObject(s).let { o -> o.keys().asSequence().associateWith { o.optLong(it) } } }.getOrNull()
    }

    // ---------------------------------------------------------------- кэш и уведомления
    var issuesJson: String?
        get() = p.getString("issuesJson", null)
        set(v) = p.edit().putString("issuesJson", v).apply()
    var me: String
        get() = p.getString("me", "").orEmpty()
        set(v) = p.edit().putString("me", v).apply()
    var lastOk: Long
        get() = p.getLong("lastOk", 0L)
        set(v) = p.edit().putLong("lastOk", v).apply()
    var lastError: String?
        get() = p.getString("lastError", null)
        set(v) = p.edit().putString("lastError", v).apply()

    var notifyState: NotifyState?
        get() = p.getString("notifyState", null)?.let { s ->
            val o = JSONObject(s)
            val n = o.getJSONObject("notified")
            val k = o.getJSONArray("known")
            NotifyState(n.keys().asSequence().associateWith { n.optLong(it) }, (0 until k.length()).map { k.getString(it) }.toSet())
        }
        set(v) = p.edit().putString("notifyState", v?.let {
            JSONObject().put("notified", JSONObject(it.notified as Map<*, *>)).put("known", JSONArray(it.known.toList())).toString()
        }).apply()

    fun issues(): List<Issue> = issuesJson?.let { runCatching { Model.parseIssues(it, config.fieldNames) }.getOrNull() } ?: emptyList()

    /** Задачи под наблюдением: статус и время последнего комментария на момент прошлой проверки. */
    var watchState: WatchState?
        get() = p.getString("watchState", null)?.let { s ->
            runCatching {
                val o = JSONObject(s)
                val st = o.getJSONObject("states")
                val cm = o.getJSONObject("comments")
                WatchState(st.keys().asSequence().associateWith { st.optString(it) }, cm.keys().asSequence().associateWith { cm.optLong(it) })
            }.getOrNull()
        }
        set(v) = p.edit().putString("watchState", v?.let {
            JSONObject().put("states", JSONObject(it.states as Map<*, *>)).put("comments", JSONObject(it.comments as Map<*, *>)).toString()
        }).apply()

    /** Ошибка запроса наблюдения (основной список при этом работает) — показываем в настройках. */
    var watchError: String?
        get() = p.getString("watchError", null)
        set(v) = p.edit().putString("watchError", v).apply()

    fun resetWatch() { p.edit().remove("watchState").remove("watchError").apply() }

    fun resetMentions() { p.edit().remove("mentionsJson").remove("mentionSeen").apply() }

    /** Смена адреса/запроса — сбросить кэш и состояние уведомлений, чтобы «старые» задачи не считались новыми. */
    fun resetData() {
        p.edit().remove("issuesJson").remove("notifyState").remove("lastOk").remove("lastError").remove("me")
            .remove("mentionsJson").remove("mentionSeen").remove("watchState").remove("watchError").apply()
    }

    // ---------------------------------------------------------------- шифрование токена
    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build())
        }.generateKey()
    }

    private fun encrypt(plain: String): String {
        val c = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val ct = c.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(c.iv, Base64.NO_WRAP) + ":" + Base64.encodeToString(ct, Base64.NO_WRAP)
    }

    private fun decrypt(stored: String): String {
        val (iv, ct) = stored.split(":").map { Base64.decode(it, Base64.NO_WRAP) }
        val c = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv)) }
        return String(c.doFinal(ct), Charsets.UTF_8)
    }

    companion object {
        private const val KEY_ALIAS = "ytw_token_key"
    }
}
