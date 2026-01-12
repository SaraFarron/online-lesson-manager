/**
 * Main App component - entry point for the calendar application
 */

import { useState, useEffect } from 'react';
import { MonthView } from './components/MonthView';
import { WeekView } from './components/WeekView';
import { LoginView } from './components/LoginView';
import { LanguageSwitch } from './components/LanguageSwitch';
import { HelpModal } from './components/HelpModal';
import { useCalendarEvents } from './hooks/useCalendarEvents';
import { unavailableSlotService } from './services/unavailableSlotService';
import { authService } from './services/authService';
import { i18nService } from './services/i18nService';
import { CalendarView, UnavailableSlot, WorkDayConfig, Language } from './types';
import './index.css';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [currentLanguage, setCurrentLanguage] = useState<Language>('en');
  const [isHelpModalOpen, setIsHelpModalOpen] = useState(false);

  const {
    events,
    addEvent,
    updateEvent,
    deleteEvent,
    deleteRecurringGroup,
    moveEvent,
  } = useCalendarEvents();

  const [currentView, setCurrentView] = useState<CalendarView>('week'); // Default to week view
  const [currentDate, setCurrentDate] = useState(new Date());
  const [unavailableSlots, setUnavailableSlots] = useState<UnavailableSlot[]>([]);
  
  // Work day configuration - will be fetched from backend in the future
  const workDayConfig: WorkDayConfig = {
    startHour: 9,
    endHour: 20,
  };

  // Initialize language on mount
  useEffect(() => {
    const language = i18nService.getCurrentLanguage();
    setCurrentLanguage(language);
    
    const authenticated = authService.isAuthenticated();
    setIsAuthenticated(authenticated);
    setIsLoading(false);
  }, []);

  // Load unavailable slots on mount (only if authenticated)
  useEffect(() => {
    if (isAuthenticated) {
      const slots = unavailableSlotService.loadSlots();
      setUnavailableSlots(slots);
    }
  }, [isAuthenticated]);

  const handleLogin = (token: string) => {
    authService.saveToken(token);
    setIsAuthenticated(true);
  };

  const handleLogout = () => {
    authService.removeToken();
    setIsAuthenticated(false);
    setCurrentView('month');
  };

  const handleLanguageChange = (language: Language) => {
    i18nService.setLanguage(language);
    setCurrentLanguage(language);
  };

  // Get translations for current language
  const t = i18nService.getTranslations(currentLanguage);

  // Show loading state briefly on initial mount
  if (isLoading) {
    return (
      <div className="h-screen flex items-center justify-center bg-gray-50">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Loading...</p>
        </div>
      </div>
    );
  }

  // Show login view if not authenticated
  if (!isAuthenticated) {
    return <LoginView onLogin={handleLogin} currentLanguage={currentLanguage} onLanguageChange={handleLanguageChange} />;
  }

  // Show calendar app if authenticated
  return (
    <div className="h-screen flex flex-col bg-gray-50">
      <header className="bg-white shadow-sm">
        <div className="max-w-7xl mx-auto px-3 sm:px-5 lg:px-6 py-3 sm:py-4">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between space-y-3 sm:space-y-0">
            <div className="flex-1">
              <h1 className="text-xl sm:text-2xl md:text-3xl font-bold text-gray-900">{t.appTitle}</h1>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setIsHelpModalOpen(true)}
                className="flex items-center justify-center w-8 h-8 bg-blue-100 text-blue-600 rounded-full hover:bg-blue-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
                title={t.help}
                aria-label={t.help}
              >
                <span className="text-lg font-bold">?</span>
              </button>
              <LanguageSwitch
                currentLanguage={currentLanguage}
                onLanguageChange={handleLanguageChange}
              />
              <div className="flex space-x-2">
                {/* Hide month button on mobile (md:flex) */}
                <button
                  onClick={() => setCurrentView('month')}
                  className={`hidden md:flex px-3 sm:px-4 py-1.5 sm:py-2 text-sm rounded-md focus:outline-none focus:ring-2 ${
                    currentView === 'month'
                      ? 'bg-blue-600 text-white hover:bg-blue-700 focus:ring-blue-500'
                      : 'bg-gray-200 text-gray-700 hover:bg-gray-300 focus:ring-gray-400'
                  }`}
                >
                  {t.month}
                </button>
                <button
                  onClick={() => setCurrentView('week')}
                  className={`px-3 sm:px-4 py-1.5 sm:py-2 text-sm rounded-md focus:outline-none focus:ring-2 ${
                    currentView === 'week'
                      ? 'bg-blue-600 text-white hover:bg-blue-700 focus:ring-blue-500'
                      : 'bg-gray-200 text-gray-700 hover:bg-gray-300 focus:ring-gray-400'
                  }`}
                >
                  {t.week}
                </button>
                <button
                  onClick={handleLogout}
                  className="px-3 sm:px-4 py-1.5 sm:py-2 text-sm bg-red-600 text-white rounded-md hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500"
                  title={t.logout}
                >
                  <span className="hidden sm:inline">{t.logout}</span>
                  <span className="sm:hidden">✕</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </header>

      <main className="flex-1 overflow-hidden max-w-7xl mx-auto w-full px-0 sm:px-5 lg:px-6 py-0 sm:py-4">
        {currentView === 'month' ? (
          <MonthView
            events={events}
            onAddEvent={addEvent}
            onUpdateEvent={updateEvent}
            onDeleteEvent={deleteEvent}
            onDeleteRecurringGroup={deleteRecurringGroup}
            onMoveEvent={moveEvent}
            translations={t}
          />
        ) : (
          <WeekView
            currentDate={currentDate}
            events={events}
            unavailableSlots={unavailableSlots}
            workDayConfig={workDayConfig}
            onAddEvent={addEvent}
            onUpdateEvent={updateEvent}
            onDeleteEvent={deleteEvent}
            onDeleteRecurringGroup={deleteRecurringGroup}
            onWeekChange={setCurrentDate}
            translations={t}
          />
        )}
      </main>

      {/* Help Modal */}
      <HelpModal
        isOpen={isHelpModalOpen}
        onClose={() => setIsHelpModalOpen(false)}
        translations={t}
      />
    </div>
  );
}

export default App;
