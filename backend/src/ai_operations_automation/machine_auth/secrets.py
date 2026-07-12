"""Minimal injectable external machine-secret boundary."""

from typing import Protocol


class MachineSecretUnavailable(Exception):
    pass


class MachineSecretResolver(Protocol):
    def resolve(self, external_secret_reference: str) -> bytes: ...


class UnavailableMachineSecretResolver:
    def resolve(self, external_secret_reference: str) -> bytes:
        del external_secret_reference
        raise MachineSecretUnavailable
