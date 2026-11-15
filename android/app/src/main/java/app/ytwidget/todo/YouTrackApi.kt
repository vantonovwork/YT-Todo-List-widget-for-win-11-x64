package app.ytwidget.todo

import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.URLEncoder
import java.net.UnknownHostException

class YTException(message: String) : Exception(message)

/** Ограничение длины комментария (как в десктопной версии). */
const val COMMENT_MAX = 10_000

/** Клиент REST API YouTrack. Методы блокирующие — вызывать из Dispatchers.IO. */
class YouTrackApi(baseUrl: String, private val token: String) {
    private val base = baseUrl.trim().trimEnd('/')

    private fun url(path: String, params: Map<String, String>): URL {
        val q = params.entries.joinToString("&") { (k, v) ->
            URLEncoder.encode(k, "UTF-8") + "=" + URLEncoder.encode(v, "UTF-8").replace("+", "%20")
        }
        return URL(base + path + if (q.isEmpty()) "" else "?$q")
    }

    private fun open(u: URL): HttpURLConnection = (u.openConnection() as HttpURLConnection).apply {
        connectTimeout = 20_000
        readTimeout = 25_000
        setRequestProperty("Authorization", "Bearer $token")
        setRequestProperty("Accept", "application/json")
    }

    private fun errorDescription(c: HttpURLConnection): String = try {
        c.errorStream?.bufferedReader()?.use { JSONObject(it.readText()).optString("error_description") }.orEmpty()
    } catch (e: Exception) {
        ""
    }

    private fun <T> network(block: () -> T): T = try {
        block()
    } catch (e: YTException) {
        throw e
    } catch (e: UnknownHostException) {
        throw YTException("Адрес «${runCatching { URL(base).host }.getOrDefault(base)}» не найден. " +
            "Проверьте адрес YouTrack или подключение к сети")
    } catch (e: SocketTimeoutException) {
        throw YTException("Сервер не ответил вовремя")
    } catch (e: javax.net.ssl.SSLException) {
        throw YTException("Не удалось проверить HTTPS-сертификат сервера. Если YouTrack работает на внутреннем " +
            "сертификате компании, установите корневой сертификат на планшет (Настройки → Безопасность → " +
            "Шифрование и учётные данные → Установить сертификат)")
    } catch (e: IOException) {
        throw YTException("Нет соединения: ${e.message ?: e.javaClass.simpleName}")
    }

    fun get(path: String, params: Map<String, String> = emptyMap()): String = network {
        val c = open(url(path, params))
        try {
            when (val code = c.responseCode) {
                in 200..299 -> c.inputStream.bufferedReader().use { it.readText() }.also { body ->
                    val s = body.trimStart()
                    if (!(s.startsWith("{") || s.startsWith("["))) throw YTException("Сервер вернул не JSON — проверьте адрес")
                }
                401, 403 -> throw YTException("Доступ запрещён: проверьте токен")
                404 -> throw YTException("Не найдено: проверьте адрес YouTrack")
                400 -> throw YTException("Ошибка в запросе: ${errorDescription(c).ifEmpty { "HTTP 400" }}")
                else -> throw YTException("Ошибка сервера: HTTP $code")
            }
        } finally {
            c.disconnect()
        }
    }

    fun post(path: String, body: JSONObject, params: Map<String, String> = emptyMap()): String = network {
        val c = open(url(path, params))
        try {
            c.requestMethod = "POST"
            c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json")
            c.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            when (val code = c.responseCode) {
                in 200..299 -> c.inputStream.bufferedReader().use { it.readText() }
                401, 403 -> throw YTException(errorDescription(c).ifEmpty { "Нет прав на изменение задачи" })
                else -> throw YTException(errorDescription(c).ifEmpty { "Ошибка сервера: HTTP $code" })
            }
        } finally {
            c.disconnect()
        }
    }

    private fun json(text: String): JSONObject = try {
        JSONObject(text)
    } catch (e: JSONException) {
        throw YTException("Сервер вернул не JSON — проверьте адрес")
    }

    fun me(): String = json(get("/api/users/me", mapOf("fields" to "login"))).optString("login")

    /** Сырые JSON задач (кэшируются как есть и разбираются Model.parseIssues). */
    fun issuesJson(query: String, top: Int): String {
        val raw = get("/api/issues", mapOf("query" to query, "fields" to Model.ISSUE_FIELDS, "\$top" to top.toString()))
        try {
            JSONArray(raw)
        } catch (e: JSONException) {
            throw YTException("Сервер вернул не JSON — проверьте адрес")
        }
        return raw
    }

    fun description(issue: Issue): String =
        json(get("/api/issues/${issue.id}", mapOf("fields" to "description"))).let {
            if (it.isNull("description")) "" else it.optString("description")
        }

    /** Варианты для плашки статуса / приоритета / спринта (см. Model.fieldOptions). */
    fun fieldOptions(issue: Issue, kind: FieldKind): FieldOptions {
        val f = when (kind) {
            FieldKind.STATE -> issue.stateField
            FieldKind.PRIORITY -> issue.priorityField
            FieldKind.SPRINT -> issue.sprintField
        } ?: throw YTException("Не найдено поле: ${kind.what}")
        val data = json(get("/api/issues/${issue.id}/customFields/${f.id}", mapOf("fields" to
            "possibleEvents(id,presentation),projectCustomField(bundle(values(name,archived,released,releaseDate)))")))
        return Model.fieldOptions(kind, f, Model.currentValues(issue, kind), data, System.currentTimeMillis())
    }

    /** Варианты смены статуса: (подпись, команда) — только разрешённые переходы workflow. */
    fun stateOptions(issue: Issue): List<Pair<String, String>> =
        fieldOptions(issue, FieldKind.STATE).options.map { it.label to it.command }

    /** Добавить комментарий; возвращает созданный комментарий. */
    fun postComment(issue: Issue, text: String): Comment {
        val t = text.trim()
        if (t.isEmpty()) throw YTException("Пустой комментарий")
        val res = post("/api/issues/${issue.id}/comments", JSONObject().put("text", t.take(COMMENT_MAX)),
            mapOf("fields" to "id,created,deleted,text,author(login,fullName)"))
        return runCatching { Model.parseComment(JSONObject(res)) }.getOrNull()?.takeIf { it.created > 0 }
            ?: Comment(System.currentTimeMillis(), "", "", t)
    }

    fun applyCommand(issue: Issue, command: String) {
        post("/api/commands", JSONObject().put("query", command)
            .put("issues", JSONArray().put(JSONObject().put("id", issue.id))))
    }
}
