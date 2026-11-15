# Виджет главного экрана и фоновая проверка создаются системой по имени класса
-keep class app.ytwidget.todo.TasksWidgetReceiver { *; }
-keep class app.ytwidget.todo.SyncWorker { *; }
-keep class app.ytwidget.todo.RefreshAction { *; }
