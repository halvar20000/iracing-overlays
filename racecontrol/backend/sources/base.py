"""Abstract data-source interface.

The rest of the application only ever talks to a :class:`DataSource`.  Two
implementations exist:

* :class:`~backend.sources.simulator.SimulatorSource` - a self-contained race
  simulator so the app works on any machine, with or without iRacing.
* :class:`~backend.sources.iracing_source.IRacingSource` - the real bridge to
  iRacing's telemetry SDK (Windows only, iRacing must be running).

Swapping the data source changes *nothing* downstream: the race-state engine,
the WebSocket layer and the UI all stay identical.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from backend.models import Frame


class DataSource(ABC):
    """A producer of normalised :class:`Frame` objects."""

    #: Short identifier shown in the UI ("simulator" / "iracing").
    name: str = "base"

    @abstractmethod
    def connect(self) -> bool:
        """Attempt to connect. Returns True on success."""

    @abstractmethod
    def disconnect(self) -> None:
        """Release any resources held by the source."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the source currently has live data available."""

    @abstractmethod
    def poll(self) -> Optional[Frame]:
        """Return the latest :class:`Frame`, or ``None`` if no data yet."""

    def send_command(self, command: str, **params) -> str:
        """Execute a race-control command.

        Returns a short human-readable result string for the race log.
        The base implementation simply acknowledges the command; concrete
        sources override this to actually act on it.
        """
        return f"command '{command}' ignored - {self.name} source has no control link"
