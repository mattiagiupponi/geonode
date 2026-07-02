#########################################################################
#
# Copyright (C) 2024 OSGeo
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
#########################################################################
from django.core.exceptions import PermissionDenied

from rest_framework import permissions

from geonode.security.registry import permissions_registry
from geonode.security.utils import check_add_remote_resource_perm

RESCAN_HARVEST_ACTIONS = ("rescan", "harvest")


class ServicePermissions(permissions.BasePermission):
    """Mirrors the permission checks previously done in geonode.services.views."""

    def has_permission(self, request, view):
        if request.method == "POST" and view.action not in RESCAN_HARVEST_ACTIONS:
            try:
                check_add_remote_resource_perm(request.user)
            except PermissionDenied:
                return False
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        if request.method in ("PUT", "PATCH"):
            return request.user == obj.owner or request.user.has_perm("change_service", obj=obj)
        if request.method == "DELETE":
            return request.user == obj.owner or request.user.has_perm("delete_service", obj=obj)
        if view.action in RESCAN_HARVEST_ACTIONS:
            return request.user == obj.owner or permissions_registry.user_has_perm(
                request.user, perm="change_resourcebase", instance=obj
            )
        return True
