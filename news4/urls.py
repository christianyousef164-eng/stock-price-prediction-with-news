from django.contrib import admin
from django.urls import path, include
from django.conf import settings # NEW
from django.conf.urls.static import static # NEW

urlpatterns = [
    path('admin/', admin.site.urls),
    # Include all URLs from your 'news' app
    path('', include('news.urls', namespace='newsapp')), 
]

# This is needed to serve static files during development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)