/**
 * Custom hook for managing calendar events
 * Handles CRUD operations and syncs with localStorage
 */

import { useState, useEffect } from 'react';
import { CalendarEvent } from '../types';
import { eventService } from '../services/eventService';
import { v4 as uuidv4 } from 'uuid';
import { addWeeks, format } from 'date-fns';

export const useCalendarEvents = () => {
  const [events, setEvents] = useState<CalendarEvent[]>([]);

  // Load events on mount
  useEffect(() => {
    const loadedEvents = eventService.loadEvents();
    setEvents(loadedEvents);
  }, []);

  const addEvent = (eventData: Omit<CalendarEvent, 'id'>) => {
    const newEvent: CalendarEvent = {
      ...eventData,
      id: uuidv4(),
    };

    // If recurring, generate weekly events for the next 12 weeks
    if (eventData.isRecurring) {
      const recurringGroupId = uuidv4();
      const recurringEvents: CalendarEvent[] = [];
      
      for (let i = 0; i < 12; i++) {
        const eventDate = addWeeks(new Date(eventData.date), i);
        recurringEvents.push({
          ...eventData,
          id: uuidv4(),
          date: format(eventDate, 'yyyy-MM-dd'),
          recurringGroupId,
        });
      }

      recurringEvents.forEach(event => eventService.addEvent(event));
      setEvents(eventService.loadEvents());
    } else {
      const updatedEvents = eventService.addEvent(newEvent);
      setEvents(updatedEvents);
    }
  };

  const updateEvent = (eventId: string, eventData: Omit<CalendarEvent, 'id'>) => {
    const updatedEvent: CalendarEvent = {
      ...eventData,
      id: eventId,
    };
    const updatedEvents = eventService.updateEvent(updatedEvent);
    setEvents(updatedEvents);
  };

  const deleteEvent = (eventId: string) => {
    const updatedEvents = eventService.deleteEvent(eventId);
    setEvents(updatedEvents);
  };

  const deleteRecurringGroup = (recurringGroupId: string) => {
    const updatedEvents = eventService.deleteRecurringGroup(recurringGroupId);
    setEvents(updatedEvents);
  };

  const moveEvent = (eventId: string, newDate: string, newStartTime: string) => {
    const event = events.find(e => e.id === eventId);
    if (event) {
      updateEvent(eventId, {
        ...event,
        date: newDate,
        startTime: newStartTime,
      });
    }
  };

  return {
    events,
    addEvent,
    updateEvent,
    deleteEvent,
    deleteRecurringGroup,
    moveEvent,
  };
};
