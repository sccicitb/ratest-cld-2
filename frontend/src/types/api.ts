export interface User {
  id: string;
  email: string;
  displayName: string;
  isAdmin: boolean;
  avatarUrl?: string;
}

export interface AuthResponse {
  accessToken: string;
  user: User;
}

export interface LoginCredentials {
  email: string;
  password: string;
}
