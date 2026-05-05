# Nested Hierarchies (Tree Views)

When building nested API responses (e.g., Organization -> Folders -> Documents), standard nested DRF serializers can easily trigger massive N+1 queries or accidentally leak unauthorized records.

To solve this, use a 3-step **Prefetch Pattern**:

1. **Query ReBAC:** Call `list_objects` for each level of the hierarchy (e.g., allowed Orgs, allowed Folders, allowed Documents).
2. **Filter Base Querysets:** Create Django querysets using `.filter(id__in=allowed_ids)` for each level.
3. **Stitch with Prefetch:** Use Django's `Prefetch('related_name', queryset=filtered_queryset)` to stitch the objects together.

### Example 1: Secure Tree API View (Raw)

Assume you have a standard nested serializer setup where an `Organization` has many `folders`, and a `Folder` has many `documents`.

Here is how you write a single `APIView` that returns the entire tree securely for the current user:

```python
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db.models import Prefetch
from rebac.conf import get_setting
from rebac.utils import get_rebac_client

from .models import Organization, Folder, Document
from .serializers import OrganizationNestedSerializer

class SecureHierarchyTreeAPIView(APIView):
    """
    Returns a deeply nested tree of Orgs -> Folders -> Documents.
    100% ReBAC-secured at every single level, using only 3 DB queries.
    """

    def get_rebac_ids(self, rebac_user: str, object_type: str, relation: str) -> list[str]:
        """Helper to fetch allowed IDs for a specific type from ReBAC."""
        client = get_rebac_client()

        # and automatically strips the prefixes for you.
        allowed_ids = client.list_objects(
            user=rebac_user,
            relation=relation,
            object_type=object_type
        )

        return allowed_ids

    def get(self, request):
        user_attr = get_setting("REBAC_USER_ATTR")
        rebac_user = getattr(request, user_attr, None)
        if not rebac_user:
            return Response({"error": "Missing identity context."}, status=401)

        # STEP 1: Query ReBAC for the allowed IDs (3 Fast Network Calls)
        allowed_org_ids = self.get_rebac_ids(rebac_user, "organization", "can_list_org")
        allowed_folder_ids = self.get_rebac_ids(rebac_user, "folder", "can_list_folder")
        allowed_doc_ids = self.get_rebac_ids(rebac_user, "document", "can_read_document")

        # STEP 2: Filter the base querysets securely
        secure_docs = Document.objects.filter(id__in=allowed_doc_ids)

        # STEP 3: Stitch the tree together from the bottom up using Prefetch
        secure_folders = Folder.objects.filter(id__in=allowed_folder_ids).prefetch_related(
            Prefetch('documents', queryset=secure_docs) # Stitches Docs into Folders
        )

        secure_orgs = Organization.objects.filter(id__in=allowed_org_ids).prefetch_related(
            Prefetch('folders', queryset=secure_folders) # Stitches Folders into Orgs
        )

        # 4. Serialize the final, perfectly secured tree!
        serializer = OrganizationNestedSerializer(secure_orgs, many=True)
        return Response(serializer.data)
```


### Example 2: Using DRF Generic Views (ListAPIView)

If you prefer using DRF's Generic Views to take advantage of built-in pagination, filtering, and standard DRF workflows, you can place the exact same Prefetch logic inside the `get_queryset()` method.

```python
from rest_framework import generics
from rest_framework.exceptions import AuthenticationFailed
from django.db.models import Prefetch
from rebac.conf import get_setting
from rebac.utils import get_rebac_client

from .models import Organization, Folder, Document
from .serializers import OrganizationNestedSerializer

class SecureHierarchyTreeListAPIView(generics.ListAPIView):
    """
    Returns a deeply nested, ReBAC-secured tree of Orgs -> Folders -> Documents.
    Built on top of DRF's standard generic ListAPIView.
    """
    # Standard DRF setup
    serializer_class = OrganizationNestedSerializer

    def get_rebac_ids(self, rebac_user: str, object_type: str, relation: str) -> list[str]:
        """Helper to fetch allowed IDs for a specific type from ReBAC Backend."""
        client = get_rebac_client()

        # and automatically strips the prefixes for you.
        allowed_ids = client.list_objects(
            user=rebac_user,
            relation=relation,
            object_type=object_type
        )

        return allowed_ids

    def get_queryset(self):
        """
        Intercepts the queryset building process to inject ReBAC security
        and high-performance database Prefetching.
        """
        user_attr = get_setting("REBAC_USER_ATTR")

        rebac_user = getattr(self.request, user_attr, None)
        if not rebac_user:
            raise AuthenticationFailed("Missing identity context.")

        # STEP 1: Query ReBAC for the allowed IDs
        allowed_org_ids = self.get_rebac_ids(rebac_user, "organization", "can_list_org")
        allowed_folder_ids = self.get_rebac_ids(rebac_user, "folder", "can_list_folder")
        allowed_doc_ids = self.get_rebac_ids(rebac_user, "document", "can_read_document")

        # STEP 2: Filter the base querysets securely
        secure_docs = Document.objects.filter(id__in=allowed_doc_ids)

        # STEP 3: Stitch the tree together from the bottom up using Prefetch
        secure_folders = Folder.objects.filter(id__in=allowed_folder_ids).prefetch_related(
            Prefetch('documents', queryset=secure_docs) # Stitches Docs into Folders
        )

        # STEP 4: Return the finalized, top-level queryset to DRF
        return Organization.objects.filter(id__in=allowed_org_ids).prefetch_related(
            Prefetch('folders', queryset=secure_folders) # Stitches Folders into Orgs
        )
```

#### Serializer for the Standard Views

This is the standard `DocumentSerializer` referenced in the `DocumentCreateAPIView` and `DocumentViewSet` examples in [Securing API Views](views.md). Notice how `creator_id` is set to `read_only=True` because our view logic handles injecting the current user's ID during creation.

```python
# serializers.py
from rest_framework import serializers
from .models import Organization, Folder, Document

class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ['id', 'title', 'creator_id', 'created_at']

class FolderNestedSerializer(serializers.ModelSerializer):
    # This matches the related_name we used in the Prefetch object!
    documents = DocumentSerializer(many=True, read_only=True)

    class Meta:
        model = Folder
        fields = ['id', 'name', 'documents']

class OrganizationNestedSerializer(serializers.ModelSerializer):
    # This matches the related_name we used in the Prefetch object!
    folders = FolderNestedSerializer(many=True, read_only=True)

    class Meta:
        model = Organization
        fields = ['id', 'name', 'folders']
```

This ensures you execute only a few fast network calls and a few fast database queries, returning a 100% secure JSON tree.

### The ReBAC Schema Powering This View

For the Python code above to work flawlessly, your ReBAC schema must follow the **Roles vs. Permissions** pattern.

Notice how the generic views do **not** check if the user is an `admin` or an `editor`. They strictly check the **Permissions** (`can_list_org`, `can_list_folder`, `can_read_document`), allowing the ReBAC graph to calculate all the complex role inheritance automatically.

👉 **[See the Schema Design Guide](../schema/design-guide.md#complete-example-cascading-hierarchy) for the exact ReBAC Schema required to power this view.**
