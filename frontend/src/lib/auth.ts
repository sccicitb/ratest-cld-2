/**
 * Auth helpers — bridge the API layer and the Zustand auth store.
 * The token is kept in memory only (never persisted to storage).
 */
import * as api from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import type { LoginCredentials } from "@/types/api";

// In-memory token reference so non-React modules can read it if needed.
let inMemoryToken: string | null = null;

export function getToken(): string | null {
  return inMemoryToken ?? useAuthStore.getState().accessToken;
}

export async function login(credentials: LoginCredentials): Promise<void> {
  useAuthStore.getState().setLoading(true);
  try {
    const { user, accessToken } = await api.login(credentials);
    inMemoryToken = accessToken;
    useAuthStore.getState().setAuth(user, accessToken);
  } catch (err) {
    useAuthStore.getState().clearAuth();
    throw err;
  }
}

export async function logout(): Promise<void> {
  try {
    await api.logout();
  } finally {
    inMemoryToken = null;
    useAuthStore.getState().clearAuth();
  }
}

/**
 * Validate the current in-memory token. In this mock app there's no persisted
 * session, so on a fresh load this resolves to "not authenticated" and the
 * user is routed to /login.
 */
export async function checkAuth(): Promise<boolean> {
  const store = useAuthStore.getState();
  const token = getToken();
  if (!token) {
    store.clearAuth();
    return false;
  }
  store.setLoading(true);
  try {
    const user = await api.getMe(token);
    store.setAuth(user, token);
    return true;
  } catch {
    store.clearAuth();
    return false;
  }
}
