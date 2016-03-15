from django.conf.urls import url, include
from django.contrib import admin
from .apps.vici import urls as vici_urls
from .apps.connections import urls as connections_urls
from .apps import views

urlpatterns = [
    url(r'^admin/', admin.site.urls),
    url(r'^vici/', include(vici_urls)),
    url(r'^connection/', include(connections_urls)),
    url(r'^$', views.overview, name='index'),
    url(r'^login/?$', views.login, name='login'),
    url(r'^logout/?$', views.logout, name='logout'),
    url(r'^about/?$', views.about, name='about'),
]
