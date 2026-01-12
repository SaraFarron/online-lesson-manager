/**
 * LoginView component - handles user authentication via token
 */

import React, { useState } from 'react';
import { Language } from '../types';
import { LanguageSwitch } from './LanguageSwitch';
import { i18nService } from '../services/i18nService';

interface LoginViewProps {
  onLogin: (token: string) => void;
  currentLanguage: Language;
  onLanguageChange: (language: Language) => void;
}

export const LoginView: React.FC<LoginViewProps> = ({ onLogin, currentLanguage, onLanguageChange }) => {
  const [token, setToken] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const t = i18nService.getTranslations(currentLanguage);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    // Validate token length
    if (token.length < 4 || token.length > 16) {
      setError(t.tokenError);
      return;
    }

    setIsLoading(true);

    // Simulate API call delay
    setTimeout(() => {
      // For now, accept any token between 4-16 characters
      // Later this will be replaced with actual backend verification
      onLogin(token);
      setIsLoading(false);
    }, 500);
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 via-white to-purple-50 flex items-center justify-center px-4">
      <div className="max-w-md w-full">
        {/* Language Switcher - Top Right */}
        <div className="flex justify-end mb-4">
          <LanguageSwitch
            currentLanguage={currentLanguage}
            onLanguageChange={onLanguageChange}
          />
        </div>

        {/* App Title */}
        <div className="text-center mb-8">
          <h1 className="text-4xl font-bold text-gray-900 mb-2">{t.loginTitle}</h1>
          <p className="text-gray-600">{t.loginSubtitle}</p>
        </div>

        {/* Login Card */}
        <div className="bg-white rounded-lg shadow-xl p-8">
          <form onSubmit={handleSubmit} className="space-y-6">
            {/* Token Input */}
            <div>
              <label htmlFor="token" className="block text-sm font-medium text-gray-700 mb-2">
                {t.accessToken}
              </label>
              <input
                id="token"
                type="text"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="w-full px-4 py-3 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                placeholder={t.enterToken}
                minLength={4}
                maxLength={16}
                disabled={isLoading}
                autoFocus
              />
              <p className="mt-2 text-xs text-gray-500">
                {t.tokenLength}
              </p>
            </div>

            {/* Error Message */}
            {error && (
              <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-md text-sm">
                {error}
              </div>
            )}

            {/* Submit Button */}
            <button
              type="submit"
              disabled={isLoading || token.length < 4 || token.length > 16}
              className="w-full bg-blue-600 text-white py-3 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors font-medium"
            >
              {isLoading ? (
                <span className="flex items-center justify-center">
                  <svg className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                  {t.verifying}
                </span>
              ) : (
                t.signIn
              )}
            </button>
          </form>
        </div>

        {/* Footer */}
        <div className="text-center mt-6 text-sm text-gray-600">
          <p>{t.testingNote}</p>
        </div>
      </div>
    </div>
  );
};
