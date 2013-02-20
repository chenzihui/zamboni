import logging
import json

from mkt.api.authentication import (PermissionAuthorization,
                                    MarketplaceAuthentication)

from mkt.api.base import MarketplaceResource

from .models import MonolithRecord


class MonolithData(MarketplaceResource):

    class Meta:
        queryset = MonolithRecord.objects.all()
        allowed_methods = ['get', 'delete']
        resource_name = 'data'
        filtering = {'recorded': ['exact', 'lt', 'lte', 'gt', 'gte'],
                     'key': ['exact'],
                     'id': ['lte', 'gte']}
        authorization = PermissionAuthorization('Monolith', 'API')
        authentication = MarketplaceAuthentication()

    def obj_delete_list(self, request=None, **kwargs):
        filters = self.build_filters(request.GET)
        object_list = self.get_object_list(request).filter(**filters)

        for obj in object_list:
            obj.delete()
