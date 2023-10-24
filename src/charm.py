#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed operator for the SD-Core Graphical User Interface."""

import json
import logging
from typing import List

from charms.observability_libs.v1.kubernetes_service_patch import (  # type: ignore[import]
    KubernetesServicePatch,
)
from charms.sdcore_gnbsim.v0.fiveg_gnb_identity import GnbIdentityRequires  # type: ignore[import]
from charms.sdcore_upf.v0.fiveg_n4 import N4Requires  # type: ignore[import]
from charms.sdcore_webui.v0.sdcore_management import (  # type: ignore[import]
    SdcoreManagementRequires,
)
from charms.traefik_k8s.v1.ingress import IngressPerAppRequirer  # type: ignore[import]
from lightkube.models.core_v1 import ServicePort
from ops.charm import CharmBase
from ops.framework import EventBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.pebble import Layer

logger = logging.getLogger(__name__)

FIVEG_N4_RELATION_NAME = "fiveg_n4"
GNB_IDENTITY_RELATION_NAME = "fiveg_gnb_identity"
NMS_PORT = 3000
SDCORE_MANAGEMENT_RELATION_NAME = "sdcore-management"
GNB_CONFIG_PATH = "/nms/config/gnb_config.json"
UPF_CONFIG_PATH = "/nms/config/upf_config.json"


class SDCoreNMSOperatorCharm(CharmBase):
    """Main class to describe juju event handling for the SD-Core NMS operator."""

    def __init__(self, *args):
        super().__init__(*args)
        self._container_name = "nms"
        self._service_name = "sdcore-nms"
        self._container = self.unit.get_container(self._container_name)
        self.fiveg_n4 = N4Requires(charm=self, relation_name=FIVEG_N4_RELATION_NAME)
        self._gnb_identity = GnbIdentityRequires(self, GNB_IDENTITY_RELATION_NAME)
        self._sdcore_management = SdcoreManagementRequires(self, SDCORE_MANAGEMENT_RELATION_NAME)
        self._service_patcher = KubernetesServicePatch(
            charm=self,
            ports=[
                ServicePort(name="nms", port=NMS_PORT),
            ],
        )
        self.ingress = IngressPerAppRequirer(
            charm=self,
            port=NMS_PORT,
            relation_name="ingress",
            strip_prefix=True,
        )

        self.framework.observe(self.on.nms_pebble_ready, self._configure_sdcore_nms)
        self.framework.observe(self.fiveg_n4.on.fiveg_n4_available, self._configure_sdcore_nms)
        self.framework.observe(
            self._sdcore_management.on.management_url_available,
            self._configure_sdcore_nms,
        )
        self.framework.observe(
            self._gnb_identity.on.fiveg_gnb_identity_available,
            self._configure_sdcore_nms,
        )

    def _configure_sdcore_nms(self, event: EventBase) -> None:
        """Add Pebble layer and manages Juju unit status.

        Args:
            event (EventBase): Juju event.
        """
        if not self._container.can_connect():
            self.unit.status = WaitingStatus("Waiting for container to be ready")
            event.defer()
            return

        if not self.model.relations.get(SDCORE_MANAGEMENT_RELATION_NAME):
            self.unit.status = BlockedStatus(
                f"Waiting for `{SDCORE_MANAGEMENT_RELATION_NAME}` relation to be created"
            )
            return
        if not self._sdcore_management.management_url:
            self.unit.status = WaitingStatus("Waiting for webui management url to be available")
            event.defer()
            return
        self._configure_upf_information()
        self._configure_gnb_information()
        self._configure_pebble()
        self.unit.status = ActiveStatus()

    def _configure_pebble(self) -> None:
        """Configure the Pebble layer."""
        plan = self._container.get_plan()
        layer = self._pebble_layer
        if plan.services != layer.services:
            self._container.add_layer(self._container_name, layer, combine=True)
            self._container.restart(self._service_name)

    def _configure_upf_information(self) -> None:
        """The `fiveg_n4` relation is not mandatory.

        If it exists, config files are generated and pushed to the workload.
        """
        if not self.model.relations.get(FIVEG_N4_RELATION_NAME):
            logger.warning("Relation %s not available", FIVEG_N4_RELATION_NAME)
            return
        upf_existing_content = self._get_existing_config_file(path=UPF_CONFIG_PATH)
        upf_config_content = self._get_upf_config()
        if not upf_config_content:
            logger.warning("UPF config file not available")
            return
        if not upf_existing_content or not config_file_content_matches(
            existing_content=upf_existing_content, new_content=upf_config_content
        ):
            self._push_upf_config_file_to_workload(upf_config_content)

    def _configure_gnb_information(self) -> None:
        """The `fiveg_gnb_identity` relation is not mandatory.

        If it exists, config files are generated and pushed to the workload.
        """
        if not self.model.relations.get(GNB_IDENTITY_RELATION_NAME):
            logger.warning("Relation %s not available", GNB_IDENTITY_RELATION_NAME)
            return
        gnb_existing_content = self._get_existing_config_file(path=GNB_CONFIG_PATH)
        gnb_config_content = self._get_gnb_config()
        if not gnb_existing_content and not gnb_config_content:
            logger.warning("gNB config file not available")
            return
        if not config_file_content_matches(
            existing_content=gnb_existing_content, new_content=gnb_config_content
        ):
            self._push_gnb_config_file_to_workload(gnb_config_content)

    def _get_existing_config_file(self, path: str) -> str:
        """Gets the existing config file from the workload.

        Args:
            path (str): Path to the config file.

        Returns:
            str: Content of the config file.
        """
        if self._container.exists(path=path):
            existing_content_stringio = self._container.pull(path=path)
            return existing_content_stringio.read()
        return ""

    def _get_upf_hostname_list(self) -> List[str]:
        """Gets the list of UPF hostnames from the `fiveg_n4` relation data bag.

        Returns:
            List[str]: List of UPF hostnames
        """
        upf_hostname_list = []
        for fiveg_n4_relation in self.model.relations.get(FIVEG_N4_RELATION_NAME, []):
            if not fiveg_n4_relation:
                logger.warning("Relation %s not available", FIVEG_N4_RELATION_NAME)
                return []
            if not fiveg_n4_relation.app:
                logger.warning(
                    "Application missing from the %s relation data", FIVEG_N4_RELATION_NAME
                )
                return []
            if hostname := fiveg_n4_relation.data[fiveg_n4_relation.app].get("upf_hostname", ""):
                upf_hostname_list.append(hostname)
        return upf_hostname_list

    def _get_upf_port_list(self) -> List[int]:
        """Gets the list of UPF ports from the `fiveg_n4` relation data bag.

        Returns:
            List[int]: List of UPF ports
        """
        upf_ports = []
        for fiveg_n4_relation in self.model.relations.get(FIVEG_N4_RELATION_NAME, []):
            if not fiveg_n4_relation:
                logger.warning("Relation %s not available", FIVEG_N4_RELATION_NAME)
                return []
            if not fiveg_n4_relation.app:
                logger.warning(
                    "Application missing from the %s relation data", FIVEG_N4_RELATION_NAME
                )
                return []
            if port := fiveg_n4_relation.data[fiveg_n4_relation.app].get("upf_port", ""):
                upf_ports.append(int(port))
        return upf_ports

    def _get_gnb_name_list(self) -> List[str]:
        """Gets gNB name from the `fiveg_gnb_identity` relation data bag.

        Returns:
            str: gNB name.
        """
        gnb_name_list = []
        for gnb_identity_relation in self.model.relations.get(GNB_IDENTITY_RELATION_NAME, []):
            if not gnb_identity_relation:
                logger.warning("Relation %s not available", GNB_IDENTITY_RELATION_NAME)
                return []
            if not gnb_identity_relation.app:
                logger.warning(
                    "Application missing from the %s relation data",
                    GNB_IDENTITY_RELATION_NAME,
                )
                return []
            gnb_name_list.append(
                gnb_identity_relation.data[gnb_identity_relation.app].get("gnb_name", "")
            )
        return gnb_name_list

    def _get_gnb_tac_list(self) -> List[int]:
        """Gets TAC from the `fiveg_gnb_identity` relation data bag.

        Returns:
            int: Tracking Area Code (TAC)
        """
        tac_list = []
        for gnb_identity_relation in self.model.relations.get(GNB_IDENTITY_RELATION_NAME, []):
            if not gnb_identity_relation:
                logger.warning("Relation %s not available", GNB_IDENTITY_RELATION_NAME)
                return []
            if not gnb_identity_relation.app:
                logger.warning(
                    "Application missing from the %s relation data",
                    GNB_IDENTITY_RELATION_NAME,
                )
                return []
            tac_list.append(gnb_identity_relation.data[gnb_identity_relation.app].get("tac", ""))
        return tac_list

    def _get_upf_config(self) -> str:
        """Gets the UPF hosts configuration for the NMS in a list of dictionaries format.

        Returns:
            str: A json representation of list of dictionaries,
                each containing UPF hostname and port.
        """
        upf_hostname_list = self._get_upf_hostname_list()
        upf_port_list = self._get_upf_port_list()
        if len(upf_hostname_list) != len(upf_port_list):  # type: ignore[arg-type]
            raise RuntimeError("Number of UPF hostnames and ports do not match")

        upf_config = []
        for upf_hostname, upf_port in zip(
            upf_hostname_list, upf_port_list
        ):  # type: ignore[arg-type]
            upf_config_entry = {"hostname": upf_hostname, "port": str(upf_port)}
            upf_config.append(upf_config_entry)

        return json.dumps(upf_config, sort_keys=True)

    def _get_gnb_config(self) -> str:
        """Gets the UPF hosts configuration for the NMS in a list of dictionaries format.

        Returns:
            str: A json representation of list of dictionaries,
                each containing UPF hostname and port.
        """
        gnb_name_list = self._get_gnb_name_list()
        gnb_tac_list = self._get_gnb_tac_list()
        if len(gnb_name_list) != len(gnb_tac_list):  # type: ignore[arg-type]
            raise RuntimeError("Number of UPF hostnames and ports do not match")

        gnb_config = []
        for gnb_name, gnb_tac in zip(gnb_name_list, gnb_tac_list):  # type: ignore[arg-type]
            gnb_conf_entry = {"name": gnb_name, "tac": str(gnb_tac)}
            gnb_config.append(gnb_conf_entry)

        return json.dumps(gnb_config, sort_keys=True)

    def _push_upf_config_file_to_workload(self, content: str):
        """Push the upf config files to the NMS workload."""
        self._container.push(path=UPF_CONFIG_PATH, source=content)
        logger.info("Pushed %s config file", UPF_CONFIG_PATH)

    def _push_gnb_config_file_to_workload(self, content: str):
        """Push the gnb config files to the NMS workload."""
        self._container.push(path=GNB_CONFIG_PATH, source=content)
        logger.info("Pushed %s config file", GNB_CONFIG_PATH)

    @property
    def _pebble_layer(self) -> Layer:
        """Return pebble layer for the charm.

        Return:
            Layer: Pebble Layer.
        """
        return Layer(
            {
                "summary": "NMS layer",
                "description": "Pebble config layer for the NMS",
                "services": {
                    self._service_name: {
                        "override": "replace",
                        "startup": "enabled",
                        "command": "/bin/bash -c 'cd /app && npm run start'",
                        "environment": self._environment_variables,
                    },
                },
            }
        )

    @property
    def _environment_variables(self) -> dict:
        """Returns environment variables for the nms service.

        Returns:
            dict: Environment variables.
        """
        return {
            "WEBUI_ENDPOINT": self._sdcore_management.management_url,
            "UPF_CONFIG_PATH": UPF_CONFIG_PATH,
            "GNB_CONFIG_PATH": GNB_CONFIG_PATH,
        }


def config_file_content_matches(existing_content: str, new_content: str) -> bool:
    """Returns whether two config file contents match."""
    try:
        existing_content_list = json.loads(existing_content)
        new_content_list = json.loads(new_content)
        return existing_content_list == new_content_list
    except json.JSONDecodeError:
        return False


if __name__ == "__main__":  # pragma: no cover
    main(SDCoreNMSOperatorCharm)
