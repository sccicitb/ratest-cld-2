/* Admin resource types — camelCase, mirrors backend UserOut/GroupOut/GroupDetailOut/MCPServerOut */

export interface AdminUser {
  id: string;
  email: string;
  displayName: string;
  isAdmin: boolean;
  disabled: boolean;
  groupIds: string[];
  createdAt: string;
}

export interface Group {
  id: string;
  name: string;
  defaultTags: string[];
  memberCount: number;
  createdAt: string;
}

export interface GroupDetail extends Group {
  members: AdminUser[];
  mcpServerIds: string[];
}

export interface MCPServer {
  id: string;
  name: string;
  transport: string;
  url: string;
  authType: "none" | "bearer";
  enabled: boolean;
  createdAt: string;
}

export interface TestMcpResult {
  ok: boolean;
  tools?: string[];
  error?: string;
}

/* ── Request payloads ─────────────────────────────────────────────────────── */

export interface CreateUserPayload {
  email: string;
  displayName: string;
  password: string;
  isAdmin?: boolean;
}

export interface PatchUserPayload {
  disabled?: boolean;
  isAdmin?: boolean;
  displayName?: string;
}

export interface CreateGroupPayload {
  name: string;
  defaultTags?: string[];
}

export interface PatchGroupPayload {
  name?: string;
  defaultTags?: string[];
}

export interface CreateMcpServerPayload {
  name: string;
  url: string;
  transport?: string;
  authType?: "none" | "bearer";
  token?: string;
  enabled?: boolean;
}

export interface PatchMcpServerPayload {
  name?: string;
  url?: string;
  authType?: "none" | "bearer";
  token?: string;
  enabled?: boolean;
}

export interface ChangePasswordPayload {
  oldPassword: string;
  newPassword: string;
}
