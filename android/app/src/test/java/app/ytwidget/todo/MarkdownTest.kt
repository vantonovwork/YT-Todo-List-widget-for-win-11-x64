package app.ytwidget.todo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MarkdownTest {
    private fun plain(spans: List<MdSpan>) = spans.joinToString("") { it.text }

    @Test fun headingsParagraphsRule() {
        val b = Markdown.parse("# Заголовок\nПервая строка\nвторая\n\n---\n### Мелкий ###")
        assertEquals(4, b.size)
        assertEquals(1, (b[0] as MdBlock.Heading).level)
        assertEquals("Первая строка\nвторая", plain((b[1] as MdBlock.Paragraph).text))
        assertEquals(MdBlock.Rule, b[2])
        assertEquals("Мелкий", plain((b[3] as MdBlock.Heading).text))
    }

    @Test fun inlineStyles() {
        val s = Markdown.inline("a **жирный** и *курсив*, ~~нет~~, `code_x` и snake_case_name")
        assertEquals("a жирный и курсив, нет, code_x и snake_case_name", plain(s))
        assertTrue(s.first { it.text == "жирный" }.bold)
        assertTrue(s.first { it.text == "курсив" }.italic)
        assertTrue(s.first { it.text == "нет" }.strike)
        assertTrue(s.first { it.text == "code_x" }.code)
        assertTrue(s.none { it.italic && "case" in it.text })  // _ внутри слова — не курсив
    }

    @Test fun nestedBoldInItalic() {
        val s = Markdown.inline("*a **b** c*")
        assertEquals("a b c", plain(s))
        assertTrue(s.all { it.italic })
        assertTrue(s.first { it.text == "b" }.bold)
    }

    @Test fun unmatchedMarkersStayText() {
        assertEquals("2 * 3 = 6 и **без пары", plain(Markdown.inline("2 * 3 = 6 и **без пары")))
    }

    @Test fun linksImagesAutolinks() {
        val s = Markdown.inline("См. [задачу](/issue/OE-1), https://ya.ru. и ![схема](x.png){width=70%}")
        assertEquals("/issue/OE-1", s.first { it.text == "задачу" }.url)
        assertEquals("https://ya.ru", s.first { it.text == "https://ya.ru" }.url)
        assertTrue(s.first { it.text == "схема" }.image)
        assertEquals("См. задачу, https://ya.ru. и схема", plain(s))
        assertEquals("https://yt.example.com/issue/OE-1", Markdown.absoluteUrl("/issue/OE-1", "https://yt.example.com/"))
    }

    @Test fun lists() {
        val b = Markdown.parse("- один\n  - вложенный\n- [x] сделано\n- [ ] нет\n1. первый\n2) второй")
        val items = (b.single() as MdBlock.Bullets).items
        assertEquals(listOf(0, 1, 0, 0, 0, 0), items.map { it.depth })
        assertEquals(listOf(null, null, true, false, null, null), items.map { it.checked })
        assertEquals(listOf("•", "•", "•", "•", "1.", "2."), items.map { it.marker })
        assertEquals("сделано", plain(items[2].text))
    }

    @Test fun table() {
        val b = Markdown.parse("Текст\n| Поле | Значение | Итог |\n|:---|:---:|---:|\n| a | **b** |\n| c \\| d | e | f |\nпосле")
        assertEquals(3, b.size)
        val t = b[1] as MdBlock.Table
        assertEquals(listOf("Поле", "Значение", "Итог"), t.header.map(::plain))
        assertEquals(listOf(MdAlign.START, MdAlign.CENTER, MdAlign.END), t.align)
        assertEquals(listOf("a", "b", ""), t.rows[0].map(::plain))  // короткая строка дополнена
        assertTrue(t.rows[0][1].single().bold)
        assertEquals("c | d", plain(t.rows[1][0]))
    }

    @Test fun codeAndQuote() {
        val b = Markdown.parse("```kotlin\nval x = **1**\n```\n> цитата **жирно**\n> ещё")
        val code = b[0] as MdBlock.Code
        assertEquals("kotlin", code.lang)
        assertEquals("val x = **1**", code.text)  // внутри кода разметку не трогаем
        val q = b[1] as MdBlock.Quote
        assertEquals("цитата жирно\nещё", plain((q.blocks.single() as MdBlock.Paragraph).text))
    }

    @Test fun htmlBreaksAndTags() {
        assertEquals("a\nb c", plain(Markdown.inline("a<br>b <span>c</span>")))
    }

    /** Длинный «плохой» текст разбирается на маленьком стеке, без переполнения и зависания. */
    @Test fun hugeTextOnSmallStack() {
        val samples = listOf(
            "[" + "a".repeat(50_000),                              // незакрытая скобка ссылки
            "[x](" + "b".repeat(50_000),                           // незакрытый адрес
            "*a ".repeat(10_000) + "**b ".repeat(10_000),          // маркеры без пары
            "_".repeat(20_000) + "~".repeat(20_000) + "`".repeat(5_000),
            "| a | b |\n|---|---|\n" + "| x | y |\n".repeat(3_000),
            "- пункт [ссылка](https://ya.ru) **жирно**\n".repeat(3_000),
            "https://" + "c".repeat(50_000),
        )
        var error: Throwable? = null
        val t = Thread(null, {
            try {
                samples.forEach { Markdown.parse(it) }
            } catch (e: Throwable) {
                error = e
            }
        }, "md", 256 * 1024)
        t.start()
        t.join(60_000)
        assertTrue("разбор завис", !t.isAlive)
        val failure = error
        if (failure != null) throw AssertionError("разбор упал", failure)
    }

    @Test fun bracketsWithoutLink() {
        assertEquals("[FRONT] Редизайн [x] и [ссылка]", plain(Markdown.inline("[FRONT] Редизайн [x] и [ссылка]")))
        val s = Markdown.inline("[a [b] c](/x)")
        assertEquals("/x", s.single().url)
        assertEquals("a [b] c", s.single().text)
    }
}
