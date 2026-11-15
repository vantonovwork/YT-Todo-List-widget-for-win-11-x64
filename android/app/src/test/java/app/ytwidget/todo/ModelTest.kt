package app.ytwidget.todo

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZoneOffset

class ModelTest {
    private val zone: ZoneId = ZoneOffset.UTC
    private val today: LocalDate = LocalDate.of(2026, 11, 13)  // пятница
    private fun ms(days: Long) = today.plusDays(days).atTime(12, 0).toInstant(ZoneOffset.UTC).toEpochMilli()
    private val now = ms(0)

    private fun field(name: String, type: String, value: Any?) =
        JSONObject().put("id", "f-$name").put("name", name).put("\$type", type).put("value", value ?: JSONObject.NULL)

    private fun enumV(name: String, bg: String? = null, fg: String? = null) = JSONObject().put("name", name).apply {
        if (bg != null) put("color", JSONObject().put("background", bg).put("foreground", fg ?: "#fff"))
    }

    private fun comment(t: Long, login: String = "ilya", text: String = "Посмотрел **маппинг**") =
        JSONObject().put("created", t).put("deleted", false).put("text", text)
            .put("author", JSONObject().put("login", login).put("fullName", "Илья Гаврилов"))

    private fun issue(id: String, vararg fields: JSONObject, comments: List<JSONObject> = emptyList()) =
        JSONObject().put("id", "2-$id").put("idReadable", id).put("summary", "Задача $id")
            .put("customFields", JSONArray(fields.toList())).put("comments", JSONArray(comments))

    private fun parse(o: JSONObject, names: FieldNames = FieldNames()) = Model.parseIssue(o, names, now)

    @Test fun stateAutodetectByWorkflowType() {
        val i = parse(issue("OE-1", field("Статус задачи", "StateMachineIssueCustomField", enumV("Анализ"))))
        assertEquals("Анализ", i.state?.text)
        assertEquals("Статус задачи", i.stateFieldName)
        assertEquals("f-Статус задачи", i.stateFieldId)
    }

    @Test fun priorityByNameAndColors() {
        val i = parse(issue("OE-1", field("Приоритет", "SingleEnumIssueCustomField", enumV("Высокий", "#ff7a00", "#fff"))))
        assertEquals(Chip("Высокий", 0xFFFF7A00, 0xFFFFFFFF), i.priority)
        assertEquals(0xFFFF7A00, i.barColor)
    }

    @Test fun exactFieldNameWins() {
        val i = parse(issue("OE-1", field("State", "StateIssueCustomField", enumV("Open")),
            field("Статус задачи", "StateMachineIssueCustomField", enumV("Анализ"))))
        assertEquals("Open", i.state?.text)
    }

    @Test fun chipColors() {
        assertEquals(Chip("x"), Model.chipColors("x", "#ffffff", "#000"))           // слишком светлый → нейтральный
        assertEquals(Chip("x", 0xFFE30000, 0xFFFFFFFF), Model.chipColors("x", "#e30000", "#fff"))
        assertEquals(Chip("Критический", 0xFFD32F2F, 0xFFFFFFFF), Model.chipColors("Критический", null, null))
        assertEquals(Chip("x"), Model.chipColors("x", "zzz", null))
        assertEquals(0xFFAABBCC, Model.parseHex("#abc"))
    }

    @Test fun emptyAndListValues() {
        val i = parse(issue("OE-1", field("Приоритет", "SingleEnumIssueCustomField", null)))
        assertNull(i.priority)
        val f = field("Компонент", "MultiEnumIssueCustomField", JSONArray().put(enumV("a")).put(enumV("b")))
        assertEquals("a, b", Model.valueText(f))
    }

    @Test fun dueLabels() {
        val cases = mapOf(-3L to ("просрочено" to true), 0L to ("сегодня" to true), 1L to ("завтра" to true), 2L to ("до " to false))
        for ((d, exp) in cases) {
            val info = Model.dueInfo(ms(d), today, zone)!!
            assertTrue("$d: ${info.label}", info.label.startsWith(exp.first))
            assertEquals(exp.second, info.hot)
        }
        assertEquals("до чт, 19.11", Model.dueInfo(ms(6), today, zone)!!.label)
        assertTrue(Model.dueInfo(ms(400), today, zone)!!.label.endsWith(".27"))
        assertNull(Model.dueInfo(null, today, zone))
    }

    @Test fun dueFieldDetection() {
        val due = parse(issue("OE-1", field("Дата окончания", "DateIssueCustomField", ms(1))))
        assertEquals(ms(1), due.dueMillis)
        val start = parse(issue("OE-1", field("Дата начала", "DateIssueCustomField", ms(1))))
        assertNull("«начала» — не срок", start.dueMillis)
    }

    @Test fun sprints() {
        val cur = JSONObject().put("name", "26.22").put("archived", false).put("released", false).put("releaseDate", ms(10))
        val old = JSONObject().put("name", "26.20").put("archived", true).put("released", false).put("releaseDate", ms(-20))
        val i = parse(issue("OE-1", field("Спринт", "MultiVersionIssueCustomField", JSONArray().put(old).put(cur))))
        assertEquals(listOf(Sprint("26.22", true), Sprint("26.20", false)), i.sprints)
        val numeric = parse(issue("OE-2", field("Дата добавления в спринт", "SimpleIssueCustomField", 5)))
        assertTrue(numeric.sprints.isEmpty())
    }

    @Test fun cleanMarkdown() {
        val s = Model.cleanMarkdown("**Жирный** [ссылка](http://x) ![img](a.png)\n\n\n```\ncode\n```\n> цитата", 500)
        assertEquals("Жирный ссылка [картинка]\n[код]\nцитата", s)
        assertEquals(10, Model.cleanMarkdown("а".repeat(50), 10).length)
        assertTrue(Model.cleanMarkdown("а".repeat(50), 10).endsWith("…"))
        assertEquals("", Model.cleanMarkdown(null, 10))
        assertEquals("Смотри лог важно", Model.commentSnippet(Comment(0, "", "", "Смотри [лог](http://x)\n**важно**")))
    }

    @Test fun deletedCommentsIgnoredAndNewCount() {
        val o = issue("OE-1", comments = listOf(comment(1000), comment(2000, "me"), comment(3000).put("deleted", true)))
        val i = parse(o)
        assertEquals(2, i.comments.size)
        assertEquals(2000L, i.lastCommentTime)
        assertEquals(1, Model.newComments(i, 0, "me"))   // свои не считаются
        assertEquals(0, Model.newComments(i, null, "me")) // задачу ещё не видели — новых нет
    }

    @Test fun notificationEvents() {
        val base = listOf(parse(issue("OE-1", comments = listOf(comment(1000)))), parse(issue("OE-2")))
        val (first, s1) = Model.computeEvents(base, null, emptyMap(), "me")
        assertTrue("первая загрузка — без уведомлений", first.isEmpty())

        val withOwn = listOf(parse(issue("OE-1", comments = listOf(comment(1000), comment(5000, "me")))), parse(issue("OE-2")))
        val (own, s2) = Model.computeEvents(withOwn, s1, emptyMap(), "me")
        assertTrue("свой комментарий — без уведомления", own.isEmpty())

        val next = listOf(parse(issue("OE-1", comments = listOf(comment(1000), comment(5000, "me"), comment(6000)))),
            parse(issue("OE-2")), parse(issue("OE-9")))
        val (events, s3) = Model.computeEvents(next, s2, emptyMap(), "me")
        assertEquals(2, events.size)
        assertTrue(events[0] is Event.NewComments && (events[0] as Event.NewComments).comments.single().created == 6000L)
        assertTrue(events[1] is Event.NewIssue && (events[1] as Event.NewIssue).issue.idReadable == "OE-9")

        val (again, _) = Model.computeEvents(next, s3, emptyMap(), "me")
        assertTrue("об одном и том же — один раз", again.isEmpty())

        // прочитано в приложении — уведомления о старом не будет
        val (afterRead, _) = Model.computeEvents(next, s2, mapOf("OE-1" to 6000L), "me")
        assertFalse(afterRead.any { it is Event.NewComments })
    }

    @Test fun planOrderDoneLast() {
        val list = listOf("A", "B", "C", "D").map { parse(issue(it)) }
        val ordered = Model.planOrder(list, listOf("C", "A", "B"), setOf("C"))
        assertEquals(listOf("A", "B", "C"), ordered.map { it.idReadable })
    }

    @Test fun urgentAddEditDoneOrder() {
        var list = Urgent.add(emptyList(), "  Позвонить поставщику  ", "текст", 1000, "U-1")
        list = Urgent.add(list, "Отчёт", "", 2000, "U-2")
        list = Urgent.add(list, "   ", "пустое название не добавляется", 3000, "U-3")
        assertEquals(listOf("U-1", "U-2"), list.map { it.id })
        assertEquals("Позвонить поставщику", list[0].title)
        assertEquals(listOf("U-2", "U-1"), Urgent.ordered(list).map { it.id })       // новые сверху
        list = Urgent.toggleDone(list, "U-2", 5000)
        assertEquals(listOf("U-1", "U-2"), Urgent.ordered(list).map { it.id })       // выполненные — в конце
        assertEquals(5000L, list.first { it.id == "U-2" }.doneAt)
        list = Urgent.toggleDone(list, "U-2", 6000)
        assertEquals(0L, list.first { it.id == "U-2" }.doneAt)
        list = Urgent.update(list, "U-1", "Новое", "x".repeat(2000))
        assertEquals(Urgent.TEXT_MAX, list.first { it.id == "U-1" }.text.length)
        assertEquals("Новое", Urgent.update(list, "U-1", "  ", "y").first { it.id == "U-1" }.title)  // пустое — не меняем
        list = Urgent.toggleDone(list, "U-1", 7000)
        assertEquals(listOf("U-2"), Urgent.clearDone(list).map { it.id })
        assertEquals(listOf("U-2"), Urgent.delete(list, "U-1").map { it.id })
    }

    @Test fun urgentJsonCompatibleWithDesktop() {
        val desktop = """[{"id": "U-ab12cd34", "title": "${"t".repeat(150)}", "text": "текст", "created": 1700000000000,
            "done": true, "done_at": 1700000100000}, {"id": "", "title": "без id"}, {"id": "U-2", "title": "  "}]"""
        val list = Urgent.parse(desktop)
        assertEquals(1, list.size)
        assertEquals(Urgent.TITLE_MAX, list[0].title.length)
        assertTrue(list[0].done)
        assertEquals(1700000100000L, list[0].doneAt)
        assertEquals(list, Urgent.parse(Urgent.toJson(list)))
        assertTrue(Urgent.parse("{битый").isEmpty())
        assertTrue(Urgent.parse(null).isEmpty())
    }

    private fun bundle(vararg values: JSONObject) =
        JSONObject().put("projectCustomField", JSONObject().put("bundle", JSONObject().put("values", JSONArray(values.toList()))))

    @Test fun fieldRefsParsed() {
        val i = parse(issue("OE-1", field("Приоритет", "SingleEnumIssueCustomField", null),
            field("Спринт", "MultiVersionIssueCustomField", JSONArray())))
        assertEquals(FieldRef("f-Приоритет", "Приоритет", "SingleEnumIssueCustomField"), i.priorityField)
        assertEquals("f-Спринт", i.sprintField?.id)   // поле есть, хотя спринт не задан — можно назначить
        assertTrue(i.sprints.isEmpty())
    }

    @Test fun stateOptionsWithoutBraces() {
        val sm = FieldRef("f1", "Статус задачи", "StateMachineIssueCustomField")
        val ev = JSONObject().put("possibleEvents", JSONArray().put(JSONObject().put("id", "to-test").put("presentation", "На тест")))
        assertEquals(listOf(FieldOption("На тест", "Статус задачи to-test", false)),
            Model.fieldOptions(FieldKind.STATE, sm, listOf("Анализ"), ev, now).options)
        val plain = FieldRef("f2", "State", "StateIssueCustomField")
        val opts = Model.fieldOptions(FieldKind.STATE, plain, listOf("Open"),
            bundle(enumV("Open"), enumV("In Progress"), enumV("Old").put("archived", true)), now).options
        assertEquals(listOf(FieldOption("In Progress", "State In Progress", false)), opts)  // без {…}, без текущего и архивных
    }

    @Test fun priorityOptions() {
        val f = FieldRef("f", "Приоритет", "SingleEnumIssueCustomField")
        val opts = Model.fieldOptions(FieldKind.PRIORITY, f, listOf("Высокий"), bundle(enumV("Высокий"), enumV("Низкий")), now).options
        assertEquals(listOf(FieldOption("Высокий", "Приоритет Высокий", true), FieldOption("Низкий", "Приоритет Низкий", false)), opts)
    }

    @Test fun sprintOptionsMoveAndRemove() {
        val f = FieldRef("f", "Спринт", "MultiVersionIssueCustomField")
        fun v(n: String, rel: Long, released: Boolean = false) = JSONObject().put("name", n).put("releaseDate", rel).put("released", released)
        val data = bundle(v("26.24", ms(20)), v("26.22", ms(5)), v("26.10", ms(-90), released = true), v("26.20", ms(-3)))
        val opts = Model.fieldOptions(FieldKind.SPRINT, f, listOf("26.20"), data, now).options
        assertEquals(listOf("26.20", "26.22", "26.24"), opts.map { it.label })  // текущий + активные по дате
        assertEquals(FieldOption("26.20", "remove Спринт 26.20", true), opts[0])
        assertEquals("remove Спринт 26.20 add Спринт 26.22", opts[1].command)
        assertEquals("add Спринт 26.24", Model.fieldOptions(FieldKind.SPRINT, f, emptyList(), data, now).options.last().command)
    }

    @Test fun mentionInfoAndAttention() {
        val i = parse(issue("OE-1", comments = listOf(
            comment(1000, "ilya", "@vlad посмотри"), comment(2000, "ilya", "@vladimir это не тебе"),
            comment(3000, "olga", "Повторю: @Vlad, срочно"))))
        val m = Model.mentionInfo(i, "vlad")!!
        assertEquals(3000L, m.time)
        assertEquals(2, m.count)
        assertFalse(m.replied)
        assertTrue(Model.attnWaiting(m, null))
        assertFalse("отмечено вручную", Model.attnWaiting(m, 3000L))
        assertTrue("новое упоминание после отметки", Model.attnWaiting(m, 1000L))
        val answered = parse(issue("OE-1", comments = listOf(comment(1000, "ilya", "@vlad ?"), comment(1500, "vlad", "готово"))))
        assertTrue(Model.mentionInfo(answered, "vlad")!!.replied)
        assertNull(Model.mentionInfo(i, ""))
        assertNull(Model.mentionInfo(parse(issue("OE-2", comments = listOf(comment(1, text = "mail@vlad.kz")))), "vlad.k"))
        assertTrue(Model.mentions("привет @VLAD", "vlad"))
    }

    @Test fun mentionNotifications() {
        val a = parse(issue("OE-5", comments = listOf(comment(1000, "ilya", "@vlad глянь"))))
        val (first, s1) = Model.mentionEvents(listOf(a), emptySet(), null, "vlad")
        assertTrue("первая загрузка — без уведомлений", first.isEmpty())
        val a2 = parse(issue("OE-5", comments = listOf(comment(1000, "ilya", "@vlad глянь"), comment(4000, "olga", "@vlad ну?"))))
        val b = parse(issue("OE-6", comments = listOf(comment(5000, "olga", "@vlad тут тоже"))))
        val own = parse(issue("OE-7", comments = listOf(comment(6000, "olga", "@vlad своя"))))
        val (ev, s2) = Model.mentionEvents(listOf(a2, b, own), setOf("OE-7"), s1, "vlad")
        assertEquals(listOf("OE-5", "OE-6"), ev.map { it.issue.idReadable })  // о своей задаче — через уведомление о комментарии
        assertTrue(Model.mentionEvents(listOf(a2, b, own), setOf("OE-7"), s2, "vlad").first.isEmpty())
    }

    @Test fun parseCreatedComment() {
        val c = Model.parseComment(comment(7000, "vlad", "ок"))
        assertEquals(Comment(7000, "vlad", "Илья Гаврилов", "ок"), c)
    }

    @Test fun parseIssuesFromJsonArray() {
        val arr = JSONArray().put(issue("OE-1")).put(issue("OE-2"))
        assertEquals(listOf("OE-1", "OE-2"), Model.parseIssues(arr.toString(), FieldNames(), now).map { it.idReadable })
    }

    // ---------------------------------------------------------------- наблюдение и типы уведомлений
    private fun st(name: String) = field("State", "StateMachineIssueCustomField", enumV(name))

    @Test fun watchStatusAndComments() {
        val a = parse(issue("OE-10", st("Открыта"), comments = listOf(comment(1000))))
        val (first, w1) = Model.watchEvents(listOf(a), null, emptySet(), emptyMap(), "vlad")
        assertTrue("первая загрузка — без уведомлений", first.isEmpty())
        val a2 = parse(issue("OE-10", st("В работе"), comments = listOf(comment(1000), comment(2000, "olga", "Готово"),
            comment(3000, "vlad", "Спасибо"))))
        val fresh = parse(issue("OE-11", st("Открыта"), comments = listOf(comment(500))))  // только что поставили звёздочку
        val (ev, w2) = Model.watchEvents(listOf(a2, fresh), w1, emptySet(), emptyMap(), "vlad")
        assertEquals(2, ev.size)
        val sc = ev.filterIsInstance<Event.StateChanged>().single()
        assertEquals("Открыта" to "В работе", sc.from to sc.to)
        val nc = ev.filterIsInstance<Event.NewComments>().single()
        assertEquals(listOf("olga"), nc.comments.map { it.login })  // свой комментарий не в счёт
        assertTrue("повторная проверка — тишина", Model.watchEvents(listOf(a2, fresh), w2, emptySet(), emptyMap(), "vlad").first.isEmpty())
    }

    @Test fun watchSkipsCommentsOfOwnIssuesAndReadOnes() {
        val a = parse(issue("OE-12", st("Открыта"), comments = listOf(comment(1000))))
        val (_, w1) = Model.watchEvents(listOf(a), null, emptySet(), emptyMap(), "vlad")
        val a2 = parse(issue("OE-12", st("Открыта"), comments = listOf(comment(1000), comment(2000))))
        // своя задача — о комментарии уже сообщил computeEvents
        assertTrue(Model.watchEvents(listOf(a2), w1, setOf("OE-12"), emptyMap(), "vlad").first.isEmpty())
        // прочитана в приложении после комментария
        assertTrue(Model.watchEvents(listOf(a2), w1, emptySet(), mapOf("OE-12" to 2500L), "vlad").first.isEmpty())
    }

    @Test fun whoChangedState() {
        val json = JSONArray()
            .put(JSONObject().put("timestamp", 100).put("author", JSONObject().put("login", "olga").put("fullName", "Ольга"))
                .put("field", JSONObject().put("name", "State")).put("added", JSONArray().put(JSONObject().put("name", "В работе"))))
            .put(JSONObject().put("timestamp", 200).put("author", JSONObject().put("login", "vlad").put("fullName", "Влад"))
                .put("field", JSONObject().put("name", "Приоритет")).put("added", JSONArray().put(JSONObject().put("name", "Высокий"))))
            .toString()
        val changes = Model.parseFieldChanges(json)
        assertEquals(200L, changes.first().time)  // новые первыми
        assertEquals("Ольга", Model.whoChanged(changes, "state", "В работе")?.author)
        assertEquals(null, Model.whoChanged(changes, "State", "Готово"))
    }

    @Test fun notifyKinds() {
        val i = parse(issue("OE-13"))
        val c = Comment(1, "olga", "Ольга", "@vlad посмотри")
        assertEquals(NotifyKind.MENTION, Model.kindOf(Event.NewComments(i, listOf(c)), "vlad"))
        assertEquals(NotifyKind.COMMENT, Model.kindOf(Event.NewComments(i, listOf(c.copy(text = "ок"))), "vlad"))
        assertEquals(NotifyKind.STATUS, Model.kindOf(Event.StateChanged(i, "A", "B"), "vlad"))
        assertEquals(NotifyKind.ASSIGNED, Model.kindOf(Event.NewIssue(i), "vlad"))
        val cfg = Config(notifyStatus = false)
        assertTrue(cfg.allows(NotifyKind.MENTION))
        assertTrue(!cfg.allows(NotifyKind.STATUS))
        assertTrue(!Config(notify = false).allows(NotifyKind.MENTION))
    }
}
