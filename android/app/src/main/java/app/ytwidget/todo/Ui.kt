@file:OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)

package app.ytwidget.todo

import androidx.activity.compose.BackHandler
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.ui.draw.clip
import androidx.compose.material.icons.rounded.AddComment
import androidx.compose.material.icons.rounded.AlternateEmail
import androidx.compose.material.icons.rounded.ArrowDropDown
import androidx.compose.material.icons.rounded.Check
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.rounded.Add
import androidx.compose.material.icons.rounded.Bolt
import androidx.compose.material.icons.rounded.DeleteOutline
import androidx.compose.material.icons.rounded.DeleteSweep
import androidx.compose.material.icons.rounded.Edit
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.automirrored.rounded.OpenInNew
import androidx.compose.material.icons.rounded.ChatBubble
import androidx.compose.material.icons.rounded.ChatBubbleOutline
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.FilterList
import androidx.compose.material.icons.rounded.KeyboardArrowDown
import androidx.compose.material.icons.rounded.KeyboardArrowUp
import androidx.compose.material.icons.rounded.MoreVert
import androidx.compose.material.icons.rounded.RadioButtonUnchecked
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material.icons.rounded.Star
import androidx.compose.material.icons.rounded.StarBorder
import androidx.compose.material.icons.rounded.WbSunny
import androidx.compose.material.icons.rounded.Whatshot
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

// ---------------------------------------------------------------- тема (как у десктопной версии)
private val Brand = Color(0xFF7C4DFF)

@Composable
fun YtTheme(content: @Composable () -> Unit) {
    val scheme = if (isSystemInDarkTheme())
        darkColorScheme(primary = Color(0xFFB39DFF), secondary = Color(0xFF4CC2FF), background = Color(0xFF1F1F1F),
            surface = Color(0xFF1F1F1F), surfaceVariant = Color(0xFF2B2B2B), surfaceContainer = Color(0xFF2B2B2B))
    else
        lightColorScheme(primary = Brand, secondary = Color(0xFF0067C0), background = Color(0xFFF3F4F6),
            surface = Color(0xFFF3F4F6), surfaceVariant = Color(0xFFE3E5E8), surfaceContainer = Color.White)
    MaterialTheme(colorScheme = scheme, content = content)
}

private fun argb(v: Long) = Color(v.toInt())

@Composable
fun App(vm: MainViewModel) {
    var settings by rememberSaveable { mutableStateOf(false) }
    val opened = vm.openedIssue()
    when {
        settings || !vm.configured -> SettingsScreen(vm, canGoBack = vm.configured) { settings = false }
        opened != null -> IssueScreen(vm, opened)
        else -> TasksScreen(vm) { settings = true }
    }
    if (!settings) vm.commentFor?.let { id -> vm.allIssues().firstOrNull { it.idReadable == id } }?.let { CommentEditor(vm, it) }
}

// ---------------------------------------------------------------- список задач
@Composable
fun TasksScreen(vm: MainViewModel, openSettings: () -> Unit) {
    val snackbar = remember { SnackbarHostState() }
    var editing by remember { mutableStateOf<UrgentTask?>(null) }    // id = "" — новая задача
    var deleting by remember { mutableStateOf<UrgentTask?>(null) }
    var confirmClearDone by remember { mutableStateOf(false) }
    LaunchedEffect(vm.message) {
        vm.message?.let { snackbar.showSnackbar(it); vm.message = null }
    }
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("YouTrack", fontWeight = FontWeight.Bold)
                        val n = vm.totalNew()
                        if (n > 0) {
                            Spacer(Modifier.width(10.dp))
                            Icon(Icons.Rounded.ChatBubble, null, tint = Color(0xFFE5484D), modifier = Modifier.size(18.dp))
                            Text(" $n нов.", style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                },
                actions = {
                    IconButton(onClick = vm::refresh) { Icon(Icons.Rounded.Refresh, "Обновить") }
                    IconButton(onClick = openSettings) { Icon(Icons.Rounded.Settings, "Настройки") }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
        floatingActionButton = {
            if (vm.tab == "urgent") {
                ExtendedFloatingActionButton(onClick = { editing = UrgentTask("", "", "", 0) },
                    icon = { Icon(Icons.Rounded.Add, null) }, text = { Text("Срочная задача") })
            }
        },
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize(), contentAlignment = Alignment.TopCenter) {
            Column(Modifier.widthIn(max = 920.dp).fillMaxSize().padding(horizontal = 12.dp)) {
                val tabIndex = when (vm.tab) { "day" -> 1; "urgent" -> 2; "attention" -> 3; else -> 0 }
                // подписи вкладок в три уровня под ширину экрана: полные, короткие, только значок и число
                val w = LocalConfiguration.current.screenWidthDp
                val lvl = if (w >= 840) 0 else if (w >= 600) 1 else 2
                fun label(names: List<String>, n: Int) = "${names[lvl]}  ${n.takeIf { it > 0 } ?: ""}".trim()
                val nUrgent = vm.urgentCount()
                val nAttn = vm.attnCount()
                TabRow(selectedTabIndex = tabIndex, containerColor = Color.Transparent) {
                    Tab(selected = tabIndex == 0, onClick = { vm.selectTab("all") },
                        text = { TabText(label(listOf("Все мои задачи", "Все", "Все"), vm.issues.size)) })
                    Tab(selected = tabIndex == 1, onClick = { vm.selectTab("day") },
                        icon = { Icon(Icons.Rounded.WbSunny, "На день", tint = if (tabIndex == 1) Color(0xFFF5B301) else LocalContentColorFallback()) },
                        text = { TabText(label(listOf("На день", "День", ""), vm.dayCount())) })
                    Tab(selected = tabIndex == 2, onClick = { vm.selectTab("urgent") },
                        icon = { Icon(Icons.Rounded.Bolt, "Срочные", tint = if (tabIndex == 2 || nUrgent > 0) Urgent_ else LocalContentColorFallback()) },
                        text = { TabText(label(listOf("Срочные", "Срочные", ""), nUrgent)) })
                    Tab(selected = tabIndex == 3, onClick = { vm.selectTab("attention") },
                        icon = { Icon(Icons.Rounded.AlternateEmail, "Нужно внимание", tint = if (tabIndex == 3 || nAttn > 0) Attn else LocalContentColorFallback()) },
                        text = { TabText(label(listOf("Нужно внимание", "Внимание", ""), nAttn)) })
                }
                Filters(vm) { confirmClearDone = true }
                if (vm.tab == "urgent") {
                    val list = vm.visibleUrgent()
                    Box(Modifier.weight(1f)) {
                        if (list.isEmpty()) {
                            Text(if (vm.search.isNotBlank()) "Ничего не найдено"
                                else "Срочных задач нет.\n\nНажмите «Срочная задача», чтобы добавить своё дело — оно хранится только на этом устройстве и в YouTrack не попадает.",
                                color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyLarge,
                                textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                                modifier = Modifier.fillMaxWidth().padding(40.dp))
                        } else {
                            LazyColumn(contentPadding = PaddingValues(top = 6.dp, bottom = 88.dp), modifier = Modifier.fillMaxSize()) {
                                items(list, key = { it.id }) { u ->
                                    UrgentCard(u, onToggle = { vm.toggleUrgentDone(u.id) }, onEdit = { editing = u }, onDelete = { deleting = u })
                                }
                            }
                        }
                    }
                } else {
                    val list = vm.visible()
                    PullToRefreshBox(isRefreshing = vm.loading, onRefresh = vm::refresh, modifier = Modifier.weight(1f)) {
                        if (list.isEmpty()) {
                            EmptyState(vm)
                        } else {
                            LazyColumn(contentPadding = PaddingValues(vertical = 6.dp), modifier = Modifier.fillMaxSize()) {
                                items(list, key = { it.idReadable }) { issue -> IssueCard(vm, issue) }
                            }
                        }
                    }
                }
                StatusLine(vm)
            }
        }
    }
    editing?.let { u ->
        UrgentEditor(u, onDismiss = { editing = null }) { title, text ->
            if (u.id.isEmpty()) vm.addUrgent(title, text) else vm.updateUrgent(u.id, title, text)
            editing = null
        }
    }
    deleting?.let { u ->
        AlertDialog(onDismissRequest = { deleting = null },
            title = { Text("Удалить задачу?") }, text = { Text("«${u.title}»") },
            confirmButton = { TextButton(onClick = { vm.deleteUrgent(u.id); deleting = null }) { Text("Удалить") } },
            dismissButton = { TextButton(onClick = { deleting = null }) { Text("Отмена") } })
    }
    if (confirmClearDone) {
        val n = vm.urgent.count { it.done }
        AlertDialog(onDismissRequest = { confirmClearDone = false },
            title = { Text("Удалить выполненные задачи ($n)?") },
            confirmButton = { TextButton(onClick = { vm.clearDoneUrgent(); confirmClearDone = false }) { Text("Удалить") } },
            dismissButton = { TextButton(onClick = { confirmClearDone = false }) { Text("Отмена") } })
    }
}

private val Urgent_ = Color(0xFFEF6C00)
private val Attn = Color(0xFFE5484D)

// ---------------------------------------------------------------- свои срочные задачи
@Composable
private fun UrgentCard(u: UrgentTask, onToggle: () -> Unit, onEdit: () -> Unit, onDelete: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
        shape = RoundedCornerShape(6.dp),
    ) {
        Row(Modifier.height(IntrinsicSize.Min)) {
            Box(Modifier.width(4.dp).fillMaxHeight().background(if (u.done) Color(0xFF757575) else Urgent_))
            IconButton(onClick = onToggle, modifier = Modifier.padding(top = 4.dp)) {
                Icon(if (u.done) Icons.Rounded.CheckCircle else Icons.Rounded.RadioButtonUnchecked,
                    if (u.done) "Не выполнено" else "Выполнено",
                    tint = if (u.done) Color(0xFF43A047) else MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Column(Modifier.weight(1f).padding(top = 10.dp, bottom = 10.dp, end = 4.dp)) {
                val stamp = if (u.done) u.doneAt else u.created
                Text((if (u.done) "выполнено · " else "") + if (stamp > 0) SimpleDateFormat("dd.MM HH:mm", Locale.getDefault()).format(Date(stamp)) else "",
                    style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(u.title, style = MaterialTheme.typography.titleMedium,
                    color = if (u.done) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface,
                    textDecoration = if (u.done) TextDecoration.LineThrough else null,
                    modifier = Modifier.clickable(onClick = onEdit).padding(vertical = 2.dp))
                if (u.text.isNotBlank()) {
                    Text(u.text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            Column(Modifier.padding(top = 4.dp, end = 4.dp)) {
                IconButton(onClick = onEdit) { Icon(Icons.Rounded.Edit, "Изменить", tint = MaterialTheme.colorScheme.onSurfaceVariant) }
                IconButton(onClick = onDelete) { Icon(Icons.Rounded.DeleteOutline, "Удалить", tint = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
        }
    }
}

/** Создание/изменение срочной задачи: название до 100 символов, текст до 1000 (со счётчиками). */
@Composable
private fun UrgentEditor(u: UrgentTask, onDismiss: () -> Unit, onSave: (String, String) -> Unit) {
    var title by rememberSaveable(u.id) { mutableStateOf(u.title) }
    var text by rememberSaveable(u.id) { mutableStateOf(u.text) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (u.id.isEmpty()) "Новая срочная задача" else "Изменить срочную задачу") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(title, { title = it.replace("\n", " ").take(Urgent.TITLE_MAX) }, label = { Text("Название") },
                    singleLine = true, modifier = Modifier.fillMaxWidth(),
                    supportingText = { Counter(title.length, Urgent.TITLE_MAX, 10) })
                OutlinedTextField(text, { text = it.take(Urgent.TEXT_MAX) }, label = { Text("Текст (необязательно)") },
                    minLines = 4, maxLines = 10, modifier = Modifier.fillMaxWidth(),
                    supportingText = { Counter(text.length, Urgent.TEXT_MAX, 50) })
                Text("Хранится только на этом устройстве, в YouTrack не отправляется.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        },
        confirmButton = { TextButton(onClick = { onSave(title, text) }, enabled = title.isNotBlank()) { Text("Сохранить") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } },
    )
}

@Composable
private fun Counter(n: Int, max: Int, warn: Int) {
    Text("$n/$max", color = if (n >= max - warn) Urgent_ else MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.fillMaxWidth(), textAlign = androidx.compose.ui.text.style.TextAlign.End)
}

// ---------------------------------------------------------------- полный просмотр задачи
@Composable
fun IssueScreen(vm: MainViewModel, issue: Issue) {
    val ctx = LocalContext.current
    val snackbar = remember { SnackbarHostState() }
    BackHandler { vm.closeFull() }
    LaunchedEffect(vm.message) {
        vm.message?.let { snackbar.showSnackbar(it); vm.message = null }
    }
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(issue.idReadable) },
                navigationIcon = { IconButton(onClick = vm::closeFull) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, "Назад") } },
                actions = {
                    val planned = issue.idReadable in vm.plan
                    IconButton(onClick = { vm.toggleStar(issue) }) {
                        Icon(if (planned) Icons.Rounded.Star else Icons.Rounded.StarBorder,
                            if (planned) "Убрать из плана" else "В план на день",
                            tint = if (planned) Color(0xFFF5B301) else MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    StatusMenu(vm, issue)
                    IconButton(onClick = { vm.open(ctx, issue) }) { Icon(Icons.AutoMirrored.Rounded.OpenInNew, "Открыть в YouTrack") }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
        floatingActionButton = {
            ExtendedFloatingActionButton(onClick = { vm.commentFor = issue.idReadable },
                icon = { Icon(Icons.Rounded.AddComment, null) }, text = { Text("Комментарий") })
        },
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize(), contentAlignment = Alignment.TopCenter) {
            SelectionContainer {  // текст можно выделить и скопировать
                Column(Modifier.widthIn(max = 920.dp).fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)) {
                    Text(issue.summary, style = MaterialTheme.typography.headlineSmall)
                    if (vm.tab == "attention") MentionBlock(vm, issue)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp),
                        modifier = Modifier.padding(top = 10.dp)) {
                        FieldChips(vm, issue, allSprints = true)
                    }
                    SectionTitle("Описание")
                    val d = vm.details[issue.idReadable]
                    when {
                        d == null -> Text("Загрузка…", color = MaterialTheme.colorScheme.onSurfaceVariant)
                        d.startsWith(MainViewModel.ERR) -> Text("Не удалось загрузить: ${d.drop(1)}", color = Color(0xFFE5484D))
                        d.isBlank() -> Text("Нет описания", color = MaterialTheme.colorScheme.onSurfaceVariant)
                        else -> Text(Model.cleanMarkdown(d, Int.MAX_VALUE), style = MaterialTheme.typography.bodyLarge)
                    }
                    SectionTitle(if (issue.comments.isEmpty()) "Комментариев нет" else "Комментарии (${issue.comments.size})")
                    issue.comments.reversed().forEach { c ->  // новые сверху
                        Card(Modifier.fillMaxWidth().padding(vertical = 4.dp),
                            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer)) {
                            Column(Modifier.padding(12.dp)) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(c.author, style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.Bold)
                                    Spacer(Modifier.width(8.dp))
                                    Text(SimpleDateFormat("dd.MM.yyyy HH:mm", Locale.getDefault()).format(Date(c.created)),
                                        style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                                Text(Model.cleanMarkdown(c.text, Int.MAX_VALUE).ifEmpty { "—" }, style = MaterialTheme.typography.bodyMedium,
                                    modifier = Modifier.padding(top = 4.dp))
                            }
                        }
                    }
                    Spacer(Modifier.height(88.dp))  // место под кнопку «Комментарий»
                }
            }
        }
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 20.dp, bottom = 6.dp))
}

@Composable
private fun LocalContentColorFallback() = MaterialTheme.colorScheme.onSurfaceVariant

@Composable
private fun StatusLine(vm: MainViewModel) {
    val text = when {
        vm.error != null -> vm.error + if (vm.lastOk > 0) " · данные от ${time(vm.lastOk)}" else ""
        vm.lastOk > 0 -> "Обновлено в ${time(vm.lastOk)}"
        else -> ""
    }
    Text(text, color = if (vm.error != null) Color(0xFFE5484D) else MaterialTheme.colorScheme.onSurfaceVariant,
        style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 6.dp))
}

private fun time(ms: Long) = SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(ms))

@Composable
private fun EmptyState(vm: MainViewModel) {
    val text = when {
        vm.filtersActive -> "Ничего не найдено"
        vm.tab == "attention" && vm.config.mentionsQuery.isBlank() -> "Вкладка выключена: в настройках не задан запрос упоминаний."
        vm.tab == "attention" && (vm.issues.isNotEmpty() || vm.lastOk > 0) ->
            "Вас нигде не упоминали 🎉\n\nЗдесь появятся незакрытые задачи, где коллеги отметили вас в комментарии через @."
        vm.tab == "day" && vm.issues.isNotEmpty() -> "План на день пуст.\n\nНажмите звёздочку на карточке во вкладке «Все мои задачи», чтобы добавить задачу в план."
        vm.loading -> "Загрузка…"
        vm.error != null && vm.issues.isEmpty() -> vm.error!!
        else -> "Задач нет 🎉"
    }
    // LazyColumn нужен, чтобы потянуть вниз для обновления работало и на пустом списке
    LazyColumn(Modifier.fillMaxSize()) {
        item {
            Text(text, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.fillMaxWidth().padding(40.dp), textAlign = androidx.compose.ui.text.style.TextAlign.Center)
        }
    }
}

@Composable
private fun Filters(vm: MainViewModel, clearDoneUrgent: () -> Unit) {
    Column(Modifier.padding(top = 8.dp)) {
        OutlinedTextField(
            value = vm.search, onValueChange = vm::updateSearch, singleLine = true,
            placeholder = { Text("Поиск…") }, leadingIcon = { Icon(Icons.Rounded.Search, null) },
            trailingIcon = { if (vm.search.isNotEmpty()) IconButton(onClick = { vm.updateSearch("") }) { Icon(Icons.Rounded.Close, "Очистить") } },
            modifier = Modifier.fillMaxWidth(),
        )
        if (vm.tab == "urgent") {  // на «Срочных» — только поиск
            if (vm.urgent.any { it.done }) {
                TextButton(onClick = clearDoneUrgent) {
                    Icon(Icons.Rounded.DeleteSweep, null, Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("Удалить выполненные")
                }
            }
            return@Column
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 6.dp)) {
            FilterChip(selected = vm.hotOnly, onClick = vm::toggleHot, label = { Text("Горящие сроки") },
                leadingIcon = { Icon(Icons.Rounded.Whatshot, null, Modifier.size(18.dp)) })
            MultiSelectChip("Приоритет", vm.priorities(), vm.prioFilter, vm::togglePrio) { it }
            val sprints = vm.sprints()
            if (sprints.any { it.name != MainViewModel.NO_SPRINT }) {
                MultiSelectChip("Спринт", sprints.map { it.name }, vm.sprintFilter, vm::toggleSprint) { name ->
                    name + if (sprints.firstOrNull { it.name == name }?.active == true) "  · текущий" else ""
                }
            }
            if (vm.hotOnly || vm.prioFilter.isNotEmpty() || vm.sprintFilter.isNotEmpty()) {
                TextButton(onClick = vm::resetFilters) { Text("Сбросить") }
            }
            if (vm.tab == "day" && vm.plan.isNotEmpty()) {
                TextButton(onClick = vm::clearPlan) { Text("Очистить план") }
            }
        }
    }
}

@Composable
private fun MultiSelectChip(title: String, options: List<String>, selected: Set<String>, toggle: (String) -> Unit, label: (String) -> String) {
    var open by remember { mutableStateOf(false) }
    Box {
        FilterChip(
            selected = selected.isNotEmpty(), onClick = { open = true },
            label = { Text(when (selected.size) { 0 -> title; 1 -> selected.first(); else -> "$title: ${selected.size}" }) },
            leadingIcon = { Icon(Icons.Rounded.FilterList, null, Modifier.size(18.dp)) },
        )
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            options.forEach { o ->
                DropdownMenuItem(
                    text = { Text(label(o)) }, onClick = { toggle(o) },
                    leadingIcon = { Icon(if (o in selected) Icons.Rounded.CheckCircle else Icons.Rounded.RadioButtonUnchecked, null) },
                )
            }
        }
    }
}

// ---------------------------------------------------------------- карточка задачи
@Composable
private fun IssueCard(vm: MainViewModel, issue: Issue) {
    val ctx = LocalContext.current
    val dayTab = vm.tab == "day"
    val done = dayTab && issue.idReadable in vm.done
    val opened = issue.idReadable in vm.expanded
    Card(
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
        shape = RoundedCornerShape(6.dp),
    ) {
        Row(Modifier.height(IntrinsicSize.Min)) {
            Box(Modifier.width(4.dp).fillMaxHeight().background(issue.barColor?.let { argb(it) } ?: MaterialTheme.colorScheme.surfaceVariant))
            if (dayTab) {
                Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(start = 2.dp)) {
                    IconButton(onClick = { vm.toggleDone(issue) }) {
                        Icon(if (done) Icons.Rounded.CheckCircle else Icons.Rounded.RadioButtonUnchecked,
                            if (done) "Не сделано" else "Сделано", tint = if (done) Color(0xFF43A047) else MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    IconButton(onClick = { vm.move(issue, -1) }, enabled = !vm.filtersActive) { Icon(Icons.Rounded.KeyboardArrowUp, "Выше") }
                    IconButton(onClick = { vm.move(issue, 1) }, enabled = !vm.filtersActive) { Icon(Icons.Rounded.KeyboardArrowDown, "Ниже") }
                }
            }
            Column(Modifier.weight(1f).padding(start = 12.dp, top = 10.dp, bottom = 10.dp, end = 4.dp)) {
                Text(issue.idReadable, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(
                    issue.summary,
                    style = MaterialTheme.typography.titleMedium,
                    color = if (done) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface,
                    textDecoration = if (done) TextDecoration.LineThrough else null,
                    modifier = Modifier.clickable { vm.openFull(issue) }.padding(vertical = 2.dp),
                )
                if (vm.tab == "attention") MentionBlock(vm, issue)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp),
                    modifier = Modifier.padding(top = 6.dp)) {
                    FieldChips(vm, issue, allSprints = false)
                }
                if (opened) Details(vm, issue)
            }
            Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(top = 4.dp, end = 4.dp)) {
                val n = if (opened) 0 else vm.newCount(issue)
                IconButton(onClick = { vm.toggleDetails(issue) }) {
                    BadgedBox(badge = { if (n > 0) Badge { Text(if (n > 99) "99+" else "$n") } }) {
                        Icon(if (opened) Icons.Rounded.ChatBubble else Icons.Rounded.ChatBubbleOutline, "Быстрый просмотр",
                            tint = if (opened) MaterialTheme.colorScheme.primary
                            else if (n > 0) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                val planned = issue.idReadable in vm.plan
                IconButton(onClick = { vm.toggleStar(issue) }) {
                    Icon(if (planned) Icons.Rounded.Star else Icons.Rounded.StarBorder,
                        if (planned) "Убрать из плана" else "В план на день",
                        tint = if (planned) Color(0xFFF5B301) else MaterialTheme.colorScheme.onSurfaceVariant)
                }
                StatusMenu(vm, issue)
            }
        }
    }
}

@Composable
private fun Details(vm: MainViewModel, issue: Issue) {
    val ctx = LocalContext.current
    Column(Modifier.padding(top = 10.dp)) {
        HorizontalDivider()
        Text("Описание", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 8.dp, bottom = 2.dp))
        val d = vm.details[issue.idReadable]
        when {
            d == null -> Text("Загрузка…", color = MaterialTheme.colorScheme.onSurfaceVariant)
            d.startsWith(MainViewModel.ERR) -> Text("Не удалось загрузить: ${d.drop(1)}", color = Color(0xFFE5484D))
            d.isBlank() -> Text("Нет описания", color = MaterialTheme.colorScheme.onSurfaceVariant)
            else -> Text(Model.cleanMarkdown(d, 700), style = MaterialTheme.typography.bodyMedium)
        }
        val comments = issue.comments.takeLast(3).reversed()
        Text(if (comments.isEmpty()) "Комментариев нет" else "Последние комментарии", style = MaterialTheme.typography.labelLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 10.dp, bottom = 2.dp))
        comments.forEach { c ->
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 4.dp)) {
                Text(c.author, style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.Bold)
                Spacer(Modifier.width(8.dp))
                Text(SimpleDateFormat("dd.MM HH:mm", Locale.getDefault()).format(Date(c.created)),
                    style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Text(Model.cleanMarkdown(c.text, 400).ifEmpty { "—" }, style = MaterialTheme.typography.bodyMedium)
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            TextButton(onClick = { vm.commentFor = issue.idReadable }, contentPadding = PaddingValues(0.dp)) {
                Icon(Icons.Rounded.AddComment, null, Modifier.size(16.dp))
                Spacer(Modifier.width(4.dp))
                Text("Написать комментарий")
            }
            TextButton(onClick = { vm.openFull(issue) }, contentPadding = PaddingValues(0.dp)) { Text("Полностью") }
            TextButton(onClick = { vm.open(ctx, issue) }, contentPadding = PaddingValues(0.dp)) {
                Text("Открыть в YouTrack")
                Spacer(Modifier.width(4.dp))
                Icon(Icons.AutoMirrored.Rounded.OpenInNew, null, Modifier.size(16.dp))
            }
        }
    }
}

@Composable
private fun Labeled(caption: String, chip: Chip?) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(caption, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(6.dp))
        if (chip == null) Text("—", color = MaterialTheme.colorScheme.onSurfaceVariant)
        else ChipView(chip)
    }
}

/** Плашка; с onClick — кликабельная, dropdown — со стрелкой ▾ (выпадающий список). */
@Composable
private fun ChipView(chip: Chip, dropdown: Boolean = false, onClick: (() -> Unit)? = null) {
    val shape = RoundedCornerShape(4.dp)
    val fg = chip.fg?.let { argb(it) } ?: MaterialTheme.colorScheme.onSurfaceVariant
    Surface(color = chip.bg?.let { argb(it) } ?: MaterialTheme.colorScheme.surfaceVariant, shape = shape,
        modifier = if (onClick != null) Modifier.clip(shape).clickable(onClick = onClick) else Modifier) {
        Row(verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(start = 6.dp, end = if (dropdown) 2.dp else 6.dp, top = 2.dp, bottom = 2.dp)) {
            Text(chip.text, color = fg, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
            if (dropdown) Icon(Icons.Rounded.ArrowDropDown, null, tint = fg, modifier = Modifier.size(18.dp))
        }
    }
}

/** Плашки карточки: статус, приоритет и спринт открывают список значений из YouTrack; срок — только показ. */
@Composable
private fun FieldChips(vm: MainViewModel, issue: Issue, allSprints: Boolean) {
    if (vm.tab == "attention" && vm.mention(issue) != null) {
        val waiting = vm.attnWaiting(issue)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Ответ", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.width(6.dp))
            ChipView(if (waiting) Chip("ждёт ответа", 0xFFE5484D, 0xFFFFFFFF) else Chip("отвечено", 0xFF2E7D32, 0xFFFFFFFF),
                onClick = { vm.toggleAttn(issue) })
        }
    }
    FieldChip(vm, issue, FieldKind.STATE, "Статус", issue.state)
    FieldChip(vm, issue, FieldKind.PRIORITY, "Приоритет", issue.priority)
    Model.dueInfo(issue.dueMillis)?.let { Labeled("Срок", Chip(it.label, it.bg, it.fg)) }
    fun chipOf(s: Sprint, text: String) = if (s.active) Chip(text, 0xFF1565C0, 0xFFFFFFFF) else Chip(text)
    when {
        issue.sprints.isEmpty() -> if (issue.sprintField != null) FieldChip(vm, issue, FieldKind.SPRINT, "Спринт", null)
        allSprints -> issue.sprints.forEachIndexed { i, s ->
            if (i == 0) FieldChip(vm, issue, FieldKind.SPRINT, "Спринт", chipOf(s, s.name)) else Labeled("Спринт", chipOf(s, s.name))
        }
        else -> {
            val s = issue.sprints.first()
            val more = if (issue.sprints.size > 1) " +${issue.sprints.size - 1}" else ""
            FieldChip(vm, issue, FieldKind.SPRINT, "Спринт", chipOf(s, s.name + more))
        }
    }
}

@Composable
private fun FieldChip(vm: MainViewModel, issue: Issue, kind: FieldKind, caption: String, chip: Chip?) {
    val editable = when (kind) {
        FieldKind.STATE -> issue.stateField
        FieldKind.PRIORITY -> issue.priorityField
        FieldKind.SPRINT -> issue.sprintField
    } != null
    if (!editable) {
        Labeled(caption, chip)
        return
    }
    var open by remember { mutableStateOf(false) }
    var data by remember { mutableStateOf<FieldOptions?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(open) {
        if (open) {
            data = null; error = null
            try {
                data = vm.fieldOptions(issue, kind)
            } catch (e: Exception) {
                error = e.message ?: "ошибка"
            }
        }
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(caption, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(6.dp))
        Box {
            if (chip != null) ChipView(chip, dropdown = true) { open = true }
            else OutlinedChip("выбрать") { open = true }
            DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
                val d = data
                val err = error
                when {
                    err != null -> MenuNote(err, Color(0xFFE5484D))
                    d == null -> MenuNote("Загрузка…")
                    else -> {
                        val cur = d.current.joinToString(", ").ifEmpty { "не задан" }
                        MenuNote(when (kind) {
                            FieldKind.STATE -> "Сейчас: $cur — перевести в:"
                            FieldKind.PRIORITY -> "Приоритет:"
                            FieldKind.SPRINT -> "Спринт ($cur):"
                        }, bold = true)
                        if (d.options.isEmpty()) MenuNote(if (kind == FieldKind.STATE) "Нет доступных переходов" else "Нет доступных значений")
                        d.options.forEach { o ->
                            val removing = kind == FieldKind.SPRINT && o.checked
                            DropdownMenuItem(
                                text = { Text(if (removing) "${o.label}   (убрать из спринта)" else o.label) },
                                leadingIcon = { if (o.checked) Icon(Icons.Rounded.Check, null) else Spacer(Modifier.size(24.dp)) },
                                enabled = !(kind == FieldKind.PRIORITY && o.checked),  // текущий приоритет — ничего не меняем
                                onClick = {
                                    open = false
                                    vm.changeField(issue, kind, if (removing) "убрано из ${o.label}" else o.label, o.command)
                                },
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun OutlinedChip(text: String, onClick: () -> Unit) {
    val shape = RoundedCornerShape(4.dp)
    Row(verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.clip(shape).border(1.dp, MaterialTheme.colorScheme.outline, shape).clickable(onClick = onClick)
            .padding(start = 6.dp, end = 2.dp, top = 1.dp, bottom = 1.dp)) {
        Text(text, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Icon(Icons.Rounded.ArrowDropDown, null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(18.dp))
    }
}

@Composable
private fun MenuNote(text: String, color: Color = Color.Unspecified, bold: Boolean = false) {
    DropdownMenuItem(text = { Text(text, color = color, fontWeight = if (bold) FontWeight.Bold else null) },
        onClick = {}, enabled = false)
}

/** «Нужно внимание»: кто и когда упомянул + цитата комментария. */
@Composable
private fun MentionBlock(vm: MainViewModel, issue: Issue) {
    val m = vm.mention(issue) ?: return
    val waiting = vm.attnWaiting(issue)
    val more = if (m.count > 1) " · упоминаний: ${m.count}" else ""
    val whenText = if (m.time > 0) SimpleDateFormat("dd.MM HH:mm", Locale.getDefault()).format(Date(m.time)) else ""
    Text("@ ${m.author} · $whenText$more", style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold,
        color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 6.dp))
    Row(Modifier.height(IntrinsicSize.Min).padding(top = 2.dp)) {
        Box(Modifier.width(2.dp).fillMaxHeight().background(if (waiting) Attn else MaterialTheme.colorScheme.outline))
        Text(Model.cleanMarkdown(m.text, 220).ifEmpty { "—" }, style = MaterialTheme.typography.bodyMedium,
            color = if (waiting) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 8.dp))
    }
}

/** Новый комментарий. Закрытие — текст сохраняется как черновик; ошибка отправки — текст не теряется. */
@Composable
private fun CommentEditor(vm: MainViewModel, issue: Issue) {
    var text by rememberSaveable(issue.idReadable) { mutableStateOf(vm.drafts[issue.idReadable].orEmpty()) }
    var sending by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val close = { vm.saveDraft(issue, text); vm.commentFor = null }
    AlertDialog(
        onDismissRequest = { if (!sending) close() },
        title = { Text("Комментарий к ${issue.idReadable}") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(issue.summary, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Bold, maxLines = 3,
                    overflow = TextOverflow.Ellipsis)
                OutlinedTextField(text, { text = it.take(COMMENT_MAX); error = null }, placeholder = { Text("Текст комментария") },
                    minLines = 5, maxLines = 12, enabled = !sending, modifier = Modifier.fillMaxWidth())
                error?.let { Text(it, color = Color(0xFFE5484D), style = MaterialTheme.typography.bodySmall) }
                Text("Поддерживается разметка YouTrack, @логин — упомянуть коллегу. При закрытии текст сохранится как черновик.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        },
        confirmButton = {
            TextButton(enabled = !sending && text.isNotBlank(), onClick = {
                sending = true; error = null
                scope.launch {
                    val err = vm.sendComment(issue, text)
                    sending = false
                    if (err == null) vm.commentFor = null else error = err
                }
            }) { Text(if (sending) "Отправляю…" else "Отправить") }
        },
        dismissButton = { TextButton(onClick = close, enabled = !sending) { Text("Отмена") } },
    )
}

/** Меню карточки: комментарий и смена статуса (только переходы, разрешённые workflow). */
@Composable
private fun StatusMenu(vm: MainViewModel, issue: Issue) {
    var open by remember { mutableStateOf(false) }
    var options by remember { mutableStateOf<List<FieldOption>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(open) {
        if (open) {
            options = null; error = null
            try {
                options = vm.fieldOptions(issue, FieldKind.STATE).options
            } catch (e: Exception) {
                error = e.message ?: "ошибка"
            }
        }
    }
    Box {
        IconButton(onClick = { open = true }) { Icon(Icons.Rounded.MoreVert, "Действия") }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            DropdownMenuItem(text = { Text("Написать комментарий…") }, leadingIcon = { Icon(Icons.Rounded.AddComment, null) },
                onClick = { open = false; vm.commentFor = issue.idReadable })
            HorizontalDivider()
            MenuNote("Сменить статус", bold = true)
            val opts = options
            val err = error
            when {
                err != null -> MenuNote(err, Color(0xFFE5484D))
                opts == null -> MenuNote("Загрузка…")
                opts.isEmpty() -> MenuNote("Нет доступных переходов")
                else -> opts.forEach { o ->
                    DropdownMenuItem(text = { Text(o.label) }, onClick = { open = false; vm.changeField(issue, FieldKind.STATE, o.label, o.command) })
                }
            }
        }
    }
}

// ---------------------------------------------------------------- настройки
@Composable
fun SettingsScreen(vm: MainViewModel, canGoBack: Boolean, close: () -> Unit) {
    val cfg = vm.config
    var url by rememberSaveable { mutableStateOf(cfg.baseUrl) }
    var token by rememberSaveable { mutableStateOf("") }
    var query by rememberSaveable { mutableStateOf(cfg.query) }
    var stateField by rememberSaveable { mutableStateOf(cfg.stateField) }
    var prioField by rememberSaveable { mutableStateOf(cfg.priorityField) }
    var notify by rememberSaveable { mutableStateOf(cfg.notify) }
    var mentionsQuery by rememberSaveable { mutableStateOf(cfg.mentionsQuery) }
    var test by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    Scaffold(topBar = {
        TopAppBar(title = { Text("Настройки") }, navigationIcon = {
            if (canGoBack) IconButton(onClick = close) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, "Назад") }
        })
    }) { pad ->
        Box(Modifier.padding(pad).fillMaxSize(), contentAlignment = Alignment.TopCenter) {
            Column(Modifier.widthIn(max = 720.dp).verticalScroll(rememberScrollState()).padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(url, { url = it }, label = { Text("Адрес YouTrack") }, singleLine = true,
                    placeholder = { Text("https://company.youtrack.cloud") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), modifier = Modifier.fillMaxWidth())
                OutlinedTextField(token, { token = it }, label = { Text("Постоянный токен (permanent token)") }, singleLine = true,
                    visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
                    supportingText = {
                        Text("Профиль → Аккаунт и безопасность → Токены → Новый токен" +
                            if (vm.hasToken()) "\n✓ токен сохранён — оставьте поле пустым, чтобы не менять" else "")
                    })
                OutlinedTextField(query, { query = it }, label = { Text("Запрос (синтаксис поиска YouTrack)") },
                    modifier = Modifier.fillMaxWidth(), supportingText = { Text("например: ${Config.DEFAULT_QUERY}") })
                OutlinedTextField(mentionsQuery, { mentionsQuery = it }, label = { Text("Запрос вкладки «Нужно внимание»") },
                    modifier = Modifier.fillMaxWidth(), singleLine = true,
                    supportingText = { Text("задачи, где вас упомянули через @; пусто — вкладка выключена. По умолчанию: ${Config.DEFAULT_MENTIONS}") })
                FlowRow(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp),
                    maxItemsInEachRow = if (isPhone()) 1 else 2) {
                    OutlinedTextField(stateField, { stateField = it }, label = { Text("Поле статуса") }, singleLine = true, modifier = Modifier.weight(1f))
                    OutlinedTextField(prioField, { prioField = it }, label = { Text("Поле приоритета") }, singleLine = true, modifier = Modifier.weight(1f))
                }
                Text("Если поля называются иначе, виджет сам найдёт статус и приоритет по смыслу.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Уведомления о новых задачах и комментариях", modifier = Modifier.weight(1f))
                    Switch(notify, { notify = it })
                }
                val ctx = LocalContext.current
                OutlinedButton(onClick = { requestBackgroundWork(ctx) }) { Text("Разрешить работу в фоне") }
                Text("Без этого система может останавливать проверку новых событий.\n" +
                    "OnePlus: «Сведения о приложении → Использование батареи» → включите «Разрешить фоновую активность» и «Автозапуск».\n" +
                    "Huawei: «Настройки → Батарея → Запуск приложений» → «YouTrack задачи» → «Управлять вручную», включите «Работа в фоне».",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (test.isNotEmpty()) {
                    Text(test, color = if (test.startsWith("✓")) Color(0xFF43A047) else Color(0xFFE5484D))
                }
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    OutlinedButton(onClick = {
                        test = "Проверяю…"
                        scope.launch { test = vm.testConnection(url, token, query) }
                    }) { Text("Проверить") }
                    Button(onClick = {
                        vm.saveSettings(cfg.copy(baseUrl = url, query = query, stateField = stateField.ifBlank { "State" },
                            priorityField = prioField.ifBlank { "Priority" }, notify = notify, mentionsQuery = mentionsQuery.trim()), token)
                        if (vm.configured) close() else test = "✗ Укажите адрес и токен"
                    }) { Text("Сохранить") }
                }
            }
        }
    }
}

/** Узкий экран (телефон): ширина меньше 600 dp. */
@Composable
private fun isPhone() = LocalConfiguration.current.screenWidthDp < 600

@Composable
private fun TabText(s: String) = Text(s, maxLines = 1, softWrap = false, overflow = TextOverflow.Ellipsis)

/** Снять ограничения батареи для фоновой проверки; если система не даёт — открыть сведения о приложении. */
@SuppressLint("BatteryLife")
private fun requestBackgroundWork(ctx: Context) {
    val pkg = Uri.parse("package:${ctx.packageName}")
    runCatching { ctx.startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, pkg)) }
        .recoverCatching { ctx.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, pkg)) }
}
