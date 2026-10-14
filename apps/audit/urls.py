from django.urls import path

from . import views

app_name = "audit"
urlpatterns = [path("", views.AuditoriaListView.as_view(), name="lista")]
