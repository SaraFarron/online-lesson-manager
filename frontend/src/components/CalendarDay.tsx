/**
 * CalendarDay component - represents a single day cell in the calendar
 * Displays events and handles click interactions for creating new events
 */

import React from 'react';
import { CalendarEvent } from '../types';
import { format, isSameMonth, isToday } from 'date-fns';
import { Draggable, Droppable } from '@hello-pangea/dnd';

interface CalendarDayProps {
  date: Date;
  currentMonth: Date;
  events: CalendarEvent[];
  onDayClick: (date: string) => void;
  onEventClick: (event: CalendarEvent) => void;
}

// Format duration for display
const formatDuration = (minutes: number): string => {
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (hours === 0) {
    return `${mins}m`;
  } else if (mins === 0) {
    return `${hours}h`;
  } else {
    return `${hours}h ${mins}m`;
  }
};

// Calculate total occupied time in minutes
const getTotalOccupiedTime = (events: CalendarEvent[]): number => {
  return events.reduce((total, event) => total + event.duration, 0);
};

export const CalendarDay: React.FC<CalendarDayProps> = ({
  date,
  currentMonth,
  events,
  onDayClick,
  onEventClick,
}) => {
  const dateString = format(date, 'yyyy-MM-dd');
  const dayNumber = format(date, 'd');
  const isCurrentMonth = isSameMonth(date, currentMonth);
  const isTodayDate = isToday(date);

  // Check if date is in the past
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const isPastDate = date < today;

  // Calculate availability based on occupied time (24 hours = 1440 minutes)
  const totalOccupiedMinutes = getTotalOccupiedTime(events);
  const hasAvailableSlots = totalOccupiedMinutes < 1440;
  const isFullyBooked = totalOccupiedMinutes >= 1440;

  const handleDayClick = (e: React.MouseEvent) => {
    // Only trigger if clicking the day background, not an event
    // Don't allow clicking on past dates
    if (e.target === e.currentTarget && !isPastDate) {
      onDayClick(dateString);
    }
  };

  const handleEventClickInternal = (event: CalendarEvent) => {
    // Don't allow clicking on past events
    if (!isPastDate) {
      onEventClick(event);
    }
  };

  return (
    <Droppable droppableId={dateString}>
      {(provided, snapshot) => (
        <div
          ref={provided.innerRef}
          {...provided.droppableProps}
          onClick={handleDayClick}
          className={`
            min-h-[80px] sm:min-h-[120px] border border-gray-200 p-1 sm:p-2 transition-colors
            ${!isCurrentMonth ? 'bg-gray-50 text-gray-400' : ''}
            ${isPastDate && isCurrentMonth ? 'bg-red-100 cursor-not-allowed' : 'cursor-pointer'}
            ${isTodayDate && !isFullyBooked && !isPastDate ? 'bg-blue-50' : ''}
            ${isFullyBooked && isCurrentMonth && !isPastDate ? 'bg-red-100' : ''}
            ${hasAvailableSlots && isCurrentMonth && !isTodayDate && !isFullyBooked && !isPastDate ? 'bg-green-50' : ''}
            ${snapshot.isDraggingOver && !isPastDate ? 'bg-blue-100 border-blue-400' : ''}
            ${!isPastDate ? 'hover:bg-gray-100' : ''}
          `}
        >
          {/* Day Number */}
          <div className={`
            text-xs sm:text-sm font-semibold mb-1
            ${isTodayDate ? 'text-blue-600' : ''}
            ${isPastDate ? 'text-gray-500' : ''}
          `}>
            {dayNumber}
          </div>

          {/* Events List */}
          <div className="space-y-0.5 sm:space-y-1">
            {events.map((event, index) => (
              <Draggable key={event.id} draggableId={event.id} index={index} isDragDisabled={isPastDate}>
                {(provided, snapshot) => (
                  <div
                    ref={provided.innerRef}
                    {...provided.draggableProps}
                    {...provided.dragHandleProps}
                    onClick={(e) => {
                      e.stopPropagation();
                      handleEventClickInternal(event);
                    }}
                    className={`
                      text-[10px] sm:text-xs p-0.5 sm:p-1 rounded truncate
                      ${isPastDate ? 'bg-gray-400 text-white cursor-not-allowed' : 'bg-blue-500 text-white hover:bg-blue-600 cursor-pointer'}
                      ${snapshot.isDragging ? 'opacity-50 shadow-lg' : ''}
                    `}
                    title={`${event.title} - ${event.startTime} (${formatDuration(event.duration)})`}
                  >
                    <div className="font-medium truncate">{event.title}</div>
                    <div className="text-[9px] sm:text-xs opacity-90">
                      {event.startTime} ({formatDuration(event.duration)})
                    </div>
                  </div>
                )}
              </Draggable>
            ))}
            {provided.placeholder}
          </div>
        </div>
      )}
    </Droppable>
  );
};
