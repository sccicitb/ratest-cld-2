/**
 * Auth helpers — bridge the API layer and the Zustand auth store.
 *
 * Token is held in memory (never persisted). On app load `checkAuth` calls
 * /auth/refresh (httpOnly cookie, §4), which returns AuthResponse in one
 * round-trip. `api.ts` reads the token via `setTokenSource`.
 */
import * as api from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import type { LoginCredentials } from "@/types/api";

let inMemoryToken: string | null = null;

export function getToken(): string | null {
  return inMemoryToken ?? useAuthStore.getState().accessToken;
}

// Register with the API layer so `authHeaders()` gets the current token.
api.setTokenSource(getToken);

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
  try { await api.logout(); } finally {
    inMemoryToken = null;
    useAuthStore.getState().clearAuth();
  }
}

export async function checkAuth(): Promise<boolean> {
  const store = useAuthStore.getState();
  store.setLoading(true);
  try {
    const { user, accessToken } = await api.refresh();
    inMemoryToken = accessToken;
    store.setAuth(user, accessToken);
    return true;
  } catch {
    store.clearAuth();
    return false;
  }
}
