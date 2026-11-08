from django.urls import path

from . import views

app_name = 'netbox_subnet_wizard'

urlpatterns = [
    path('', views.PickerView.as_view(), name='wizard'),
    path('<int:pk>/', views.AllocateView.as_view(), name='allocate'),
    path('<int:pk>/confirm/', views.ConfirmView.as_view(), name='confirm'),
]
