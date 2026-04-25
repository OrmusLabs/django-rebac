# django_rebac/backends/base.py
from abc import ABC, abstractmethod


class BaseReBACBackend(ABC):
    """Abstract base class defining the contract for all ReBAC backend adapters."""

    def __init__(self, **options: str) -> None:
        """Initializes the backend with configuration options.

        Args:
            **options: Dynamic keyword arguments derived from BACKEND_OPTIONS.
        """
        self.options = options
        self.setup()

    @abstractmethod
    def setup(self) -> None:
        """Instantiates the underlying SDK client (e.g., OpenFGA or SpiceDB client)."""
        pass

    @abstractmethod
    def check(self, user: str, relation: str, obj: str) -> bool:
        """Verifies if a user has a specific relation to an object.

        Args:
            user: The subject string (e.g., 'user:123').
            relation: The action or role (e.g., 'viewer').
            obj: The resource string (e.g., 'document:456').

        Returns:
            bool: True if authorized, False otherwise.

        Raises:
            ConnectionError: If the backend service is unreachable.
        """
        pass

    @abstractmethod
    def list_objects(self, user: str, relation: str, object_type: str) -> list[str]:
        """Returns a list of object IDs the user has the specified relation to."""
        pass

    @abstractmethod
    def write_tuples(self, tuples: list[dict[str, str]]) -> None:
        """Writes relationships to the ReBAC store."""
        pass

    @abstractmethod
    def delete_tuples(self, tuples: list[dict[str, str]]) -> None:
        """Deletes relationships from the ReBAC store."""
        pass
