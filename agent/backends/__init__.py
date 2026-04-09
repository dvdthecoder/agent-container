from typing import Protocol, runtime_checkable


@runtime_checkable
class AgentBackend(Protocol):
    name: str
    display_name: str

    def install(self, workspace: object) -> None: ...

    def run(self, workspace: object, task: str, timeout: int) -> object: ...
