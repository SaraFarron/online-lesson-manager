/**
 * Authentication service - manages user token
 */

const TOKEN_STORAGE_KEY = 'user_token';

export const authService = {
  /**
   * Save token to localStorage
   */
  saveToken: (token: string): void => {
    try {
      localStorage.setItem(TOKEN_STORAGE_KEY, token);
    } catch (error) {
      console.error('Failed to save token:', error);
    }
  },

  /**
   * Get token from localStorage
   */
  getToken: (): string | null => {
    try {
      return localStorage.getItem(TOKEN_STORAGE_KEY);
    } catch (error) {
      console.error('Failed to get token:', error);
      return null;
    }
  },

  /**
   * Remove token from localStorage
   */
  removeToken: (): void => {
    try {
      localStorage.removeItem(TOKEN_STORAGE_KEY);
    } catch (error) {
      console.error('Failed to remove token:', error);
    }
  },

  /**
   * Check if user is authenticated
   */
  isAuthenticated: (): boolean => {
    const token = authService.getToken();
    return token !== null && token.length >= 4 && token.length <= 16;
  },
};
