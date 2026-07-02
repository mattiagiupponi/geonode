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
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APITestCase

from geonode.services.models import Service
from geonode.services import enumerations

UserModel = get_user_model()


class ServiceViewSetTestCase(APITestCase):
    """Tests the CRUD endpoints exposed by ServiceViewSet."""

    def setUp(self):
        self.owner = UserModel.objects.create_user(
            username="service_owner", password="password", email="owner@example.com"
        )
        self.other_user = UserModel.objects.create_user(
            username="other_user", password="password", email="other@example.com"
        )
        self.service = Service.objects.create(
            name="test_wms_service",
            title="Test WMS Service",
            type=enumerations.WMS,
            method=enumerations.INDEXED,
            base_url="http://example.com/wms",
            owner=self.owner,
        )

    def tearDown(self):
        Service.objects.filter(name="test_wms_service").delete()

    def test_anonymous_user_can_list_services(self):
        """Anonymous users have read-only access to the services list."""
        url = reverse("services-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(any(s["pk"] == str(self.service.pk) for s in response.data["services"]))

    def test_anonymous_user_can_retrieve_service(self):
        """Anonymous users have read-only access to a single service."""
        url = reverse("services-detail", kwargs={"pk": self.service.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["service"]["name"], self.service.name)

    def test_anonymous_user_cannot_create_service(self):
        """Anonymous users are not allowed to register a new service (POST)."""
        url = reverse("services-list")
        payload = {"url": "http://example.com/wms2", "type": enumerations.WMS}
        response = self.client.post(url, data=payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_user_cannot_update_service(self):
        """Anonymous users are not allowed to edit a service (PUT)."""
        url = reverse("services-detail", kwargs={"pk": self.service.pk})
        response = self.client.put(url, data={"title": "Hacked title"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_other_user_cannot_update_service(self):
        """A user without edit permissions on the service cannot update it (PUT)."""
        self.client.force_login(self.other_user)
        url = reverse("services-detail", kwargs={"pk": self.service.pk})
        response = self.client.put(url, data={"title": "Should not change"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_update_service(self):
        """The owner of the service can edit it (PUT)."""
        self.client.force_login(self.owner)
        url = reverse("services-detail", kwargs={"pk": self.service.pk})
        response = self.client.put(url, data={"title": "Updated title"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.service.refresh_from_db()
        self.assertEqual(self.service.title, "Updated title")

    def test_anonymous_user_cannot_delete_service(self):
        """Anonymous users are not allowed to delete a service (DELETE)."""
        url = reverse("services-detail", kwargs={"pk": self.service.pk})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_other_user_cannot_delete_service(self):
        """A user without delete permissions on the service cannot delete it (DELETE)."""
        self.client.force_login(self.other_user)
        url = reverse("services-detail", kwargs={"pk": self.service.pk})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_delete_service(self):
        """The owner of the service can delete it (DELETE), which also removes its harvester."""
        self.client.force_login(self.owner)
        url = reverse("services-detail", kwargs={"pk": self.service.pk})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Service.objects.filter(pk=self.service.pk).exists())

    def test_anonymous_user_can_get_resources_action(self):
        """Anonymous users can call the resources GET action on a service."""
        url = reverse("services-resources", kwargs={"pk": self.service.pk})
        with patch("geonode.services.api.views._get_service_handler") as mock_handler:
            mock_handler.return_value.has_unharvested_resources.return_value = False
            response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("resources", response.data)
        self.assertIn("total_resources", response.data)

    def test_anonymous_user_cannot_call_harvest_action(self):
        """Anonymous users are not allowed to trigger harvesting (POST)."""
        url = reverse("services-harvest", kwargs={"pk": self.service.pk})
        response = self.client.post(url, data={"resource_list": [1]})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_harvest_action_requires_resource_list(self):
        """The harvest action returns 400 if resource_list is missing/empty."""
        self.client.force_login(self.owner)
        url = reverse("services-harvest", kwargs={"pk": self.service.pk})
        with patch("geonode.services.api.views._get_service_handler"):
            response = self.client.post(url, data={"resource_list": []})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_can_harvest_resources(self):
        """The owner can trigger harvesting of the requested resource_list (POST) for the correct service handler."""
        self.client.force_login(self.owner)
        url = reverse("services-harvest", kwargs={"pk": self.service.pk})
        with patch("geonode.services.api.views._get_service_handler") as mock_get_handler:
            response = self.client.post(url, data={"resource_list": ["1"]})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_get_handler.assert_called_once()
        called_service = mock_get_handler.call_args[0][1]
        self.assertEqual(called_service.pk, self.service.pk)
        self.assertEqual(called_service.type, enumerations.WMS)
        mock_get_handler.return_value.harvest_resource.assert_called_once_with("1", self.service)

    def test_other_user_cannot_call_harvest_action(self):
        """A user without change permissions on the service cannot trigger harvesting (POST)."""
        self.client.force_login(self.other_user)
        url = reverse("services-harvest", kwargs={"pk": self.service.pk})
        response = self.client.post(url, data={"resource_list": ["1"]})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_user_cannot_call_rescan_action(self):
        """Anonymous users are not allowed to trigger a rescan (POST)."""
        url = reverse("services-rescan", kwargs={"pk": self.service.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_other_user_cannot_call_rescan_action(self):
        """A user without change permissions on the service cannot rescan it (POST)."""
        self.client.force_login(self.other_user)
        url = reverse("services-rescan", kwargs={"pk": self.service.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_rescan_service_without_harvester(self):
        """The owner can rescan a service that has no harvester attached."""
        self.client.force_login(self.owner)
        url = reverse("services-rescan", kwargs={"pk": self.service.pk})
        with patch("geonode.services.api.views._get_service_handler"):
            response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["detail"], "Service rescanned successfully")

    def test_owner_rescan_returns_503_on_handler_error(self):
        """The rescan action returns 503 if building the service handler fails."""
        self.client.force_login(self.owner)
        url = reverse("services-rescan", kwargs={"pk": self.service.pk})
        with patch("geonode.services.api.views._get_service_handler", side_effect=Exception("boom")):
            response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["detail"], "boom")

    def test_anonymous_user_can_get_progress_without_harvester(self):
        """The progress action returns a no-session status when the service has no harvester."""
        url = reverse("services-progress", kwargs={"pk": self.service.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "no-session")
        self.assertFalse(response.data["in_progress"])
        self.assertIsNone(response.data["progress"])

    def test_anonymous_user_can_get_progress_with_active_session(self):
        """The progress action returns the harvesting progress when a session is on-going."""
        url = reverse("services-progress", kwargs={"pk": self.service.pk})
        mock_session = Mock(status="on-going")
        mock_session.get_progress_percentage.return_value = 42
        self.service.harvester = Mock(latest_harvesting_session=mock_session)
        with patch("geonode.services.api.views.get_object_or_404", return_value=self.service):
            response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "on-going")
        self.assertTrue(response.data["in_progress"])
        self.assertEqual(response.data["progress"], 42)
