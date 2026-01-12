/**
 * Data service for event persistence
 * Uses localStorage for now, but structured to easily swap with API calls later
 */

import { CalendarEvent } from '../types';

const STORAGE_KEY = 'calendar_events';

/**
 * Abstract interface for data operations
 * Replace implementation to switch from localStorage to API
 */
export const eventService = {
  /**
   * Load all events from storage
   */
  loadEvents: (): CalendarEvent[] => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored ? JSON.parse(stored) : [];
    } catch (error) {
      console.error('Failed to load events:', error);
      return [];
    }
  },

  /**
   * Save all events to storage
   */
  saveEvents: (events: CalendarEvent[]): void => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(events));
    } catch (error) {
      console.error('Failed to save events:', error);
    }
  },

  /**
   * Add a single event
   */
  addEvent: (event: CalendarEvent): CalendarEvent[] => {
    const events = eventService.loadEvents();
    const newEvents = [...events, event];
    eventService.saveEvents(newEvents);
    return newEvents;
  },

  /**
   * Update an existing event
   */
  updateEvent: (updatedEvent: CalendarEvent): CalendarEvent[] => {
    const events = eventService.loadEvents();
    const newEvents = events.map(event =>
      event.id === updatedEvent.id ? updatedEvent : event
    );
    eventService.saveEvents(newEvents);
    return newEvents;
  },

  /**
   * Delete an event by ID
   */
  deleteEvent: (eventId: string): CalendarEvent[] => {
    const events = eventService.loadEvents();
    const newEvents = events.filter(event => event.id !== eventId);
    eventService.saveEvents(newEvents);
    return newEvents;
  },

  /**
   * Delete all events in a recurring group
   */
  deleteRecurringGroup: (recurringGroupId: string): CalendarEvent[] => {
    const events = eventService.loadEvents();
    const newEvents = events.filter(
      event => event.recurringGroupId !== recurringGroupId
    );
    eventService.saveEvents(newEvents);
    return newEvents;
  },
};
