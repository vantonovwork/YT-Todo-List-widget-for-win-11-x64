package app.ytwidget.todo

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.action.ActionParameters
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.lazy.LazyColumn
import androidx.glance.appwidget.lazy.items
import androidx.glance.appwidget.provideContent
import androidx.glance.appwidget.updateAll
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.width
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider

/** Данные для виджета на главном экране: план на день (несделанное), а если он пуст — все задачи. */
private data class WidgetItem(val id: String, val summary: String, val state: String?, val bar: Long?, val url: String, val newComments: Int)
private data class WidgetData(val title: String, val items: List<WidgetItem>, val total: Int, val error: String?)

private fun loadWidgetData(context: Context): WidgetData {
    val store = Store(context)
    if (!store.configured) return WidgetData("YouTrack", emptyList(), 0, "Откройте приложение и укажите адрес и токен")
    val issues = store.issues()
    val plan = store.plan
    val done = store.done
    val mine = issues.map { it.idReadable }.toSet()
    val withMentions = issues + store.mentions().filter { it.idReadable !in mine }  // чужие задачи из «Нужно внимание» тоже бывают в плане
    val planned = Model.planOrder(withMentions, plan, done, store.planGroups, store.planGroupOf).filter { it.idReadable !in done }
    val (title, list) = if (planned.isNotEmpty()) "На день" to planned else "Мои задачи" to issues
    val seen = store.seen
    val me = store.me
    val base = store.config.baseUrl
    return WidgetData(title, list.take(30).map {
        WidgetItem(it.idReadable, it.summary, it.state?.text, it.barColor, Notifier.issueUrl(base, it), Model.newComments(it, seen[it.idReadable], me))
    }, list.size, store.lastError)
}

class TasksWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val data = loadWidgetData(context)
        provideContent { Content(context, data) }
    }

    @Composable
    private fun Content(context: Context, data: WidgetData) {
        val bg = ColorProvider(Color(0xF01F1F1F))
        val card = ColorProvider(Color(0xFF2B2B2B))
        val text = ColorProvider(Color(0xFFE8E8E8))
        val sub = ColorProvider(Color(0xFF9D9D9D))
        Column(GlanceModifier.fillMaxSize().background(bg).cornerRadius(16.dp).padding(10.dp)) {
            Row(GlanceModifier.fillMaxWidth().padding(bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("YT", style = TextStyle(color = ColorProvider(Color(0xFFB39DFF)), fontWeight = FontWeight.Bold, fontSize = 15.sp),
                    modifier = GlanceModifier.clickable(actionStartActivity(Intent(context, MainActivity::class.java))))
                Text("  ${data.title} · ${data.total}", style = TextStyle(color = text, fontWeight = FontWeight.Bold, fontSize = 14.sp),
                    modifier = GlanceModifier.defaultWeight().clickable(actionStartActivity(Intent(context, MainActivity::class.java))))
                Text("⟳", style = TextStyle(color = text, fontSize = 20.sp),
                    modifier = GlanceModifier.padding(horizontal = 8.dp).clickable(actionRunCallback<RefreshAction>()))
            }
            if (data.items.isEmpty()) {
                Text(data.error ?: "Задач нет", style = TextStyle(color = sub, fontSize = 13.sp), modifier = GlanceModifier.padding(8.dp))
            } else {
                LazyColumn(GlanceModifier.fillMaxSize()) {
                    items(data.items, itemId = { it.id.hashCode().toLong() }) { item ->
                        Column(GlanceModifier.fillMaxWidth().padding(bottom = 4.dp)) {
                            Row(
                                GlanceModifier.fillMaxWidth().background(card).cornerRadius(6.dp)
                                    .clickable(actionStartActivity(Intent(Intent.ACTION_VIEW, Uri.parse(item.url)))),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Box(GlanceModifier.width(4.dp).height(44.dp)
                                    .background(ColorProvider(item.bar?.let { Color(it.toInt()) } ?: Color(0xFF3A3A3A)))) {}
                                Column(GlanceModifier.defaultWeight().padding(horizontal = 8.dp, vertical = 4.dp)) {
                                    Text(item.id + (item.state?.let { " · $it" } ?: "") + if (item.newComments > 0) "  💬 ${item.newComments}" else "",
                                        style = TextStyle(color = sub, fontSize = 11.sp), maxLines = 1)
                                    Text(item.summary, style = TextStyle(color = text, fontSize = 13.sp), maxLines = 2)
                                }
                            }
                            Spacer(GlanceModifier.height(2.dp))
                        }
                    }
                }
            }
        }
    }
}

class TasksWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = TasksWidget()
}

/** Кнопка ⟳ на виджете: загрузить свежие задачи и перерисовать. */
class RefreshAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        Sync.run(context)
        TasksWidget().updateAll(context)
    }
}

suspend fun GlanceAppWidget.updateAllSafe(context: Context) {
    runCatching { updateAll(context) }
}
