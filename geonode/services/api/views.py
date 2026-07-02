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
import logging

from django.conf import settings
from django.core.cache import caches
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from dynamic_rest.viewsets import DynamicModelViewSet

from geonode.base.models import ResourceBase
from geonode.base.api.pagination import GeoNodeApiPagination
from geonode.base.api.serializers import ResourceBaseSerializer
from geonode.harvesting.models import Harvester
from geonode.security.utils import get_visible_resources

from ..models import Service
from ..serviceprocessors import get_service_handler
from .permissions import ServicePermissions
from .serializers import CreateServiceSerializer, ServiceSerializer, UpdateServiceSerializer

logger = logging.getLogger(__name__)

service_cache = caches["services"]


def _get_service_handler(request, service):
    """Builds the remote service handler for the given Service instance."""
    auth = None
    if service.needs_authentication:
        from geonode.security.auth_registry import auth_handler_registry

        auth = auth_handler_registry.build(service.auth_config).get_request_auth()

    service_handler = get_service_handler(service.service_url, service.type, service.id, auth=auth)
    if not service_handler.geonode_service_id:
        service_handler.geonode_service_id = service.id
    return service_handler


def _get_service_resources(service, user, page):
    """Returns the service's harvested resources, paginated."""
    harvested_resources_ids = []
    if service.harvester:
        harvested_resources_ids = list(
            service.harvester.harvestable_resources.filter(geonode_resource__isnull=False).values_list(
                "geonode_resource__id", flat=True
            )
        )

    datasets = get_visible_resources(queryset=ResourceBase.objects.filter(id__in=harvested_resources_ids), user=user)

    paginator = Paginator(list(datasets), getattr(settings, "CLIENT_RESULTS_LIMIT", 25), orphans=3)
    try:
        resources = paginator.page(page)
    except PageNotAnInteger:
        resources = paginator.page(1)
    except EmptyPage:
        resources = paginator.page(paginator.num_pages)

    return resources, len(datasets)


class ServiceViewSet(DynamicModelViewSet):
    """API endpoint that allows remote Services to be viewed, registered, harvested and edited."""

    permission_classes = [IsAuthenticatedOrReadOnly, ServicePermissions]
    serializer_class = ServiceSerializer
    pagination_class = GeoNodeApiPagination
    queryset = Service.objects.all()
    lookup_field = "pk"

    def get_serializer_class(self):
        if self.action == "create":
            return CreateServiceSerializer
        if self.action in ("update", "partial_update"):
            return UpdateServiceSerializer
        return ServiceSerializer

    def create(self, request, *args, **kwargs):
        serializer = CreateServiceSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        service = serializer.save()
        return Response(ServiceSerializer(service, context={"request": request}).data, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        instance.dataset_set.all().delete()
        service_cache.delete(instance.base_url)
        if instance.harvester:
            instance.harvester.delete()

    @action(detail=True, methods=["get"])
    def resources(self, request, pk=None):
        """Returns the harvestable/harvested resources for this Service."""
        service = self.get_object()
        handler = _get_service_handler(request, service)

        has_unharvested_resources = handler.has_unharvested_resources(service)
        available_resources = handler.get_resources() if has_unharvested_resources else None

        errored_state = False
        if available_resources:
            not_yet_harvested = list(available_resources)
        else:
            not_yet_harvested = []
            errored_state = True

        resources, total_resources = _get_service_resources(service, request.user, request.query_params.get("page"))

        return Response(
            {
                "importable": not_yet_harvested,
                "errored_state": errored_state,
                "resources": ResourceBaseSerializer(resources, embed=True, many=True).data,
                "total_resources": total_resources,
            }
        )

    @action(detail=True, methods=["post"])
    def harvest(self, request, pk=None):
        """Triggers harvesting of the requested resource_list for this Service."""
        service = self.get_object()
        handler = _get_service_handler(request, service)

        requested = list(set(request.data.get("resource_list", [])))
        if not requested:
            raise ValidationError("resource_list is required")

        for resource_id in requested:
            try:
                handler.get_resource(resource_id)
            except KeyError as e:
                raise Http404(str(e))
            logger.debug(f"harvesting resource id {resource_id}...")
            handler.harvest_resource(resource_id, service)
            logger.debug(f"...Resource id {resource_id} harvested successfully")

        return Response({"detail": "The selected resources are being imported"})

    @action(detail=True, methods=["post"])
    def rescan(self, request, pk=None):
        """Rescans the remote Service for newly available resources."""
        service = self.get_object()
        try:
            _get_service_handler(request, service)
            if service.harvester and service.harvester.update_availability():
                if service.harvester.status != Harvester.STATUS_READY:
                    service.harvester.status = Harvester.STATUS_READY
                    service.harvester.save()
                if service.harvester.latest_refresh_session:
                    service.harvester.latest_refresh_session.delete()
                if service.harvester.latest_harvesting_session:
                    service.harvester.latest_harvesting_session.delete()
                service.harvester.initiate_update_harvestable_resources()
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({"detail": "Service rescanned successfully"})

    @action(detail=True, methods=["get"])
    def progress(self, request, pk=None):
        """Returns the harvesting progress for this Service."""
        service = get_object_or_404(Service, pk=pk)
        session = None
        if service.harvester:
            active_session = service.harvester.latest_harvesting_session
            if active_session and active_session.status in ["pending", "on-going"]:
                session = active_session
        if session:
            progress = session.get_progress_percentage()
            _status = session.status
            in_progress = _status in ["pending", "on-going"]
        else:
            progress = None
            _status = "no-session"
            in_progress = False
        return Response({"progress": progress, "status": _status, "in_progress": in_progress})
