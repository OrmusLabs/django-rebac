# 🏗️ Designing the ReBAC Schema
> DSL for OpenFGA

To get the most out of `django-rebac`, your ReBAC schema must follow the **Roles vs. Permissions Pattern**.

This is an industry-standard Zanzibar architecture that completely decouples your application's security logic from your Python code. By strictly separating **who a user is** from **what a user can do**, you can change business rules on the fly without ever deploying new Django code.

## The Core Philosophy

Every `type` in your ReBAC schema should be split into three distinct sections:

1. **Structural Links:** How does this object relate to its parent?
2. **Roles (The "Who"):** The titles users hold (`owner`, `editor`, `reader`). These are assigned by your Django **Models**.
3. **Permissions (The "What"):** The actions users can take (`can_list_org`, `can_read_document`). These are checked by your Django **Views**.

## Complete Example: Cascading Hierarchy

Here is the definitive schema design for a cascading hierarchy (`Platform` -> `Organization` -> `Folders` -> `Documents`). Notice how roles cascade downwards, and permissions strictly check those roles.

```yaml
model
  schema 1.1

type user

# ==========================================
# LEVEL 0: THE GLOBAL PLATFORM
# ==========================================
type platform
  relations
    define superadmin: [user]

# ==========================================
# LEVEL 1: ORGANIZATION
# ==========================================
type organization
  relations
    # 1. Structural Link
    define platform: [platform]

    # 2. Roles (Cascading linearly!)
    define superadmin: [user] or superadmin from platform
    define admin: [user] or superadmin
    define manager: [user] or admin
    define member: [user] or manager

    # 3. Permissions (Checked by Django Views)
    define can_manage_settings: admin
    define can_view_dashboard: manager
    define can_list_org: member

# ==========================================
# LEVEL 2: FOLDER
# ==========================================
type folder
  relations
    # 1. Structural Link
    define organization: [organization]

    # 2. Roles (Inheriting from the Organization)
    define owner: [user] or admin from organization
    define contributor: [user] or manager from organization or owner
    define viewer: [user] or contributor or member from organization

    # 3. Permissions (Checked by Django Views)
    define can_edit_folder: owner
    define can_add_items: contributor
    define can_list_folder: viewer

# ==========================================
# LEVEL 3: DOCUMENT
# ==========================================
type document
  relations
    # 1. Structural Link
    define folder: [folder]

    # 2. Roles (Inheriting from the Folder)
    define editor: [user] or owner from folder or contributor from folder
    define reader: [user] or editor or viewer from folder

    # 3. Permissions (Checked by Django Views)
    define can_update: editor
    define can_delete: editor
    define can_read_document: reader
```


### Visualize the DSL schema

```mermaid
%%{
  init: {
    'fontFamily': 'Roboto, sans-serif',
    'flowchart': {
      'nodeSpacing': 40,
      'rankSpacing': 60
    }
  }
}%%
flowchart TD
    %% Styling Definitions
    classDef role fill:none,stroke:#af471e,stroke-width:2px,rx:15,ry:15
    classDef perm fill:none,stroke:#047857,stroke-width:2px,rx:4,ry:4

    %% Light/Dark Mode Compatible Highlighting
    classDef highlight fill:none,stroke:#888888,stroke-width:2px,stroke-dasharray: 5 5

    subgraph L0 [LEVEL 0: PLATFORM]
        direction TB
        P_SA([Superadmin]):::role
    end
    class L0 highlight

    subgraph L1 [LEVEL 1: ORGANIZATION]
        direction TB
        O_SA([Superadmin]):::role
        O_AD([Admin]):::role
        O_MG([Manager]):::role
        O_MB([Member]):::role

        O_P1[can_manage_settings]:::perm
        O_P2[can_view_dashboard]:::perm
        O_P3[can_list_org]:::perm

        %% Internal Role Cascading (Compact)
        O_SA -.->|or| O_AD -.->|or| O_MG -.->|or| O_MB

        %% Permission Mapping
        O_AD --- O_P1
        O_MG --- O_P2
        O_MB --- O_P3
    end
    class L1 highlight

    subgraph L2 [LEVEL 2: FOLDER]
        direction TB
        F_OW([Owner]):::role
        F_CO([Contributor]):::role
        F_VI([Viewer]):::role

        F_P1[can_edit_folder]:::perm
        F_P2[can_add_items]:::perm
        F_P3[can_list_folder]:::perm

        %% Internal Role Cascading (Compact)
        F_OW -.->|or| F_CO -.->|or| F_VI

        %% Permission Mapping
        F_OW --- F_P1
        F_CO --- F_P2
        F_VI --- F_P3
    end
    class L2 highlight

    subgraph L3 [LEVEL 3: DOCUMENT]
        direction TB
        D_ED([Editor]):::role
        D_RE([Reader]):::role

        D_P1[can_update]:::perm
        D_P2[can_delete]:::perm
        D_P3[can_read_document]:::perm

        %% Internal Role Cascading (Compact)
        D_ED -.->|or| D_RE

        %% Permission Mapping
        D_ED --- D_P1
        D_ED --- D_P2
        D_RE --- D_P3
    end
    class L3 highlight

    %% ==========================================
    %% STRUCTURAL INHERITANCE
    %% an extra vertical layer between subgraphs, preventing title overlap!
    %% ==========================================
    P_SA === O_SA

    O_AD ==== F_OW
    O_MG ==== F_CO
    O_MB ==== F_VI

    F_OW ==== D_ED
    F_CO ==== D_ED
    F_VI ==== D_RE
```

## Rules for Django Integration

### Rule 1: Models only assign ROLES.
When configuring a Django Model's `RebacModelConfig`, the `creators` list must only assign base **Roles** (like `editor` or `owner`). Models should never directly assign permissions.

```python
# ❌ BAD: Assigning a permission directly
RebacCreatorConfig(relation="can_update", local_field="creator_id")

# ✅ GOOD: Assigning a role
RebacCreatorConfig(relation="editor", local_field="creator_id")
```

### Rule 2: Views only check PERMISSIONS.
When configuring a DRF View's `RebacViewConfig`, the configuration must only check **Permissions** (like `can_read_document` or `can_update`). Views should never check roles.

```python
# ❌ BAD: Checking a role directly
RebacViewConfig(
    object_type="document",
    read_relation="reader",
    update_relation="editor"
)

# ✅ GOOD: Checking a permission
RebacViewConfig(
    object_type="document",
    read_relation="can_read_document",
    update_relation="can_update"
)
```
