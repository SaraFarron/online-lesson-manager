/**
 * Validation utilities for date, time, and event inputs
 */

import { CalendarEvent, UnavailableSlot, Translations } from '../types';
import { parse, isValid, addMinutes } from 'date-fns';

export const validateTimeString = (time: string): boolean => {
  const timeRegex = /^([0-1]?[0-9]|2[0-3]):([0-5][0-9])$/;
  return timeRegex.test(time);
};

export const validateDateString = (date: string): boolean => {
  const dateRegex = /^\d{4}-\d{2}-\d{2}$/;
  if (!dateRegex.test(date)) return false;
  
  const parsed = parse(date, 'yyyy-MM-dd', new Date());
  return isValid(parsed);
};

export const validateDuration = (duration: number): boolean => {
  return duration >= 5 && duration % 5 === 0 && duration <= 1440; // Max 24 hours
};

export const validateEventTitle = (title: string): boolean => {
  return title.trim().length > 0 && title.length <= 100;
};

export const checkEventOverlap = (
  newEvent: Omit<CalendarEvent, 'id'>,
  existingEvents: CalendarEvent[],
  excludeEventId?: string
): boolean => {
  const newStart = parse(`${newEvent.date} ${newEvent.startTime}`, 'yyyy-MM-dd HH:mm', new Date());
  const newEnd = addMinutes(newStart, newEvent.duration);

  return existingEvents.some(event => {
    if (excludeEventId && event.id === excludeEventId) return false;
    if (event.date !== newEvent.date) return false;

    const eventStart = parse(`${event.date} ${event.startTime}`, 'yyyy-MM-dd HH:mm', new Date());
    const eventEnd = addMinutes(eventStart, event.duration);

    // Check if events overlap
    return (
      (newStart >= eventStart && newStart < eventEnd) ||
      (newEnd > eventStart && newEnd <= eventEnd) ||
      (newStart <= eventStart && newEnd >= eventEnd)
    );
  });
};

export const checkUnavailableSlotConflict = (
  newEvent: Omit<CalendarEvent, 'id'>,
  unavailableSlots: UnavailableSlot[]
): boolean => {
  const newStart = parse(`${newEvent.date} ${newEvent.startTime}`, 'yyyy-MM-dd HH:mm', new Date());
  const newEnd = addMinutes(newStart, newEvent.duration);

  return unavailableSlots.some(slot => {
    if (slot.date !== newEvent.date) return false;

    const slotStart = parse(`${slot.date} ${slot.startTime}`, 'yyyy-MM-dd HH:mm', new Date());
    const slotEnd = parse(`${slot.date} ${slot.endTime}`, 'yyyy-MM-dd HH:mm', new Date());

    // Check if event conflicts with unavailable slot
    return (
      (newStart >= slotStart && newStart < slotEnd) ||
      (newEnd > slotStart && newEnd <= slotEnd) ||
      (newStart <= slotStart && newEnd >= slotEnd)
    );
  });
};

export const validateEvent = (
  event: Omit<CalendarEvent, 'id'>,
  existingEvents: CalendarEvent[],
  excludeEventId?: string,
  unavailableSlots?: UnavailableSlot[],
  translations?: Translations
): { valid: boolean; error?: string } => {
  if (!validateEventTitle(event.title)) {
    return { valid: false, error: translations?.errorTitleRequired || 'Event title is required (max 100 characters)' };
  }

  if (!validateDateString(event.date)) {
    return { valid: false, error: translations?.errorInvalidDate || 'Invalid date format' };
  }

  // Check if date is in the past
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const eventDate = new Date(event.date);
  eventDate.setHours(0, 0, 0, 0);
  
  if (eventDate < today) {
    return { valid: false, error: translations?.errorPastDate || 'Cannot create events in the past' };
  }

  if (!validateTimeString(event.startTime)) {
    return { valid: false, error: translations?.errorInvalidTime || 'Invalid time format (use HH:MM)' };
  }

  if (!validateDuration(event.duration)) {
    return { valid: false, error: translations?.errorInvalidDuration || 'Duration must be at least 5 minutes and a multiple of 5' };
  }

  if (checkEventOverlap(event, existingEvents, excludeEventId)) {
    return { valid: false, error: translations?.errorEventOverlap || 'Event overlaps with an existing event' };
  }

  if (unavailableSlots && checkUnavailableSlotConflict(event, unavailableSlots)) {
    return { valid: false, error: translations?.errorSlotUnavailable || 'Time slot is unavailable (external booking)' };
  }

  return { valid: true };
};
