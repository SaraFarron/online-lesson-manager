/**
 * LanguageSwitch component - allows users to switch between languages
 * Uses flag emojis to represent languages visually
 */

import React from 'react';
import { Language } from '../types';

interface LanguageSwitchProps {
  currentLanguage: Language;
  onLanguageChange: (language: Language) => void;
}

const languageOptions: { code: Language; flag: string; name: string }[] = [
  { code: 'en', flag: '🇬🇧', name: 'English' },
  { code: 'ru', flag: '🇷🇺', name: 'Русский' },
  { code: 'zh', flag: '🇨🇳', name: '中文' },
];

export const LanguageSwitch: React.FC<LanguageSwitchProps> = ({
  currentLanguage,
  onLanguageChange,
}) => {
  const [isOpen, setIsOpen] = React.useState(false);
  const currentOption = languageOptions.find(opt => opt.code === currentLanguage) || languageOptions[0];

  const handleLanguageSelect = (language: Language) => {
    onLanguageChange(language);
    setIsOpen(false);
  };

  return (
    <div className="relative">
      {/* Language Button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center space-x-2 px-3 py-2 bg-white border border-gray-300 rounded-md hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500 transition-colors"
        aria-label="Change language"
      >
        <span className="text-2xl">{currentOption.flag}</span>
        <svg
          className={`w-4 h-4 text-gray-600 transition-transform ${isOpen ? 'rotate-180' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Dropdown Menu */}
      {isOpen && (
        <>
          {/* Backdrop to close dropdown */}
          <div
            className="fixed inset-0 z-40"
            onClick={() => setIsOpen(false)}
          />
          
          {/* Language Options - Ensure it's above everything with z-50 */}
          <div className="absolute left-0 sm:right-0 sm:left-auto mt-2 w-48 bg-white border border-gray-200 rounded-md shadow-lg z-50">
            {languageOptions.map((option) => (
              <button
                key={option.code}
                onClick={() => handleLanguageSelect(option.code)}
                className={`w-full flex items-center space-x-3 px-4 py-3 text-left hover:bg-gray-100 transition-colors ${
                  option.code === currentLanguage ? 'bg-blue-50' : ''
                }`}
              >
                <span className="text-2xl">{option.flag}</span>
                <span className={`text-sm ${
                  option.code === currentLanguage ? 'font-semibold text-blue-600' : 'text-gray-700'
                }`}>
                  {option.name}
                </span>
                {option.code === currentLanguage && (
                  <svg
                    className="ml-auto w-5 h-5 text-blue-600"
                    fill="currentColor"
                    viewBox="0 0 20 20"
                  >
                    <path
                      fillRule="evenodd"
                      d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                      clipRule="evenodd"
                    />
                  </svg>
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
};
