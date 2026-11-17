package app.ytwidget.todo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PasteTest {
    @Test fun insertsAtCursor() {
        val r = Paste.into("Привет мир", 7, 7, "большой ", 100, false)!!
        assertEquals("Привет большой мир", r.value)
        assertEquals(15, r.cursor)
        assertTrue(!r.cut)
    }

    @Test fun replacesSelection() {
        val r = Paste.into("abc XXX def", 4, 7, "yyy", 100, false)!!
        assertEquals("abc yyy def", r.value)
        assertEquals(7, r.cursor)
        assertEquals("abc yyy def", Paste.into("abc XXX def", 7, 4, "yyy", 100, false)!!.value)  // выделение справа налево
    }

    @Test fun emptyClipboard() {
        assertNull(Paste.into("a", 1, 1, null, 10, false))
        assertNull(Paste.into("a", 1, 1, "", 10, false))
        assertNull(Paste.into("a", 1, 1, "\n\r\n", 10, singleLine = true))
    }

    @Test fun singleLineTitle() {
        val r = Paste.into("", 0, 0, "Первая строка\r\n  вторая\n\nтретья\n", Urgent.TITLE_MAX, singleLine = true)!!
        assertEquals("Первая строка вторая третья", r.value)
    }

    @Test fun multilineKeepsBreaks() {
        assertEquals("a\nb\nc", Paste.into("", 0, 0, "a\r\nb\rc", COMMENT_MAX, false)!!.value)
    }

    @Test fun cutByLimit() {
        val r = Paste.into("12345", 5, 5, "6789", 7, false)!!
        assertEquals("1234567", r.value)
        assertEquals(7, r.cursor)
        assertTrue(r.cut)
        val full = Paste.into("1234567", 3, 3, "x", 7, false)!!
        assertEquals("1234567", full.value)  // места нет — текст не меняется
        assertTrue(full.cut)
        assertEquals("12x67", Paste.into("12345 67".replace(" ", ""), 2, 5, "x", 7, false)!!.value)  // выделение освобождает место
    }

    @Test fun doesNotSplitEmoji() {
        val r = Paste.into("ab", 2, 2, "c😀", 4, false)!!
        assertEquals("abc", r.value)
        assertTrue(r.cut)
    }

    @Test fun cursorOutOfRangeIsClamped() {
        assertEquals("abX", Paste.into("ab", 10, 10, "X", 10, false)!!.value)
    }
}
