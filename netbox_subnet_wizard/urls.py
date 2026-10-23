from django.urls import path

from . import views

app_name = 'netbox_subnet_wizard'

urlpatterns = [
    path('', views.SubnetWizardView.as_view(), name='wizard'),
]
