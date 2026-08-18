export interface User {
  id: string;
  email: string;
  displayName: string;
  isAdmin: boolean;
  avatarUrl?: string;
  /** TTS voice style (§1b). One of M1-M5 / F1-F5; defaults to F2. */
  voice: string;
}

export interface AuthResponse {
  accessToken: string;
  user: User;
}

export interface LoginCredentials {
  email: string;
  password: string;
}
