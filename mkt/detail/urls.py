from django.conf.urls import include, patterns, url

from mkt.purchase.urls import app_purchase_patterns
from mkt.ratings.urls import review_patterns
from mkt.receipts.urls import app_receipt_patterns
from mkt.stats.urls import app_stats_patterns
from . import views


urlpatterns = patterns('',
    url('^$', views.detail, name='detail'),
    url('^abuse$', views.abuse, name='detail.abuse'),
    url('^privacy$', views.privacy, name='detail.privacy'),

    # Merge app purchase / receipt patterns.
    ('^purchase/', include(app_purchase_patterns)),
    ('^purchase/', include(app_receipt_patterns)),

    # Statistics.
    ('^statistics/', include(app_stats_patterns)),

    # Ratings.
    ('^reviews/', include(review_patterns)),

    url('^activity/', views.app_activity, name='detail.app_activity'),
)
