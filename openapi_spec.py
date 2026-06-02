def build_openapi_spec(server_url):
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Notedown Sync Server API",
            "version": "1.0.0",
            "description": (
                "Markdown note metadata and file synchronization API with "
                "revision-based conflict detection."
            ),
        },
        "servers": [{"url": server_url}],
        "tags": [
            {"name": "System"},
            {"name": "Auth"},
            {"name": "Admin"},
            {"name": "Sync"},
            {"name": "Docs"},
        ],
        "paths": {
            "/api/health": {
                "get": {
                    "tags": ["System"],
                    "summary": "Health check",
                    "responses": {
                        "200": {
                            "description": "Server is reachable.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/HealthResponse"}
                                }
                            },
                        }
                    },
                }
            },
            "/api/login": {
                "post": {
                    "tags": ["Auth"],
                    "summary": "Login with sync username and password",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/LoginRequest"},
                                "example": {
                                    "username": "admin",
                                    "password": "change-this-password",
                                },
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Bearer token issued.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/LoginResponse"}
                                }
                            },
                        },
                        "400": {"$ref": "#/components/responses/BadRequest"},
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                        "409": {
                            "description": "Initial setup is required before login.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            },
                        },
                    },
                }
            },
            "/api/setup/status": {
                "get": {
                    "tags": ["Auth"],
                    "summary": "Check whether initial credentials are configured",
                    "responses": {
                        "200": {
                            "description": "Setup status.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/SetupStatusResponse"}
                                }
                            },
                        }
                    },
                }
            },
            "/api/setup": {
                "post": {
                    "tags": ["Auth"],
                    "summary": "Create the initial admin account",
                    "description": "Allowed only while no environment or file credentials exist.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SetupRequest"},
                                "example": {
                                    "username": "admin",
                                    "password": "change-this-password",
                                },
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Initial account created and logged in.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/SetupResponse"}
                                }
                            },
                        },
                        "400": {"$ref": "#/components/responses/BadRequest"},
                        "409": {
                            "description": "Credentials are already configured.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            },
                        },
                    },
                }
            },
            "/api/logout": {
                "post": {
                    "tags": ["Auth"],
                    "summary": "Clear the browser admin session",
                    "responses": {
                        "200": {
                            "description": "Logged out.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string", "example": "ok"}
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/admin/account": {
                "get": {
                    "tags": ["Admin"],
                    "summary": "Read admin account settings",
                    "security": [{"bearerAuth": []}],
                    "responses": {
                        "200": {
                            "description": "Current account metadata.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/AdminAccount"}
                                }
                            },
                        },
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                },
                "post": {
                    "tags": ["Admin"],
                    "summary": "Update file-backed admin credentials",
                    "security": [{"bearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/AdminAccountUpdate"},
                                "example": {
                                    "username": "admin",
                                    "currentPassword": "current-password",
                                    "password": "new-password",
                                    "confirmPassword": "new-password",
                                },
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Credentials updated.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/AdminAccount"}
                                }
                            },
                        },
                        "400": {"$ref": "#/components/responses/BadRequest"},
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                        "409": {
                            "description": "Credentials are managed by environment variables.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            },
                        },
                    },
                },
            },
            "/api/admin/tokens": {
                "get": {
                    "tags": ["Admin"],
                    "summary": "List issued sync tokens",
                    "security": [{"bearerAuth": []}],
                    "responses": {
                        "200": {
                            "description": "Issued tokens.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/TokenListResponse"}
                                }
                            },
                        },
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                },
            },
            "/api/admin/tokens/{token_id}": {
                "delete": {
                    "tags": ["Admin"],
                    "summary": "Revoke one sync token",
                    "description": "Deletes the token record so the bearer token can no longer authenticate.",
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "token_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Token revoked.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/TokenDeleteResponse"}
                                }
                            },
                        },
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                        "404": {
                            "description": "Token not found.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            },
                        },
                    },
                },
            },
            "/api/admin/files/{relative_path}/history": {
                "get": {
                    "tags": ["Admin"],
                    "summary": "List git-backed history for one server file",
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "relative_path",
                            "in": "path",
                            "required": True,
                            "description": "POSIX-style note path, for example memo/note.md.",
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "File git history.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/FileHistoryResponse"}
                                }
                            },
                        },
                        "400": {"$ref": "#/components/responses/BadRequest"},
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/api/admin/files/{relative_path}/history/{commit}": {
                "get": {
                    "tags": ["Admin"],
                    "summary": "Read one historical git version of a server file",
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "relative_path",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "commit",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "Historical file payload.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/FileVersionResponse"}
                                }
                            },
                        },
                        "400": {"$ref": "#/components/responses/BadRequest"},
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/api/admin/files/{relative_path}/rollback": {
                "post": {
                    "tags": ["Admin"],
                    "summary": "Rollback a server file to a historical git commit",
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "relative_path",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/FileRollbackRequest"},
                                "example": {"commit": "2f4c1c8"},
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Rollback result.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/FileRollbackResponse"}
                                }
                            },
                        },
                        "400": {"$ref": "#/components/responses/BadRequest"},
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/api/manifest": {
                "get": {
                    "tags": ["Sync"],
                    "summary": "Get current server manifest",
                    "security": [{"bearerAuth": []}],
                    "responses": {
                        "200": {
                            "description": "Current sync manifest.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Manifest"}
                                }
                            },
                        },
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/api/files/{relative_path}": {
                "get": {
                    "tags": ["Sync"],
                    "summary": "Read one server file as base64 JSON",
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "relative_path",
                            "in": "path",
                            "required": True,
                            "description": "POSIX-style note path, for example memo/note.md.",
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "File payload.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/FilePayload"}
                                }
                            },
                        },
                        "400": {"$ref": "#/components/responses/BadRequest"},
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/api/sync/plan": {
                "post": {
                    "tags": ["Sync"],
                    "summary": "Plan a full metadata-first sync",
                    "description": (
                        "Compares client metadata with server metadata and returns "
                        "file-level upload, download, delete, and conflict targets. "
                        "This endpoint does not upload file content."
                    ),
                    "security": [{"bearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SyncPlanRequest"},
                                "example": {
                                    "clientId": "macbook-1",
                                    "baseRevision": 0,
                                    "clientInfo": {
                                        "hostname": "macbook-pro",
                                        "ipAddress": "192.168.0.12",
                                        "platform": "macOS",
                                        "appVersion": "1.2.3",
                                        "browser": "Chrome",
                                        "browserVersion": "125.0.0.0",
                                        "userAgent": "Notedown/1.2 Chrome/125",
                                    },
                                    "metadata": {
                                        "lastKnownRevision": 0,
                                        "body": {
                                            "version": 1,
                                            "workspaces": [
                                                {"id": "memo", "name": "memo"}
                                            ],
                                            "notes": [
                                                {
                                                    "id": "note-1",
                                                    "title": "새 노트",
                                                    "workspace": "memo",
                                                    "workspaceName": "memo",
                                                    "relativePath": "memo/note.md",
                                                    "updatedAtMs": 1780030055707,
                                                }
                                            ],
                                        },
                                    },
                                    "knownFiles": [],
                                },
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Sync plan.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/SyncPlanResponse"}
                                }
                            },
                        },
                        "400": {"$ref": "#/components/responses/BadRequest"},
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/api/sync/file": {
                "post": {
                    "tags": ["Sync"],
                    "summary": "Upload or delete one file",
                    "description": (
                        "Used for a file-save event or for files selected by "
                        "/api/sync/plan. The server updates the file record and "
                        "merges the supplied note/workspace metadata."
                    ),
                    "security": [{"bearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SingleFileSyncRequest"},
                                "example": {
                                    "clientId": "macbook-1",
                                    "baseRevision": 0,
                                    "clientInfo": {
                                        "hostname": "macbook-pro",
                                        "ipAddress": "192.168.0.12",
                                        "platform": "macOS",
                                        "appVersion": "1.2.3",
                                        "browser": "Chrome",
                                        "browserVersion": "125.0.0.0",
                                        "userAgent": "Notedown/1.2 Chrome/125",
                                    },
                                    "relativePath": "memo/note.md",
                                    "lastKnownRevision": 0,
                                    "updatedAtMs": 1780030055707,
                                    "contentEncoding": "utf-8",
                                    "content": "# 새 노트\n",
                                    "workspace": {"id": "memo", "name": "memo"},
                                    "note": {
                                        "id": "note-1",
                                        "title": "새 노트",
                                        "workspace": "memo",
                                        "workspaceName": "memo",
                                        "relativePath": "memo/note.md",
                                        "updatedAtMs": 1780030055707,
                                    },
                                },
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Single file sync result.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/SingleFileSyncResponse"}
                                }
                            },
                        },
                        "400": {"$ref": "#/components/responses/BadRequest"},
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/api/sync": {
                "post": {
                    "tags": ["Sync"],
                    "summary": "Legacy batch push/pull sync",
                    "description": (
                        "Backward-compatible batch endpoint. New clients should "
                        "prefer /api/sync/plan and /api/sync/file. "
                        "The client sends the last server revision it has seen. "
                        "If the server-side file revision is newer than the "
                        "client's lastKnownRevision and content differs, the "
                        "server returns a conflict instead of overwriting data."
                    ),
                    "security": [{"bearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SyncRequest"},
                                "example": {
                                    "clientId": "macbook-1",
                                    "baseRevision": 0,
                                    "clientInfo": {
                                        "hostname": "macbook-pro",
                                        "ipAddress": "192.168.0.12",
                                        "platform": "macOS",
                                        "appVersion": "1.2.3",
                                        "browser": "Chrome",
                                        "browserVersion": "125.0.0.0",
                                        "userAgent": "Notedown/1.2 Chrome/125",
                                    },
                                    "metadata": {
                                        "lastKnownRevision": 0,
                                        "body": {
                                            "version": 1,
                                            "workspaces": [
                                                {"id": "memo", "name": "memo"}
                                            ],
                                            "notes": [],
                                        },
                                    },
                                    "files": [
                                        {
                                            "relativePath": "memo/note.md",
                                            "lastKnownRevision": 0,
                                            "updatedAtMs": 1780030055707,
                                            "contentEncoding": "utf-8",
                                            "content": "# 새 노트\n",
                                        }
                                    ],
                                },
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Sync result. status is conflict when manual merge is required.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/SyncResponse"}
                                }
                            },
                        },
                        "400": {"$ref": "#/components/responses/BadRequest"},
                        "401": {"$ref": "#/components/responses/Unauthorized"},
                    },
                }
            },
            "/api/openapi.json": {
                "get": {
                    "tags": ["Docs"],
                    "summary": "OpenAPI JSON document",
                    "responses": {
                        "200": {
                            "description": "OpenAPI 3.1 schema.",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True}
                                }
                            },
                        }
                    },
                }
            },
            "/api/docs": {
                "get": {
                    "tags": ["Docs"],
                    "summary": "Swagger UI",
                    "responses": {
                        "200": {
                            "description": "Swagger UI HTML.",
                            "content": {"text/html": {"schema": {"type": "string"}}},
                        }
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "signed-token",
                }
            },
            "responses": {
                "BadRequest": {
                    "description": "Invalid request.",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                        }
                    },
                },
                "Unauthorized": {
                    "description": "Missing or invalid token, or login failure.",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                        }
                    },
                },
            },
            "schemas": {
                "HealthResponse": {
                    "type": "object",
                    "required": ["status"],
                    "properties": {"status": {"type": "string", "example": "ok"}},
                },
                "LoginRequest": {
                    "type": "object",
                    "required": ["username", "password"],
                    "properties": {
                        "username": {"type": "string"},
                        "password": {"type": "string", "format": "password"},
                    },
                },
                "LoginResponse": {
                    "type": "object",
                    "required": ["accessToken", "tokenType", "tokenId"],
                    "properties": {
                        "accessToken": {"type": "string"},
                        "tokenType": {"type": "string", "example": "Bearer"},
                        "tokenId": {"type": "string"},
                    },
                },
                "TokenRecord": {
                    "type": "object",
                    "required": ["id", "username", "issuedAt"],
                    "properties": {
                        "id": {"type": "string"},
                        "username": {"type": "string"},
                        "issuedAt": {"type": "string", "format": "date-time"},
                        "lastUsedAt": {"type": ["string", "null"], "format": "date-time"},
                        "connectionInfo": {"$ref": "#/components/schemas/ConnectionInfo"},
                    },
                    "additionalProperties": False,
                },
                "TokenListResponse": {
                    "type": "object",
                    "required": ["tokens"],
                    "properties": {
                        "tokens": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/TokenRecord"},
                        }
                    },
                    "additionalProperties": False,
                },
                "TokenDeleteResponse": {
                    "type": "object",
                    "required": ["status", "tokenId"],
                    "properties": {
                        "status": {"type": "string", "const": "deleted"},
                        "tokenId": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "FileHistoryCommit": {
                    "type": "object",
                    "required": [
                        "commit",
                        "shortCommit",
                        "committedAt",
                        "author",
                        "message",
                        "deleted",
                        "size",
                    ],
                    "properties": {
                        "commit": {"type": "string"},
                        "shortCommit": {"type": "string"},
                        "committedAt": {"type": "string", "format": "date-time"},
                        "author": {"type": "string"},
                        "message": {"type": "string"},
                        "deleted": {"type": "boolean"},
                        "contentHash": {"type": ["string", "null"]},
                        "size": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
                "FileHistoryResponse": {
                    "type": "object",
                    "required": ["relativePath", "repoPath", "commits"],
                    "properties": {
                        "relativePath": {"type": "string"},
                        "repoPath": {"type": "string"},
                        "commits": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/FileHistoryCommit"},
                        },
                    },
                    "additionalProperties": False,
                },
                "FileVersionResponse": {
                    "type": "object",
                    "required": [
                        "relativePath",
                        "commit",
                        "shortCommit",
                        "committedAt",
                        "author",
                        "message",
                        "deleted",
                    ],
                    "properties": {
                        "relativePath": {"type": "string"},
                        "commit": {"type": "string"},
                        "shortCommit": {"type": "string"},
                        "committedAt": {"type": "string", "format": "date-time"},
                        "author": {"type": "string"},
                        "message": {"type": "string"},
                        "deleted": {"type": "boolean"},
                        "contentEncoding": {"type": "string", "enum": ["base64"]},
                        "content": {"type": "string"},
                        "contentHash": {"type": ["string", "null"]},
                        "size": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
                "FileRollbackRequest": {
                    "type": "object",
                    "required": ["commit"],
                    "properties": {
                        "commit": {
                            "type": "string",
                            "description": "Full or short git commit hash from the file history.",
                        }
                    },
                    "additionalProperties": False,
                },
                "FileRollbackResponse": {
                    "type": "object",
                    "required": [
                        "status",
                        "relativePath",
                        "revision",
                        "deleted",
                        "rolledBackToCommit",
                    ],
                    "properties": {
                        "status": {"type": "string", "enum": ["accepted", "unchanged"]},
                        "relativePath": {"type": "string"},
                        "revision": {"type": "integer"},
                        "serverRevision": {"type": "integer"},
                        "contentHash": {"type": ["string", "null"]},
                        "deleted": {"type": "boolean"},
                        "gitCommit": {"type": ["string", "null"]},
                        "rolledBackToCommit": {"type": "string"},
                        "metadata": {"$ref": "#/components/schemas/MetadataSyncResult"},
                        "manifest": {"$ref": "#/components/schemas/Manifest"},
                    },
                    "additionalProperties": True,
                },
                "AdminAccount": {
                    "type": "object",
                    "required": ["username", "source", "editable"],
                    "properties": {
                        "username": {"type": ["string", "null"]},
                        "source": {
                            "type": "string",
                            "enum": ["none", "environment", "file"],
                        },
                        "editable": {"type": "boolean"},
                    },
                },
                "AdminAccountUpdate": {
                    "type": "object",
                    "required": [
                        "username",
                        "currentPassword",
                        "password",
                        "confirmPassword",
                    ],
                    "properties": {
                        "username": {"type": "string"},
                        "currentPassword": {
                            "type": "string",
                            "format": "password",
                        },
                        "password": {
                            "type": "string",
                            "format": "password",
                            "minLength": 8,
                        },
                        "confirmPassword": {
                            "type": "string",
                            "format": "password",
                            "minLength": 8,
                        },
                    },
                    "additionalProperties": False,
                },
                "SetupStatusResponse": {
                    "type": "object",
                    "required": ["configured", "source"],
                    "properties": {
                        "configured": {"type": "boolean"},
                        "source": {
                            "type": "string",
                            "enum": ["none", "environment", "file"],
                        },
                    },
                },
                "SetupRequest": {
                    "type": "object",
                    "required": ["username", "password"],
                    "properties": {
                        "username": {"type": "string"},
                        "password": {
                            "type": "string",
                            "format": "password",
                            "minLength": 8,
                        },
                    },
                    "additionalProperties": False,
                },
                "SetupResponse": {
                    "type": "object",
                    "required": [
                        "configured",
                        "username",
                        "accessToken",
                        "tokenType",
                        "tokenId",
                    ],
                    "properties": {
                        "configured": {"type": "boolean", "const": True},
                        "username": {"type": "string"},
                        "accessToken": {"type": "string"},
                        "tokenType": {"type": "string", "example": "Bearer"},
                        "tokenId": {"type": "string"},
                    },
                },
                "ErrorResponse": {
                    "type": "object",
                    "required": ["error"],
                    "properties": {
                        "error": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
                "MetadataSyncRequest": {
                    "type": "object",
                    "properties": {
                        "lastKnownRevision": {"type": "integer", "minimum": 0},
                        "contentHash": {
                            "type": "string",
                            "description": "SHA-256 hash of the canonical metadata JSON.",
                        },
                        "body": {
                            "type": "object",
                            "description": "metadata.json body.",
                            "additionalProperties": True,
                        },
                    },
                    "additionalProperties": True,
                },
                "FileSyncItem": {
                    "type": "object",
                    "required": ["relativePath"],
                    "properties": {
                        "relativePath": {"type": "string", "example": "memo/note.md"},
                        "lastKnownRevision": {"type": "integer", "minimum": 0},
                        "updatedAtMs": {"type": "integer"},
                        "deleted": {"type": "boolean", "default": False},
                        "contentEncoding": {
                            "type": "string",
                            "enum": ["base64", "utf-8"],
                            "default": "base64",
                        },
                        "content": {
                            "type": "string",
                            "description": "Required unless deleted is true.",
                        },
                        "contentHash": {
                            "type": "string",
                            "description": "Optional SHA-256 hash of decoded content.",
                        },
                    },
                    "additionalProperties": False,
                },
                "KnownFile": {
                    "type": "object",
                    "required": ["relativePath"],
                    "properties": {
                        "relativePath": {"type": "string", "example": "memo/note.md"},
                        "lastKnownRevision": {"type": "integer", "minimum": 0},
                        "contentHash": {"type": "string"},
                        "updatedAtMs": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
                "ClientInfo": {
                    "type": "object",
                    "description": (
                        "Optional client-reported device, network, browser, and "
                        "application metadata. It is stored under "
                        "manifest.clients.{clientId}.clientInfo. The server also "
                        "records request-derived IP and user-agent metadata under "
                        "connectionInfo."
                    ),
                    "properties": {
                        "hostname": {
                            "type": "string",
                            "description": "Device hostname, if available.",
                            "example": "macbook-pro",
                        },
                        "deviceName": {
                            "type": "string",
                            "description": "Human-readable device name.",
                            "example": "Taewook's MacBook Pro",
                        },
                        "ipAddress": {
                            "type": "string",
                            "description": "Client-reported local or public IP address.",
                            "example": "192.168.0.12",
                        },
                        "platform": {
                            "type": "string",
                            "description": "Operating system or runtime platform.",
                            "example": "macOS",
                        },
                        "os": {
                            "type": "string",
                            "description": "Operating system name or version.",
                            "example": "macOS 15.5",
                        },
                        "arch": {
                            "type": "string",
                            "description": "CPU architecture, if available.",
                            "example": "arm64",
                        },
                        "appVersion": {
                            "type": "string",
                            "description": "Notedown app version.",
                            "example": "1.2.3",
                        },
                        "browser": {
                            "type": "string",
                            "description": "Browser name, if the sync client runs in a browser.",
                            "example": "Chrome",
                        },
                        "browserVersion": {
                            "type": "string",
                            "description": "Browser version, if available.",
                            "example": "125.0.0.0",
                        },
                        "userAgent": {
                            "type": "string",
                            "description": "Client-reported user-agent string.",
                            "example": "Notedown/1.2 Chrome/125",
                        },
                    },
                    "additionalProperties": True,
                },
                "ConnectionInfo": {
                    "type": "object",
                    "description": (
                        "Server-observed request metadata captured during sync "
                        "and returned under manifest.clients.{clientId}.connectionInfo."
                    ),
                    "properties": {
                        "ipAddress": {
                            "type": ["string", "null"],
                            "description": (
                                "Best request IP selected from X-Real-IP, the first "
                                "X-Forwarded-For value, or the remote address."
                            ),
                            "example": "203.0.113.10",
                        },
                        "remoteAddress": {
                            "type": ["string", "null"],
                            "description": "Direct peer address seen by the Flask app.",
                            "example": "10.0.0.5",
                        },
                        "forwardedFor": {
                            "type": ["string", "null"],
                            "description": "Raw X-Forwarded-For header value.",
                            "example": "203.0.113.10, 10.0.0.5",
                        },
                        "userAgent": {
                            "type": ["string", "null"],
                            "description": "User-Agent request header.",
                            "example": "Notedown/1.2 Chrome/125",
                        },
                        "requestHost": {
                            "type": ["string", "null"],
                            "description": "Host used for the sync request.",
                            "example": "sync.example.com",
                        },
                    },
                    "additionalProperties": True,
                },
                "SyncPlanRequest": {
                    "type": "object",
                    "required": ["clientId", "metadata"],
                    "properties": {
                        "clientId": {"type": "string", "example": "macbook-1"},
                        "baseRevision": {"type": "integer", "minimum": 0, "default": 0},
                        "clientInfo": {"$ref": "#/components/schemas/ClientInfo"},
                        "metadata": {"$ref": "#/components/schemas/MetadataSyncRequest"},
                        "knownFiles": {
                            "type": "array",
                            "description": (
                                "Optional client-side file sync state. When omitted, "
                                "baseRevision is used for every file."
                            ),
                            "items": {"$ref": "#/components/schemas/KnownFile"},
                            "default": [],
                        },
                    },
                    "additionalProperties": False,
                },
                "SingleFileSyncRequest": {
                    "type": "object",
                    "required": ["clientId", "relativePath"],
                    "properties": {
                        "clientId": {"type": "string", "example": "macbook-1"},
                        "baseRevision": {"type": "integer", "minimum": 0, "default": 0},
                        "clientInfo": {"$ref": "#/components/schemas/ClientInfo"},
                        "relativePath": {"type": "string", "example": "memo/note.md"},
                        "lastKnownRevision": {"type": "integer", "minimum": 0},
                        "updatedAtMs": {"type": "integer"},
                        "deleted": {"type": "boolean", "default": False},
                        "contentEncoding": {
                            "type": "string",
                            "enum": ["base64", "utf-8"],
                            "default": "base64",
                        },
                        "content": {
                            "type": "string",
                            "description": "Required unless deleted is true.",
                        },
                        "contentHash": {
                            "type": "string",
                            "description": "Optional SHA-256 hash of decoded content.",
                        },
                        "workspace": {
                            "type": "object",
                            "description": "Workspace object to merge into metadata.json.",
                            "additionalProperties": True,
                        },
                        "note": {
                            "type": "object",
                            "description": "Note metadata object to upsert by relativePath or id.",
                            "additionalProperties": True,
                        },
                    },
                    "additionalProperties": False,
                },
                "SyncRequest": {
                    "type": "object",
                    "required": ["clientId"],
                    "properties": {
                        "clientId": {"type": "string", "example": "macbook-1"},
                        "baseRevision": {"type": "integer", "minimum": 0, "default": 0},
                        "clientInfo": {"$ref": "#/components/schemas/ClientInfo"},
                        "metadata": {"$ref": "#/components/schemas/MetadataSyncRequest"},
                        "files": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/FileSyncItem"},
                            "default": [],
                        },
                    },
                    "additionalProperties": False,
                },
                "MetadataSyncResult": {
                    "type": ["object", "null"],
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["accepted", "unchanged", "conflict"],
                        },
                        "revision": {"type": "integer"},
                        "contentHash": {"type": ["string", "null"]},
                        "serverMetadata": {
                            "type": ["object", "null"],
                            "additionalProperties": True,
                        },
                    },
                    "additionalProperties": True,
                },
                "MetadataPlanResult": {
                    "type": "object",
                    "required": [
                        "status",
                        "clientRevision",
                        "serverRevision",
                        "clientHash",
                    ],
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": [
                                "same",
                                "server_empty",
                                "client_changed",
                                "diverged",
                            ],
                        },
                        "clientRevision": {"type": "integer"},
                        "serverRevision": {"type": "integer"},
                        "clientHash": {"type": "string"},
                        "serverHash": {"type": ["string", "null"]},
                        "serverMetadata": {
                            "type": ["object", "null"],
                            "additionalProperties": True,
                        },
                    },
                    "additionalProperties": False,
                },
                "FilePayload": {
                    "type": "object",
                    "required": [
                        "relativePath",
                        "revision",
                        "contentHash",
                        "deleted",
                    ],
                    "properties": {
                        "relativePath": {"type": "string"},
                        "revision": {"type": "integer"},
                        "contentHash": {"type": ["string", "null"]},
                        "size": {"type": "integer"},
                        "deleted": {"type": "boolean"},
                        "serverUpdatedAt": {"type": ["string", "null"], "format": "date-time"},
                        "clientUpdatedAtMs": {"type": ["integer", "null"]},
                        "contentEncoding": {"type": "string", "enum": ["base64"]},
                        "content": {"type": "string"},
                        "gitCommit": {"type": ["string", "null"]},
                        "rolledBackToGitCommit": {"type": ["string", "null"]},
                    },
                    "additionalProperties": False,
                },
                "SyncPlanFileItem": {
                    "type": "object",
                    "required": ["relativePath", "reason"],
                    "properties": {
                        "relativePath": {"type": "string"},
                        "reason": {"type": "string"},
                        "note": {
                            "type": ["object", "null"],
                            "additionalProperties": True,
                        },
                        "workspace": {
                            "type": ["object", "null"],
                            "additionalProperties": True,
                        },
                        "serverFile": {
                            "anyOf": [
                                {"$ref": "#/components/schemas/FileRecord"},
                                {"type": "null"},
                            ]
                        },
                    },
                    "additionalProperties": True,
                },
                "SyncPlanConflict": {
                    "type": "object",
                    "required": ["relativePath", "reason"],
                    "properties": {
                        "type": {"type": "string"},
                        "relativePath": {"type": "string"},
                        "reason": {"type": "string"},
                        "clientNote": {
                            "type": ["object", "null"],
                            "additionalProperties": True,
                        },
                        "clientWorkspace": {
                            "type": ["object", "null"],
                            "additionalProperties": True,
                        },
                        "serverNote": {
                            "type": ["object", "null"],
                            "additionalProperties": True,
                        },
                        "serverWorkspace": {
                            "type": ["object", "null"],
                            "additionalProperties": True,
                        },
                        "serverFile": {
                            "anyOf": [
                                {"$ref": "#/components/schemas/FileRecord"},
                                {"type": "null"},
                            ]
                        },
                    },
                    "additionalProperties": True,
                },
                "SyncPlan": {
                    "type": "object",
                    "required": [
                        "uploadFiles",
                        "downloadFiles",
                        "deleteServerFiles",
                        "deleteLocalFiles",
                        "conflicts",
                    ],
                    "properties": {
                        "uploadFiles": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/SyncPlanFileItem"},
                        },
                        "downloadFiles": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/SyncPlanFileItem"},
                        },
                        "deleteServerFiles": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/SyncPlanFileItem"},
                        },
                        "deleteLocalFiles": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/SyncPlanFileItem"},
                        },
                        "conflicts": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/SyncPlanConflict"},
                        },
                    },
                    "additionalProperties": False,
                },
                "AcceptedFileResult": {
                    "type": "object",
                    "required": ["status", "relativePath", "revision"],
                    "properties": {
                        "status": {"type": "string", "enum": ["accepted", "unchanged"]},
                        "relativePath": {"type": "string"},
                        "revision": {"type": "integer"},
                        "contentHash": {"type": ["string", "null"]},
                        "deleted": {"type": "boolean"},
                        "gitCommit": {"type": ["string", "null"]},
                    },
                    "additionalProperties": False,
                },
                "ConflictFileResult": {
                    "type": "object",
                    "required": [
                        "status",
                        "relativePath",
                        "clientRevision",
                        "serverRevision",
                        "serverFile",
                    ],
                    "properties": {
                        "status": {"type": "string", "const": "conflict"},
                        "relativePath": {"type": "string"},
                        "clientRevision": {"type": "integer"},
                        "serverRevision": {"type": "integer"},
                        "serverFile": {"$ref": "#/components/schemas/FilePayload"},
                    },
                    "additionalProperties": False,
                },
                "FileRecord": {
                    "type": "object",
                    "required": ["relativePath", "revision", "deleted"],
                    "properties": {
                        "relativePath": {"type": "string"},
                        "revision": {"type": "integer"},
                        "contentHash": {"type": ["string", "null"]},
                        "size": {"type": "integer"},
                        "deleted": {"type": "boolean"},
                        "serverUpdatedAt": {"type": ["string", "null"], "format": "date-time"},
                        "clientUpdatedAtMs": {"type": ["integer", "null"]},
                        "gitCommit": {"type": ["string", "null"]},
                        "rolledBackToGitCommit": {"type": ["string", "null"]},
                    },
                    "additionalProperties": True,
                },
                "ClientRecord": {
                    "type": "object",
                    "required": ["lastSeenAt", "lastSeenRevision"],
                    "properties": {
                        "lastSeenAt": {"type": "string", "format": "date-time"},
                        "lastSyncAt": {"type": "string", "format": "date-time"},
                        "lastPlanAt": {"type": "string", "format": "date-time"},
                        "lastSeenRevision": {"type": "integer"},
                        "lastAction": {"type": "string"},
                        "user": {"type": ["string", "null"]},
                        "clientInfo": {"$ref": "#/components/schemas/ClientInfo"},
                        "connectionInfo": {
                            "$ref": "#/components/schemas/ConnectionInfo",
                        },
                    },
                    "additionalProperties": True,
                },
                "Manifest": {
                    "type": "object",
                    "required": [
                        "schemaVersion",
                        "serverRevision",
                        "updatedAt",
                        "metadata",
                        "files",
                        "clients",
                    ],
                    "properties": {
                        "schemaVersion": {"type": "integer"},
                        "serverRevision": {"type": "integer"},
                        "updatedAt": {"type": "string", "format": "date-time"},
                        "metadata": {
                            "type": "object",
                            "properties": {
                                "revision": {"type": "integer"},
                                "contentHash": {"type": ["string", "null"]},
                                "updatedAt": {"type": ["string", "null"], "format": "date-time"},
                            },
                            "additionalProperties": True,
                        },
                        "files": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/FileRecord"},
                        },
                        "clients": {
                            "type": "object",
                            "additionalProperties": {
                                "$ref": "#/components/schemas/ClientRecord"
                            },
                        },
                    },
                    "additionalProperties": False,
                },
                "SyncResponse": {
                    "type": "object",
                    "required": [
                        "status",
                        "serverRevision",
                        "syncedAt",
                        "accepted",
                        "conflicts",
                        "remoteChanges",
                        "manifest",
                    ],
                    "properties": {
                        "status": {"type": "string", "enum": ["ok", "conflict"]},
                        "serverRevision": {"type": "integer"},
                        "syncedAt": {"type": "string", "format": "date-time"},
                        "metadata": {"$ref": "#/components/schemas/MetadataSyncResult"},
                        "accepted": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/AcceptedFileResult"},
                        },
                        "conflicts": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/ConflictFileResult"},
                        },
                        "remoteChanges": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/FilePayload"},
                        },
                        "manifest": {"$ref": "#/components/schemas/Manifest"},
                    },
                    "additionalProperties": False,
                },
                "SyncPlanResponse": {
                    "type": "object",
                    "required": [
                        "status",
                        "serverRevision",
                        "plannedAt",
                        "metadata",
                        "plan",
                        "manifest",
                    ],
                    "properties": {
                        "status": {"type": "string", "enum": ["ok", "conflict"]},
                        "serverRevision": {"type": "integer"},
                        "plannedAt": {"type": "string", "format": "date-time"},
                        "metadata": {"$ref": "#/components/schemas/MetadataPlanResult"},
                        "plan": {"$ref": "#/components/schemas/SyncPlan"},
                        "manifest": {"$ref": "#/components/schemas/Manifest"},
                    },
                    "additionalProperties": False,
                },
                "SingleFileSyncResponse": {
                    "type": "object",
                    "required": [
                        "status",
                        "serverRevision",
                        "syncedAt",
                        "file",
                        "manifest",
                    ],
                    "properties": {
                        "status": {"type": "string", "enum": ["ok", "conflict"]},
                        "serverRevision": {"type": "integer"},
                        "syncedAt": {"type": "string", "format": "date-time"},
                        "file": {
                            "anyOf": [
                                {"$ref": "#/components/schemas/AcceptedFileResult"},
                                {"$ref": "#/components/schemas/ConflictFileResult"},
                            ]
                        },
                        "metadata": {"$ref": "#/components/schemas/MetadataSyncResult"},
                        "manifest": {"$ref": "#/components/schemas/Manifest"},
                    },
                    "additionalProperties": False,
                },
            },
        },
    }
