# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch

import pytest
import scenario

from charm import SDCoreNMSOperatorCharm


class BaseNMSUnitTestFixtures:
    patcher_check_output = patch("charm.check_output")
    patcher_set_webui_url_in_all_relations = patch(
        "charms.sdcore_nms_k8s.v0.sdcore_config.SdcoreConfigProvides.set_webui_url_in_all_relations"
    )
    patcher_nms_list_gnbs = patch("nms.NMS.list_gnbs")
    patcher_nms_create_gnb = patch("nms.NMS.create_gnb")
    patcher_nms_delete_gnb = patch("nms.NMS.delete_gnb")
    patcher_nms_list_upfs = patch("nms.NMS.list_upfs")
    patcher_nms_create_upf = patch("nms.NMS.create_upf")
    patcher_nms_delete_upf = patch("nms.NMS.delete_upf")

    def common_setup(self):
        self.mock_check_output = BaseNMSUnitTestFixtures.patcher_check_output.start()
        self.mock_set_webui_url_in_all_relations = (
            BaseNMSUnitTestFixtures.patcher_set_webui_url_in_all_relations.start()
        )
        self.mock_list_gnbs = BaseNMSUnitTestFixtures.patcher_nms_list_gnbs.start()
        self.mock_create_gnb = BaseNMSUnitTestFixtures.patcher_nms_create_gnb.start()
        self.mock_delete_gnb = BaseNMSUnitTestFixtures.patcher_nms_delete_gnb.start()
        self.mock_list_upfs = BaseNMSUnitTestFixtures.patcher_nms_list_upfs.start()
        self.mock_create_upf = BaseNMSUnitTestFixtures.patcher_nms_create_upf.start()
        self.mock_delete_upf = BaseNMSUnitTestFixtures.patcher_nms_delete_upf.start()

    @staticmethod
    def tearDown() -> None:
        patch.stopall()

    @pytest.fixture(autouse=True)
    def context(self):
        self.ctx = scenario.Context(
            charm_type=SDCoreNMSOperatorCharm,
        )


class NMSUnitTestFixtures(BaseNMSUnitTestFixtures):
    patcher_certificate_is_available = patch("tls.Tls.certificate_is_available")
    patcher_check_and_update_certificate = patch("tls.Tls.check_and_update_certificate")

    @pytest.fixture(autouse=True)
    def setUp(self, request):
        self.common_setup()
        self.mock_certificate_is_available = (
            NMSUnitTestFixtures.patcher_certificate_is_available.start()
        )
        self.mock_check_and_update_certificate = (
            NMSUnitTestFixtures.patcher_check_and_update_certificate.start()
        )
        yield
        request.addfinalizer(self.tearDown)

class NMSTlsCertificatesFixtures(BaseNMSUnitTestFixtures):

    patcher_get_assigned_certificate = patch(
        "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificate"
    )

    @pytest.fixture(autouse=True)
    def setUp(self, request):
        self.common_setup()
        self.mock_get_assigned_certificate = (
            NMSTlsCertificatesFixtures.patcher_get_assigned_certificate.start()
        )
        yield
        request.addfinalizer(self.tearDown)
