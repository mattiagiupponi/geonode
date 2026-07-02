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

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from dynamic_rest.serializers import DynamicModelSerializer
from dynamic_rest.fields.fields import DynamicRelationField

from geonode.security.auth_handlers import BasicAuthHandler
from geonode.security.auth_registry import auth_handler_registry
from geonode.security.models import AuthConfig
from geonode.security.registry import permissions_registry
from geonode.people.api.serializers import UserSerializer
from geonode.utils import is_safe_url

from ..models import Service
from .. import enumerations
from ..serviceprocessors import get_service_handler, get_available_service_types

logger = logging.getLogger(__name__)


class ServiceSerializer(DynamicModelSerializer):
    owner = DynamicRelationField(UserSerializer, embed=True, read_only=True)
    can_add_remote_resources = serializers.SerializerMethodField()

    class Meta:
        model = Service
        name = "service"
        fields = (
            "pk",
            "name",
            "title",
            "description",
            "abstract",
            "keywords",
            "type",
            "method",
            "base_url",
            "version",
            "operations",
            "owner",
            "harvester",
            "can_add_remote_resources",
            "created",
            "last_updated",
        )

    def get_can_add_remote_resources(self, instance):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        return permissions_registry.user_has_perm(user, perm=["add_remote_resource", "add_resource"])


class CreateServiceSerializer(serializers.Serializer):
    """Mirrors geonode.services.forms.CreateServiceForm, keeping the same validation rules."""

    url = serializers.CharField(max_length=512, label=_("Service URL"))
    type = serializers.ChoiceField(
        choices=[(k, v["label"]) for k, v in get_available_service_types().items()],
        initial=enumerations.AUTO,
        label=_("Service Type"),
    )
    username = serializers.CharField(required=False, allow_null=True, allow_blank=True, max_length=200)
    password = serializers.CharField(required=False, allow_null=True, allow_blank=True, max_length=200)

    def validate_url(self, value):
        if not is_safe_url(value):
            raise ValidationError(_("Invalid URL provided"))
        if Service.objects.filter(base_url=value).exists():
            raise ValidationError(_("Service %(url)s is already registered") % {"url": value})
        return value

    def validate(self, attrs):
        url = attrs.get("url")
        service_type = attrs.get("type")
        username = attrs.get("username") or None
        password = attrs.get("password") or None

        try:
            auth_config = None
            if username is not None or password is not None:
                payload = {"username": username, "password": password}
                auth_config = AuthConfig(type=BasicAuthHandler.handled_type)
                auth_handler_cls = auth_handler_registry.get_handler_class(auth_config.type)
                auth_handler_cls.validate(payload)
                auth_config.payload = payload

            service_handler = get_service_handler(base_url=url, service_type=service_type, auth_config=auth_config)
        except Exception as e:
            logger.error(f"CreateServiceSerializer validation error: {e}")
            raise ValidationError(_("Could not connect to the service at %(url)s") % {"url": url})

        if not service_handler.probe():
            raise ValidationError(_("Could not connect to the service at %(url)s") % {"url": url})
        elif service_type not in (enumerations.AUTO, enumerations.OWS):
            if service_handler.service_type != service_type:
                raise ValidationError(
                    _("Found service of type %(found_type)s instead of %(service_type)s")
                    % {"found_type": service_handler.service_type, "service_type": service_type}
                )

        attrs["service_handler"] = service_handler
        attrs["type"] = service_handler.service_type
        return attrs

    def create(self, validated_data):
        from django.conf import settings
        from django.core.cache import caches

        service_handler = validated_data["service_handler"]
        owner = self.context["request"].user

        service = service_handler.create_geonode_service(owner=owner)
        try:
            service.full_clean()
        except DjangoValidationError as e:
            raise ValidationError(str(e))
        service.save()
        service.keywords.add(*service_handler.get_keywords())

        if service_handler.indexing_method == enumerations.CASCADED:
            service_handler.create_cascaded_store(service)
        service_handler.geonode_service_id = service.id

        service_cache = caches["services"]
        service_cache.set(service_handler.url, service_handler, settings.SERVICE_CACHE_EXPIRATION_TIME)

        return service


class UpdateServiceSerializer(serializers.ModelSerializer):
    """Mirrors geonode.services.forms.ServiceForm."""

    class Meta:
        model = Service
        fields = ("title", "description", "abstract", "keywords")

    def update(self, instance, validated_data):
        keywords = validated_data.pop("keywords", None)
        instance = super().update(instance, validated_data)
        if keywords is not None:
            instance.keywords.clear()
            instance.keywords.add(*keywords)
            instance.save()
        return instance
