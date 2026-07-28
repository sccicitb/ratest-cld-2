import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import * as api from "@/lib/api";
import type { KBFilters } from "@/types/kb";

export const queryKeys = {
  sessions: ["sessions"] as const,
  session: (id: string) => ["sessions", id] as const,
  messages: (id: string) => ["sessions", id, "messages"] as const,
  kbFiles: (filters?: KBFilters) => ["kb-files", filters ?? {}] as const,
  sessionFiles: (sessionId: string) =>
    ["session-files", sessionId] as const,
};

// --- Sessions ------------------------------------------------------ //
export function useSessions() {
  return useQuery({
    queryKey: queryKeys.sessions,
    queryFn: api.getSessions,
    // Poll while any session has a live turn, so the sidebar's "generating"
    // dot and resume-on-entry stay fresh without a page reload.
    refetchInterval: (query) => {
      const sessions = query.state.data as { activeTurn?: boolean }[] | undefined;
      if (!sessions || sessions.length === 0) return false;
      const anyActive = sessions.some((s) => s.activeTurn);
      return anyActive ? 2500 : false;
    },
  });
}

export function useSession(id: string) {
  return useQuery({
    queryKey: queryKeys.session(id),
    queryFn: () => api.getSession(id),
    enabled: !!id,
  });
}

export function useMessages(sessionId: string) {
  return useQuery({
    queryKey: queryKeys.messages(sessionId),
    queryFn: () => api.getMessages(sessionId),
    enabled: !!sessionId,
  });
}

export function useCreateSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createSession,
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.sessions }),
  });
}

export function useRenameSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      api.renameSession(id, title),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.sessions }),
  });
}

export function useDeleteSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteSession(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.sessions }),
  });
}

// --- Knowledge base ------------------------------------------------ //
export function useKnowledgeBaseFiles(filters?: KBFilters) {
  return useQuery({
    queryKey: queryKeys.kbFiles(filters),
    queryFn: () => api.getKnowledgeBaseFiles(filters),
    // Poll while any file is indexing (§8.3: "poll-while-indexing").
    refetchInterval: (query) => {
      const files = query.state.data as { status?: string }[] | undefined;
      if (!files || files.length === 0) return false;
      const anyIndexing = files.some((f) => f.status === "indexing");
      return anyIndexing ? 2500 : false;
    },
  });
}

export function useDeleteKBFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteKnowledgeBaseFile(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["kb-files"] }),
  });
}

export function useReindexKBFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.reindexKnowledgeBaseFile(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["kb-files"] }),
  });
}

export function useSessionFiles(sessionId: string) {
  return useQuery({
    queryKey: queryKeys.sessionFiles(sessionId),
    queryFn: () => api.getSessionFiles(sessionId),
    enabled: !!sessionId,
  });
}

export function useUpdateFileTags() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, tags }: { id: string; tags: string[] }) =>
      api.updateFileTags(id, tags),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["kb-files"] }),
  });
}
