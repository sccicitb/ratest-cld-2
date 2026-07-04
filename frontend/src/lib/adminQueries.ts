/**
 * React Query hooks for the /api/admin/* endpoints.
 * All mutations invalidate the relevant query keys so tables refresh live.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import * as api from "@/lib/api";
import type {
  ChangePasswordPayload,
  CreateGroupPayload,
  CreateMcpServerPayload,
  CreateUserPayload,
  PatchGroupPayload,
  PatchMcpServerPayload,
  PatchUserPayload,
} from "@/types/admin";

/* ── Query keys ─────────────────────────────────────────────────────────── */

export const adminQueryKeys = {
  users: ["admin", "users"] as const,
  groups: ["admin", "groups"] as const,
  group: (id: string) => ["admin", "groups", id] as const,
  mcpServers: ["admin", "mcp-servers"] as const,
};

/* ── Queries ─────────────────────────────────────────────────────────────── */

export function useAdminUsers() {
  return useQuery({ queryKey: adminQueryKeys.users, queryFn: api.adminListUsers });
}

export function useAdminGroups() {
  return useQuery({ queryKey: adminQueryKeys.groups, queryFn: api.adminListGroups });
}

export function useAdminGroup(id: string | null) {
  return useQuery({
    queryKey: adminQueryKeys.group(id ?? ""),
    queryFn: () => api.adminGetGroup(id!),
    enabled: !!id,
  });
}

export function useAdminMcpServers() {
  return useQuery({ queryKey: adminQueryKeys.mcpServers, queryFn: api.adminListMcpServers });
}

/* ── User mutations ──────────────────────────────────────────────────────── */

export function useAdminCreateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateUserPayload) => api.adminCreateUser(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminQueryKeys.users }),
  });
}

export function useAdminPatchUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: PatchUserPayload }) =>
      api.adminPatchUser(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminQueryKeys.users }),
  });
}

export function useAdminResetPassword() {
  return useMutation({
    mutationFn: (id: string) => api.adminResetPassword(id),
  });
}

/* ── Group mutations ─────────────────────────────────────────────────────── */

export function useAdminCreateGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateGroupPayload) => api.adminCreateGroup(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminQueryKeys.groups }),
  });
}

export function useAdminPatchGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: PatchGroupPayload }) =>
      api.adminPatchGroup(id, data),
    onSuccess: (_res, { id }) => {
      void qc.invalidateQueries({ queryKey: adminQueryKeys.groups });
      void qc.invalidateQueries({ queryKey: adminQueryKeys.group(id) });
    },
  });
}

export function useAdminDeleteGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.adminDeleteGroup(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminQueryKeys.groups }),
  });
}

export function useAdminSetGroupMembers() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, userIds }: { id: string; userIds: string[] }) =>
      api.adminSetGroupMembers(id, userIds),
    onSuccess: (_, { id }) => {
      void qc.invalidateQueries({ queryKey: adminQueryKeys.group(id) });
      void qc.invalidateQueries({ queryKey: adminQueryKeys.users });
      void qc.invalidateQueries({ queryKey: adminQueryKeys.groups });
    },
  });
}

export function useAdminSetGroupServers() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, serverIds }: { id: string; serverIds: string[] }) =>
      api.adminSetGroupServers(id, serverIds),
    onSuccess: (_, { id }) => {
      void qc.invalidateQueries({ queryKey: adminQueryKeys.group(id) });
      void qc.invalidateQueries({ queryKey: adminQueryKeys.groups });
    },
  });
}

/* ── MCP server mutations ────────────────────────────────────────────────── */

export function useAdminCreateMcpServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateMcpServerPayload) => api.adminCreateMcpServer(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminQueryKeys.mcpServers }),
  });
}

export function useAdminPatchMcpServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: PatchMcpServerPayload }) =>
      api.adminPatchMcpServer(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminQueryKeys.mcpServers }),
  });
}

export function useAdminDeleteMcpServer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.adminDeleteMcpServer(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: adminQueryKeys.mcpServers }),
  });
}

export function useAdminTestMcpServer() {
  return useMutation({
    mutationFn: (id: string) => api.adminTestMcpServer(id),
  });
}

/* ── Self ────────────────────────────────────────────────────────────────── */

export function useChangePassword() {
  return useMutation({
    mutationFn: (data: ChangePasswordPayload) => api.changePassword(data),
  });
}
