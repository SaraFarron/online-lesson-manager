/**
 * Core type definitions for the calendar application
 */

export interface CalendarEvent {
  id: string;
  title: string;
  date: string; // ISO date string (YYYY-MM-DD)
  startTime: string; // HH:MM format
  duration: number; // in minutes
  isRecurring: boolean;
  recurringGroupId?: string; // Links recurring events together
}

export interface TimeSlot {
  hour: number;
  minute: number;
}

export interface DayAvailability {
  date: string;
  hasAvailableSlots: boolean;
  events: CalendarEvent[];
}

// Unavailable time slots (external bookings)
export interface UnavailableSlot {
  date: string; // ISO date string
  startTime: string; // HH:MM format
  endTime: string; // HH:MM format
}

// Work day configuration
export interface WorkDayConfig {
  startHour: number; // Start hour (0-23)
  endHour: number; // End hour (0-23)
}

export type CalendarView = 'month' | 'week';

/**
 * Language type definitions
 */

export type Language = 'en' | 'ru' | 'zh';

export interface Translations {
  // App header
  appTitle: string;
  help: string;
  helpTitle: string;
  helpContent: string;
  
  // Navigation
  month: string;
  week: string;
  logout: string;
  today: string;
  previous: string;
  next: string;
  
  // Login
  loginTitle: string;
  loginSubtitle: string;
  accessToken: string;
  enterToken: string;
  tokenLength: string;
  signIn: string;
  verifying: string;
  tokenError: string;
  testingNote: string;
  
  // Event Modal
  createEvent: string;
  editEvent: string;
  eventTitle: string;
  enterEventTitle: string;
  date: string;
  startTime: string;
  duration: string;
  repeatWeekly: string;
  repeatWeeklyNote: string;
  deleteThis: string;
  deleteAllWeekly: string;
  delete: string;
  cancel: string;
  create: string;
  update: string;
  
  // Week View
  weekOf: string;
  addLesson: string;
  addWeeklyLesson: string;
  generateExternalBookings: string;
  lesson: string;
  
  // Validation errors
  errorTitleRequired: string;
  errorInvalidDate: string;
  errorInvalidTime: string;
  errorInvalidDuration: string;
  errorEventOverlap: string;
  errorSlotUnavailable: string;
  errorPastDate: string;
  
  // Time formats
  hours: string;
  minutes: string;
  
  // Days of week
  monday: string;
  tuesday: string;
  wednesday: string;
  thursday: string;
  friday: string;
  saturday: string;
  sunday: string;
  time: string;
  
  // Short days
  mon: string;
  tue: string;
  wed: string;
  thu: string;
  fri: string;
  sat: string;
  sun: string;
  
  // Month names
  january: string;
  february: string;
  march: string;
  april: string;
  may: string;
  june: string;
  july: string;
  august: string;
  september: string;
  october: string;
  november: string;
  december: string;
}
