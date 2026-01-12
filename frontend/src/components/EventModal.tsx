/**
 * Modal component for creating and editing calendar events
 * Handles all input validation and user feedback
 */

import React, { useState, useEffect } from 'react';
import Modal from 'react-modal';
import { CalendarEvent, UnavailableSlot, Translations } from '../types';
import { validateEvent } from '../utils/validation';

// Set app element for accessibility
Modal.setAppElement('#root');

interface EventModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (eventData: Omit<CalendarEvent, 'id'>) => void;
  onDelete?: (deleteAll?: boolean) => void;
  initialDate?: string;
  initialTime?: string;
  existingEvent?: CalendarEvent;
  allEvents: CalendarEvent[];
  unavailableSlots?: UnavailableSlot[];
  isWeeklyLesson?: boolean;
  translations: Translations;
}

export const EventModal: React.FC<EventModalProps> = ({
  isOpen,
  onClose,
  onSave,
  onDelete,
  initialDate,
  initialTime,
  existingEvent,
  allEvents,
  unavailableSlots,
  isWeeklyLesson = false,
  translations,
}) => {
  const [title, setTitle] = useState('');
  const [date, setDate] = useState('');
  const [startTime, setStartTime] = useState('09:00');
  const [isRecurring, setIsRecurring] = useState(false);
  const [error, setError] = useState<string>('');

  // Initialize form with existing event or defaults
  useEffect(() => {
    if (existingEvent) {
      setTitle(existingEvent.title);
      setDate(existingEvent.date);
      setStartTime(existingEvent.startTime);
      setIsRecurring(existingEvent.isRecurring);
    } else if (initialDate) {
      setDate(initialDate);
      setTitle(translations.lesson); // Default to "Lesson" translation
      setStartTime(initialTime || '09:00');
      setIsRecurring(isWeeklyLesson);
    }
    setError('');
  }, [existingEvent, initialDate, initialTime, isOpen, isWeeklyLesson, translations]);

  const handleSave = () => {
    const eventData: Omit<CalendarEvent, 'id'> = {
      title: title.trim() || translations.lesson, // Use "Lesson" translation if empty
      date,
      startTime,
      duration: 60, // Fixed 1 hour duration
      isRecurring,
      recurringGroupId: existingEvent?.recurringGroupId,
    };

    const validation = validateEvent(
      eventData,
      allEvents,
      existingEvent?.id,
      unavailableSlots,
      translations
    );

    if (!validation.valid) {
      setError(validation.error || 'Invalid event data');
      return;
    }

    onSave(eventData);
    onClose();
  };

  const handleDelete = (deleteAll?: boolean) => {
    if (onDelete) {
      onDelete(deleteAll);
      onClose();
    }
  };

  // Generate time options in 5-minute increments
  const timeOptions = [];
  for (let h = 0; h < 24; h++) {
    for (let m = 0; m < 60; m += 5) {
      const hour = h.toString().padStart(2, '0');
      const minute = m.toString().padStart(2, '0');
      timeOptions.push(`${hour}:${minute}`);
    }
  }

  return (
    <Modal
      isOpen={isOpen}
      onRequestClose={onClose}
      className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 bg-white rounded-lg shadow-xl p-4 sm:p-6 w-[calc(100%-2rem)] sm:w-full max-w-md max-h-[90vh] overflow-y-auto"
      overlayClassName="fixed inset-0 bg-black/30 backdrop-blur-sm z-50"
    >
      <h2 className="text-xl sm:text-2xl font-bold mb-4">
        {existingEvent ? translations.editEvent : translations.createEvent}
      </h2>

      {error && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-3 sm:px-4 py-2 sm:py-3 rounded mb-4 text-sm">
          {error}
        </div>
      )}

      <div className="space-y-3 sm:space-y-4">
        {/* Date */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            {translations.date}
          </label>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="w-full px-3 py-2 text-base border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>

        {/* Start Time */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            {translations.startTime}
          </label>
          <select
            value={startTime}
            onChange={(e) => setStartTime(e.target.value)}
            className="w-full px-3 py-2 text-base border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {timeOptions.map((time) => (
              <option key={time} value={time}>
                {time}
              </option>
            ))}
          </select>
        </div>

        {/* Weekly Repeat */}
        <div className="flex items-center">
          <input
            type="checkbox"
            id="recurring"
            checked={isRecurring}
            onChange={(e) => setIsRecurring(e.target.checked)}
            disabled={!!existingEvent}
            className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
          />
          <label htmlFor="recurring" className="ml-2 block text-sm text-gray-700">
            {translations.repeatWeekly}
            {existingEvent && translations.repeatWeeklyNote}
          </label>
        </div>
      </div>

      {/* Action Buttons */}
      <div className="mt-6 flex flex-col sm:flex-row sm:justify-end gap-3 sm:gap-3">
        {existingEvent && onDelete && existingEvent.recurringGroupId && (
          <>
            <button
              onClick={() => {
                handleDelete(false);
              }}
              className="w-full sm:w-auto px-4 py-3 bg-red-600 text-white text-sm rounded-md hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 order-3 sm:order-1"
            >
              {translations.deleteThis}
            </button>
            <button
              onClick={() => {
                handleDelete(true);
              }}
              className="w-full sm:w-auto px-4 py-3 bg-red-700 text-white text-sm rounded-md hover:bg-red-800 focus:outline-none focus:ring-2 focus:ring-red-600 order-4 sm:order-2"
            >
              {translations.deleteAllWeekly}
            </button>
          </>
        )}
        {existingEvent && onDelete && !existingEvent.recurringGroupId && (
          <button
            onClick={() => {
              handleDelete(false);
            }}
            className="w-full sm:w-auto px-4 py-3 bg-red-600 text-white text-sm rounded-md hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 order-3 sm:order-1"
          >
            {translations.delete}
          </button>
        )}
        <button
          onClick={onClose}
          className="w-full sm:w-auto px-4 py-3 bg-gray-200 text-gray-700 text-sm rounded-md hover:bg-gray-300 focus:outline-none focus:ring-2 focus:ring-gray-500 order-1 sm:order-2"
        >
          {translations.cancel}
        </button>
        <button
          onClick={handleSave}
          className="w-full sm:w-auto px-4 py-3 bg-blue-600 text-white text-sm rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 order-2 sm:order-3"
        >
          {existingEvent ? translations.update : translations.create}
        </button>
      </div>
    </Modal>
  );
};
