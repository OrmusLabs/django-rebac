# django_rebac/backends/base.py
from abc import ABC, abstractmethod


class BaseReBACBackend(ABC):  # pragma: no cover
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
    def write_tuples(self, tuples: list[dict[str, str]]) -> None:
        """Writes relationships to the ReBAC store.

        Implementations MUST ensure this operation is idempotent. If a tuple
        already exists, the backend should safely ignore it or overwrite it
        without raising an exception.
        """
        pass

    @abstractmethod
    def delete_tuples(self, tuples: list[dict[str, str]]) -> None:
        """Deletes relationships from the ReBAC store.

        Implementations MUST ensure this operation is idempotent. If a tuple
        does not exist, the backend should safely ignore it without raising
        an exception.
        """
        pass

    @abstractmethod
    def read_tuples(self, object: str) -> list[dict[str, str]]:
        """Returns the relationship tuples the store currently holds for one object.

        T2.7: the read side of the sync contract. The outbox guarantees changes flow
        Django -> store, but only this method makes the store verifiable: a reconciler
        can diff what the store actually holds against what Django state expects, which
        is what enables drift detection, orphan cleanup, and backfill verification.

        Args:
            object: The resource string (e.g., 'document:456').

        Returns:
            list[dict[str, str]]: The stored tuples for that object, each in the same
                {'user', 'relation', 'object'} shape accepted by `write_tuples` and
                `delete_tuples`.

        Raises:
            ConnectionError: If the backend service is unreachable.
        """
        pass

    @abstractmethod
    def list_objects(self, user: str, relation: str, object_type: str) -> list[str]:
        """Returns a list of object IDs the user has the specified relation to."""
        pass

    @abstractmethod
    def batch_check(self, checks: list[dict[str, str]]) -> dict[str, dict[str, bool]]:
        """
        Executes a batch of authorization checks in a single network request.

        Args:
            checks: A list of dictionaries, each containing 'user', 'relation', and 'object'.

        Returns:
            dict[str, dict[str, bool]]: A fast-lookup mapping formatted as:
                                        { "object_id": { "relation": True/False } }

        Raises:
            ConnectionError: If the backend service is unreachable.
            SchemaError: If the store rejects a check (unknown relation or type).

        Implementations MUST let transport/schema failures propagate (as subclasses
        of `RebacError`) instead of returning a partial or empty map: callers rely
        on the exception to distinguish "no permission" from
        "authorization backend is down".
        """
        pass
