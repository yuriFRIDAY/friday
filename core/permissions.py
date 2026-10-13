class PermissionManager:

    def __init__(self):
        self.permissions = {
            "guest": [
                "converse"
            ],

            "user": [
                "converse",
                "read_memory"
            ],

            "owner": [
                "converse",
                "read_memory",
                "write_memory",
                "use_tools",
                "configure"
            ],

            "system": [
                "critical_operations"
            ]
        }

    def has_permission(self, role, permission):

        if role not in self.permissions:
            return False

        return permission in self.permissions[role]